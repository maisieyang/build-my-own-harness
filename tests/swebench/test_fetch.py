"""T6 (D40.4) — dataset fetch via HF datasets-server, stdlib-only.

The HTTP seam is injected; pagination logic and JSONL shape are what's
under test. The real-network path is exercised once in the T7 smoke.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import pytest

from openharness.swebench.errors import FetchError
from openharness.swebench.fetch import fetch_dataset
from openharness.swebench.model import load_instances

if TYPE_CHECKING:
    from pathlib import Path


def make_row(n: int) -> dict[str, object]:
    return {
        "instance_id": f"acme__widget-{n}",
        "repo": "acme/widget",
        "base_commit": "a" * 40,
        "problem_statement": f"bug {n}",
    }


def paged_http_get(total: int) -> tuple[list[int], object]:
    """Fake datasets-server: ``total`` rows served in requested page sizes."""
    offsets: list[int] = []

    def http_get(url: str) -> str:
        query = parse_qs(urlparse(url).query)
        offset = int(query["offset"][0])
        length = int(query["length"][0])
        offsets.append(offset)
        rows = [
            {"row_idx": i, "row": make_row(i)} for i in range(offset, min(offset + length, total))
        ]
        return json.dumps({"rows": rows, "num_rows_total": total})

    return offsets, http_get


class TestFetchDataset:
    def test_paginates_and_writes_all_rows(self, tmp_path: Path) -> None:
        offsets, http_get = paged_http_get(total=150)
        dest = tmp_path / "dataset" / "lite.jsonl"

        count = fetch_dataset(dest, http_get=http_get, page_size=100)

        assert count == 150
        assert offsets == [0, 100]
        instances = load_instances(dest)
        assert len(instances) == 150
        assert instances[0].instance_id == "acme__widget-0"
        assert instances[-1].instance_id == "acme__widget-149"

    def test_dataset_and_split_land_in_url(self, tmp_path: Path) -> None:
        urls: list[str] = []

        def http_get(url: str) -> str:
            urls.append(url)
            return json.dumps({"rows": [{"row_idx": 0, "row": make_row(0)}], "num_rows_total": 1})

        fetch_dataset(
            tmp_path / "lite.jsonl",
            dataset="princeton-nlp/SWE-bench_Lite",
            split="test",
            http_get=http_get,
        )

        query = parse_qs(urlparse(urls[0]).query)
        assert query["dataset"] == ["princeton-nlp/SWE-bench_Lite"]
        assert query["split"] == ["test"]

    def test_malformed_response_raises_fetch_error(self, tmp_path: Path) -> None:
        with pytest.raises(FetchError, match="datasets-server"):
            fetch_dataset(tmp_path / "lite.jsonl", http_get=lambda url: "<html>503</html>")

    def test_partial_failure_leaves_no_dest_file(self, tmp_path: Path) -> None:
        """Atomicity: a fetch that dies mid-pagination must not leave a
        half-written dataset that a later ``run`` would silently trust."""
        calls: list[int] = []

        def flaky(url: str) -> str:
            calls.append(1)
            if len(calls) == 2:
                raise OSError("connection reset")
            rows = [{"row_idx": i, "row": make_row(i)} for i in range(100)]
            return json.dumps({"rows": rows, "num_rows_total": 200})

        dest = tmp_path / "lite.jsonl"
        with pytest.raises(FetchError):
            fetch_dataset(dest, http_get=flaky, page_size=100)

        assert not dest.exists()
