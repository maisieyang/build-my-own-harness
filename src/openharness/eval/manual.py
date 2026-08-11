"""Fail-closed environment contract for manually launched evals."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, cast

from dotenv import dotenv_values

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.eval.cassette import CassetteMode


def resolve_manual_cassette_mode() -> CassetteMode:
    """Require an explicit paid or replay mode; never default to live."""
    configured = os.environ.get("OPENHARNESS_EVAL_MODE")
    if configured is None or not configured.strip():
        raise SystemExit(
            "OPENHARNESS_EVAL_MODE is required; choose live, record, or replay explicitly"
        )
    raw = configured.lower().strip()
    if raw not in ("live", "record", "replay"):
        raise SystemExit(
            f"Invalid OPENHARNESS_EVAL_MODE={raw!r}; expected one of live / record / replay"
        )
    return cast("CassetteMode", raw)


def resolve_manual_case_id() -> str | None:
    """Read the optional CLI-selected case identifier."""
    raw = os.environ.get("OPENHARNESS_EVAL_CASE")
    if raw is None or not raw.strip():
        return None
    return raw.strip()


def resolve_manual_model(project_root: Path) -> str:
    """Resolve the cassette model identity exactly like project settings.

    The CLI's ``--model`` option is forwarded through ``OPENHARNESS_MODEL``,
    so the process environment takes precedence over the checkout's ``.env``.
    A missing model fails closed instead of silently selecting a historical
    reference cassette.
    """
    configured = os.environ.get("OPENHARNESS_MODEL")
    if configured is None:
        configured = dotenv_values(project_root / ".env").get("OPENHARNESS_MODEL")
    model = configured.strip() if isinstance(configured, str) else ""
    if not model:
        raise SystemExit(
            "OPENHARNESS_MODEL is required; configure it in the project .env or pass --model"
        )
    return model
