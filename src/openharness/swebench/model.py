"""SWE-bench Lite instance model + JSONL loader (D40 T1).

Column names follow the real dataset wire shape: upper-case
``FAIL_TO_PASS`` / ``PASS_TO_PASS`` and gold ``patch`` / ``test_patch``
map onto snake_case fields via aliases.

Firewall (D40.3 红线): the answer-leaking fields — ``gold_patch`` /
``test_patch`` / ``fail_to_pass`` / ``pass_to_pass`` / ``hints_text`` —
are kept (post-hoc failure-taxonomy analysis needs them) but carry
``repr=False`` so no log line, assertion message, or exception path can
print the answer by accident. Keeping them OUT of the prompt is
``prompt.build_prompt``'s contract, tested by sentinel.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openharness.swebench.errors import DatasetNotFoundError, MalformedInstanceError

if TYPE_CHECKING:
    from pathlib import Path


class SWEBenchInstance(BaseModel):
    """One SWE-bench Lite task instance.

    ``extra="ignore"`` keeps the loader tolerant to upstream column
    additions — the dataset schema is theirs, not ours.
    """

    model_config = ConfigDict(frozen=True, extra="ignore", populate_by_name=True)

    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    version: str | None = None
    environment_setup_commit: str | None = None
    created_at: str | None = None

    # -- firewalled fields (D40.3): never in repr, never in prompts --
    gold_patch: str | None = Field(default=None, alias="patch", repr=False)
    test_patch: str | None = Field(default=None, repr=False)
    fail_to_pass: str | None = Field(default=None, alias="FAIL_TO_PASS", repr=False)
    pass_to_pass: str | None = Field(default=None, alias="PASS_TO_PASS", repr=False)
    hints_text: str | None = Field(default=None, repr=False)

    def __str__(self) -> str:
        # pydantic's __str__ renders field values ignoring repr=False;
        # route it through __repr__ so the firewall holds on both paths.
        return self.__repr__()


def load_instances(
    path: Path,
    *,
    limit: int | None = None,
    instance_ids: list[str] | None = None,
) -> list[SWEBenchInstance]:
    """Load instances from a local dataset JSONL (written by ``fetch``).

    ``instance_ids`` filters to the given ids (result ordered by dataset
    order); ``limit`` truncates after filtering. Blank lines are skipped.
    """
    if not path.is_file():
        raise DatasetNotFoundError(str(path))

    wanted = set(instance_ids) if instance_ids is not None else None
    instances: list[SWEBenchInstance] = []
    with path.open(encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            if not line.strip():
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MalformedInstanceError(line_number, f"invalid JSON: {exc}") from exc
            try:
                instance = SWEBenchInstance.model_validate(raw)
            except ValidationError as exc:
                missing = ", ".join(
                    ".".join(str(loc) for loc in err["loc"]) for err in exc.errors()
                )
                raise MalformedInstanceError(line_number, f"invalid fields: {missing}") from exc
            if wanted is not None and instance.instance_id not in wanted:
                continue
            instances.append(instance)
            if limit is not None and len(instances) >= limit:
                break
    return instances
