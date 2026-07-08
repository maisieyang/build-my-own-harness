"""Dataset acquisition via the HF datasets-server rows API (D40.4).

stdlib ``urllib`` only — no ``datasets``/``huggingface_hub`` dependency
for 300 rows of JSON. Fetch is the only networked step; ``run`` reads the
local JSONL exclusively, so a benchmark batch is deterministic and
offline-replayable. The write is atomic (tmp + rename): a fetch that dies
mid-pagination leaves no half dataset for a later ``run`` to trust.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from openharness.swebench.errors import FetchError

if TYPE_CHECKING:
    from pathlib import Path

HttpGet = Callable[[str], str]

_DATASETS_SERVER = "https://datasets-server.huggingface.co/rows"
_DEFAULT_DATASET = "princeton-nlp/SWE-bench_Lite"
_HTTP_TIMEOUT_S = 60.0


def _default_http_get(url: str) -> str:
    with urllib.request.urlopen(url, timeout=_HTTP_TIMEOUT_S) as response:
        return str(response.read().decode("utf-8"))


def _page_url(dataset: str, config: str, split: str, offset: int, length: int) -> str:
    query = urllib.parse.urlencode(
        {"dataset": dataset, "config": config, "split": split, "offset": offset, "length": length}
    )
    return f"{_DATASETS_SERVER}?{query}"


def fetch_dataset(
    dest: Path,
    *,
    dataset: str = _DEFAULT_DATASET,
    config: str = "default",
    split: str = "test",
    page_size: int = 100,
    http_get: HttpGet = _default_http_get,
) -> int:
    """Page through the datasets-server rows API and write ``dest`` JSONL.

    Returns the number of instances written. Any transport or shape
    failure raises :class:`FetchError` and leaves ``dest`` untouched.
    """
    rows: list[dict[str, Any]] = []
    total: int | None = None
    while total is None or len(rows) < total:
        url = _page_url(dataset, config, split, offset=len(rows), length=page_size)
        try:
            body = http_get(url)
            payload = json.loads(body)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise FetchError(url, str(exc)) from exc
        page_rows = payload.get("rows") if isinstance(payload, dict) else None
        num_total = payload.get("num_rows_total") if isinstance(payload, dict) else None
        if not isinstance(page_rows, list) or not isinstance(num_total, int):
            raise FetchError(url, "response missing rows / num_rows_total")
        total = num_total
        for entry in page_rows:
            row = entry.get("row") if isinstance(entry, dict) else None
            if not isinstance(row, dict):
                raise FetchError(url, "row entry missing 'row' object")
            rows.append(row)
        if not page_rows and len(rows) < total:
            raise FetchError(url, f"empty page before reaching {total} rows")

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    tmp.replace(dest)
    return len(rows)
