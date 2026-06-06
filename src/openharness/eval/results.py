"""Results persistence + version stamping — Stage 5 substrate.

Every spike run writes a JSONL file under ``evals/{service}/results/`` with:

- Line 1: ``type=run_header`` containing all version-stamped axes (D34.3)
- Lines 2+: ``type=case_result`` per case, lean (D34.4)

8 axes stamped per run (D34.3 + Stage 5.1 addendum for full artifacts):

1. model + judge_model — which LLM
2. cassette_mode — replay vs live
3. dataset_sha256 — dataset.yaml file hash
4. prompt_sha256 + prompt_text — FOCUS_STATE_SYSTEM_PROMPT hash AND full text
5. rubric_sha256s + rubric_texts — per-capability rubric hashes AND full texts
6. scorer_classes — class names registered for this run
7. git_commit + git_dirty — repo state ("当时跑的是哪个 commit + 是否有未提交改动")
8. completed_at - started_at — wall-clock duration

Stage 5.1 distinguishes identity vs content: hash is fingerprint, identity OK,
but 6 months later old prompt is overwritten by git commits and content is
no longer recoverable from hash alone. Full-text artifacts must be stored
self-contained in result (not relying on git archaeology).

The runner does NOT write — spike (consumer) orchestrates via
:func:`write_run_results`. Substrate stays unchanged (D34.6 + D31.10.1).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.eval.memory_decision import MemoryDecisionCaseResult
    from openharness.eval.protocol import Score
    from openharness.eval.runner import CaseResult


@dataclass(frozen=True)
class RunMetadata:
    """Full version stamping for one eval run (D34.3 + Stage 5.1 artifacts).

    Hash fields (sha256) give cheap identity comparison; text/artifact
    fields give self-contained content recovery 6 months later when git
    history may have moved on. Git fields stamp repo state at run time.
    """

    started_at: str
    completed_at: str
    model: str
    judge_model: str
    cassette_mode: str
    dataset_path: str
    dataset_sha256: str
    prompt_sha256: str
    prompt_text: str
    """Full text of FOCUS_STATE_SYSTEM_PROMPT at run time. Self-contained
    artifact — readable directly from result JSONL without git checkout."""
    rubric_sha256s: dict[str, str]
    rubric_texts: dict[str, str]
    """Full text of each capability's LLM-judge rubric at run time.
    Same self-contained recovery rationale as prompt_text."""
    scorer_classes: list[str]
    n_cases: int
    git_commit: str | None
    """Git HEAD commit hash at run time, or None if not in a git repo."""
    git_dirty: bool
    """True if working tree has uncommitted changes — flags that the
    code state at run time differs from git_commit (prompt/rubric text
    above are still authoritative; git_commit is only for source-level
    reference)."""
    extra: dict[str, Any] = field(default_factory=dict)
    """Reserved for consumer-specific metadata (e.g., ``base_url``,
    ``max_tokens_override``). Stored under ``"extra"`` key in JSON header."""


# ---------------------------------------------------------------------------
# Hash helpers
# ---------------------------------------------------------------------------


def compute_text_hash(text: str) -> str:
    """SHA-256 hex digest of UTF-8 encoded text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_file_hash(path: Path) -> str:
    """SHA-256 hex digest of file contents."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compute_rubric_hashes(rubrics: dict[str, str]) -> dict[str, str]:
    """Per-capability rubric hashes. Empty dict if no rubrics provided.

    Stable order: returned dict preserves rubrics' insertion order (Python
    3.7+ dict semantics), which lines up with the canonical capability ID
    order in :data:`openharness.eval.rubrics.CAPABILITY_RUBRICS`.
    """
    return {cap_id: compute_text_hash(text) for cap_id, text in rubrics.items()}


def get_git_info(project_root: Path) -> tuple[str | None, bool]:
    """Return ``(commit_hash, is_dirty)`` for the project root's git repo.

    Returns ``(None, False)`` if not a git repo or git is unavailable.
    ``is_dirty`` is True when working tree has any uncommitted changes
    (porcelain output non-empty) — flag for "run captured state may
    differ from the named commit".

    Side effect: shells out to ``git``. Bounded by 5s timeout to avoid
    blocking on hung git ops.
    """
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        if commit.returncode != 0:
            return None, False
        commit_hash = commit.stdout.strip()

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        is_dirty = bool(status.stdout.strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None, False
    return commit_hash, is_dirty


# ---------------------------------------------------------------------------
# Filename + ISO timestamp
# ---------------------------------------------------------------------------


def build_result_filename(metadata: RunMetadata) -> str:
    """Filename schema (D34.1): ``{timestamp}_{model}_{mode}.jsonl``.

    Timestamp from metadata.started_at, with ``:`` replaced by ``-`` for
    filesystem safety. Model name kept verbatim (no slashes expected for
    OpenAI-compat models).
    """
    safe_ts = metadata.started_at.replace(":", "-").replace("+00-00", "Z")
    # Cut sub-second precision to keep filename readable
    if "." in safe_ts:
        safe_ts = safe_ts.split(".")[0] + "Z"
    safe_model = metadata.model.replace("/", "_")
    return f"{safe_ts}_{safe_model}_{metadata.cassette_mode}.jsonl"


# ---------------------------------------------------------------------------
# Score serialization
# ---------------------------------------------------------------------------


def _score_to_dict(score: Score) -> dict[str, Any]:
    """Serialize Score for JSONL line. Drops case_id (in outer record)."""
    return {
        "dim": score.dim,
        "value": score.value,
        "reason": score.reason,
    }


def _case_result_to_dict(result: CaseResult) -> dict[str, Any]:
    """Serialize CaseResult to lean per-case JSONL line (D34.4).

    Drops input messages (recoverable from dataset_sha256 + dataset.yaml).
    """
    return {
        "type": "case_result",
        "case_id": result.sample.case_id,
        "capability": result.sample.capability,
        "output": {
            "goal": result.output.goal,
            "next_step": result.output.next_step,
        },
        "scores": [_score_to_dict(s) for s in result.scores],
    }


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


def write_run_results(
    output_path: Path,
    metadata: RunMetadata,
    results: list[CaseResult],
) -> None:
    """Write one JSONL file with header + case records (D34.3 + D34.4).

    Parent dir created lazily. File written atomically via tempfile + rename
    is NOT used here — single dev project, no concurrency. If user runs 2
    spikes simultaneously they get different timestamps and thus different
    filenames, no conflict.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = {"type": "run_header", **asdict(metadata)}
    lines = [json.dumps(header, ensure_ascii=False)]
    for result in results:
        lines.append(json.dumps(_case_result_to_dict(result), ensure_ascii=False))
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _memory_decision_case_result_to_dict(
    result: MemoryDecisionCaseResult,
) -> dict[str, Any]:
    """Serialize MemoryDecisionCaseResult to lean per-case JSONL line.

    Parallel to :func:`_case_result_to_dict` but tailored for the
    memory_decision consumer's output shape (multi-turn tool_use
    sequence + final text + turn count). Drops the system-prompt
    fixture details — those are recoverable from ``dataset_sha256``
    + the dataset.yaml.
    """
    return {
        "type": "case_result",
        "case_id": result.sample.case_id,
        "capability": result.sample.capability,
        "shape": result.sample.shape,
        "output": {
            "turn_count": result.output.turn_count,
            "tool_uses": [
                {"name": t.name, "input": dict(t.input)} for t in result.output.tool_uses
            ],
            "text": result.output.text,
        },
        "scores": [_score_to_dict(s) for s in result.scores],
    }


def write_memory_decision_results(
    output_path: Path,
    metadata: RunMetadata,
    results: list[MemoryDecisionCaseResult],
) -> None:
    """Write memory_decision JSONL — parallel to :func:`write_run_results`.

    See :func:`_memory_decision_case_result_to_dict` for the per-case
    schema. Header shape is the same ``RunMetadata`` D34.3 stamp.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    header = {"type": "run_header", **asdict(metadata)}
    lines = [json.dumps(header, ensure_ascii=False)]
    for result in results:
        lines.append(json.dumps(_memory_decision_case_result_to_dict(result), ensure_ascii=False))
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Convenience: timestamp generator
# ---------------------------------------------------------------------------


def utc_iso_now() -> str:
    """ISO 8601 UTC timestamp. Compatible with build_result_filename."""
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat()
