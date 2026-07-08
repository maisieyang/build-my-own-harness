"""T1 (D40 boundary) — SWEBenchInstance model + JSONL loader.

Fixture rows mirror the real SWE-bench Lite column names (upper-case
``FAIL_TO_PASS`` / ``PASS_TO_PASS``, gold ``patch`` / ``test_patch``) so the
alias mapping is exercised against the actual wire shape, not an idealized
one.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from openharness.swebench.errors import (
    DatasetNotFoundError,
    MalformedInstanceError,
)
from openharness.swebench.model import SWEBenchInstance, load_instances

GOLD_PATCH_SENTINEL = "GOLD-PATCH-SENTINEL-diff --git a/x.py"
TEST_PATCH_SENTINEL = "TEST-PATCH-SENTINEL-diff --git a/test_x.py"
FAIL_TO_PASS_SENTINEL = '["test_x.py::TEST-F2P-SENTINEL"]'
PASS_TO_PASS_SENTINEL = '["test_x.py::TEST-P2P-SENTINEL"]'
HINTS_SENTINEL = "HINTS-SENTINEL maybe try frobnicating the wozzle"


def make_row(instance_id: str = "astropy__astropy-12907", **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "instance_id": instance_id,
        "repo": "astropy/astropy",
        "base_commit": "d16bfe05a744909de4b27f5875fe0d4ed41ce607",
        "problem_statement": "Modeling's `separability_matrix` computes incorrectly.",
        "version": "4.3",
        "environment_setup_commit": "298ccb478e6bf092953bca67a3d29dc6c35f6752",
        "created_at": "2022-03-03T15:14:54Z",
        "patch": GOLD_PATCH_SENTINEL,
        "test_patch": TEST_PATCH_SENTINEL,
        "FAIL_TO_PASS": FAIL_TO_PASS_SENTINEL,
        "PASS_TO_PASS": PASS_TO_PASS_SENTINEL,
        "hints_text": HINTS_SENTINEL,
    }
    row.update(overrides)
    return row


def write_jsonl(path: Any, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


class TestLoadInstances:
    def test_returns_typed_instances_with_alias_mapping(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        write_jsonl(dataset, [make_row(), make_row("django__django-11001", repo="django/django")])

        instances = load_instances(dataset)

        assert len(instances) == 2
        first = instances[0]
        assert isinstance(first, SWEBenchInstance)
        assert first.instance_id == "astropy__astropy-12907"
        assert first.repo == "astropy/astropy"
        assert first.base_commit.startswith("d16bfe05")
        assert "separability_matrix" in first.problem_statement
        # upper-case dataset columns land on the snake_case firewalled fields
        assert first.fail_to_pass == FAIL_TO_PASS_SENTINEL
        assert first.pass_to_pass == PASS_TO_PASS_SENTINEL
        assert first.gold_patch == GOLD_PATCH_SENTINEL
        assert first.test_patch == TEST_PATCH_SENTINEL

    def test_limit(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        write_jsonl(dataset, [make_row(f"repo__repo-{i}") for i in range(5)])

        assert len(load_instances(dataset, limit=2)) == 2

    def test_instance_id_filter(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        write_jsonl(dataset, [make_row(f"repo__repo-{i}") for i in range(5)])

        got = load_instances(dataset, instance_ids=["repo__repo-3", "repo__repo-1"])

        assert [i.instance_id for i in got] == ["repo__repo-1", "repo__repo-3"]

    def test_missing_file_raises_dataset_not_found(self, tmp_path: Any) -> None:
        with pytest.raises(DatasetNotFoundError, match="fetch"):
            load_instances(tmp_path / "nope.jsonl")

    def test_malformed_json_line_reports_line_number(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        dataset.write_text(
            json.dumps(make_row()) + "\n{not json}\n",
            encoding="utf-8",
        )

        with pytest.raises(MalformedInstanceError, match="line 2"):
            load_instances(dataset)

    def test_missing_required_field_raises_malformed(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        row = make_row()
        del row["base_commit"]
        write_jsonl(dataset, [row])

        with pytest.raises(MalformedInstanceError, match="base_commit"):
            load_instances(dataset)

    def test_blank_lines_are_skipped(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        dataset.write_text(json.dumps(make_row()) + "\n\n", encoding="utf-8")

        assert len(load_instances(dataset)) == 1


class TestFirewalledRepr:
    """D40.3 — 泄题字段不进 repr/str, 防止日志/异常路径把答案打出来."""

    def test_gold_fields_absent_from_repr_and_str(self, tmp_path: Any) -> None:
        dataset = tmp_path / "lite.jsonl"
        write_jsonl(dataset, [make_row()])
        instance = load_instances(dataset)[0]

        for rendered in (repr(instance), str(instance)):
            assert GOLD_PATCH_SENTINEL not in rendered
            assert TEST_PATCH_SENTINEL not in rendered
            assert "TEST-F2P-SENTINEL" not in rendered
            assert "TEST-P2P-SENTINEL" not in rendered
            assert HINTS_SENTINEL not in rendered
