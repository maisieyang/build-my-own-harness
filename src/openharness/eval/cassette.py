"""Cassette mode — Stage 4 substrate.

Record / replay / live three-mode cassette for the 2 LLM call boundaries
in the eval pipeline (D33.1):

1. **infer cassette** — captures ``infer_focus_state`` output
   (FocusState as JSON ``{goal, next_step}``). Used by runner.
2. **judge cassette** — captures the raw judge LLM response (str). Used
   by :class:`CapabilityLLMJudgeScorer`.

File layout (D33.3): ``{root}/{model}/{kind}/{case_id}.json`` where
``kind`` is ``"infer"`` or ``"judge_<capability>"``.

Modes (D33.2):

- ``"live"``   — bypass cassette entirely (default; old behavior preserved)
- ``"record"`` — call LLM, save response to cassette (overwrites existing)
- ``"replay"`` — load from cassette only, never call LLM; missing cassette
  raises :class:`CassetteMissingError` (D33.2 — never silently fall back)

Cassette key is ``(case_id, model, kind)`` only — NOT including prompt
hash (D33.5). User workflow for prompt change: edit prompt → run record →
run replay.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from openharness.protocols.content import TextBlock
from openharness.protocols.messages import ConversationMessage
from openharness.services.focus_state import FocusState, infer_focus_state
from openharness.services.summarize import summarize

if TYPE_CHECKING:
    from openharness.api import SupportsStreamingMessages


CassetteMode = Literal["live", "record", "replay"]


class CassetteMissingError(FileNotFoundError):
    """Raised in replay mode when a required cassette file is absent.

    Carries a structured message pointing the user to run record mode
    first. Subclasses FileNotFoundError so standard error handling works
    but the type is distinct for explicit catching.
    """


@dataclass(frozen=True)
class CassetteKey:
    """Identifier for one cassetted LLM call.

    Three parts:

    - ``case_id``: e.g. ``"T1-tool-name-abstraction"``
    - ``model``:   e.g. ``"deepseek-v4-flash"``
    - ``kind``:    e.g. ``"infer"`` or ``"judge_T7"``
    """

    case_id: str
    model: str
    kind: str

    @property
    def relative_path(self) -> Path:
        """File path relative to cassette root: ``{model}/{kind}/{case_id}.json``."""
        safe_model = self.model.replace("/", "_")
        safe_kind = self.kind.replace("/", "_")
        return Path(safe_model) / safe_kind / f"{self.case_id}.json"


class CassetteStore:
    """File-backed cassette store.

    Each cassette is a JSON file with schema (D33.3):

    .. code-block:: json

        {
          "case_id": "...",
          "model": "...",
          "kind": "...",
          "recorded_at": "ISO timestamp",
          "request_summary": "first 200 chars of input, for human inspection",
          "response": "<str for judge, dict for infer>"
        }
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: CassetteKey) -> Path:
        return self._root / key.relative_path

    def has(self, key: CassetteKey) -> bool:
        return self._path(key).exists()

    def load(self, key: CassetteKey) -> dict[str, Any] | None:
        """Return the parsed JSON dict, or None if absent.

        Caller extracts the ``response`` field according to ``kind``.
        """
        path = self._path(key)
        if not path.exists():
            return None
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return data

    def save(
        self,
        key: CassetteKey,
        request_summary: str,
        response: Any,
    ) -> None:
        """Write cassette atomically (parent dirs created lazily)."""
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "case_id": key.case_id,
            "model": key.model,
            "kind": key.kind,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "request_summary": request_summary[:200],
            "response": response,
        }
        path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Cassetted helper 1 — infer_focus_state
# ---------------------------------------------------------------------------


async def cassetted_infer_focus_state(
    *,
    case_id: str,
    messages: list[ConversationMessage],
    api_client: SupportsStreamingMessages,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
) -> FocusState:
    """Cassetted wrapper around :func:`infer_focus_state`.

    Cassette key kind = ``"infer"``. Response stored as
    ``{"goal": ..., "next_step": ...}`` dict; reconstructed to FocusState
    on replay.
    """
    key = CassetteKey(case_id=case_id, model=model, kind="infer")
    request_summary = _summarize_messages_for_cassette(messages)

    if cassette_mode == "live" or cassette_store is None:
        return await infer_focus_state(
            messages=messages,
            api_client=api_client,
            model=model,
        )

    if cassette_mode == "replay":
        record = cassette_store.load(key)
        if record is None:
            raise CassetteMissingError(
                f"Cassette missing: case={case_id} kind=infer model={model}. "
                f"Run with OPENHARNESS_EVAL_MODE=record first."
            )
        resp = record["response"]
        return FocusState(
            goal=resp.get("goal"),
            next_step=resp.get("next_step"),
        )

    # cassette_mode == "record"
    result = await infer_focus_state(
        messages=messages,
        api_client=api_client,
        model=model,
    )
    cassette_store.save(
        key,
        request_summary,
        {"goal": result.goal, "next_step": result.next_step},
    )
    return result


# ---------------------------------------------------------------------------
# Cassetted helper 2 — judge LLM call (raw summarize)
# ---------------------------------------------------------------------------


async def cassetted_judge_call(
    *,
    case_id: str,
    capability: str,
    judge_payload: str,
    system_prompt: str,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int = 256,
    timeout_seconds: float = 15.0,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
) -> str:
    """Cassetted wrapper around :func:`summarize` for the judge LLM call.

    Cassette key kind = ``f"judge_{capability}"``. Response stored as raw
    str (the judge's JSON response is parsed by caller, not by cassette).

    Exceptions on the underlying ``summarize()`` propagate to caller in
    live/record modes (caller's existing ``CapabilityLLMJudgeScorer`` error
    handling turns them into Score with ``value="ERROR"``).
    """
    key = CassetteKey(case_id=case_id, model=model, kind=f"judge_{capability}")

    if cassette_mode == "live" or cassette_store is None:
        return await _call_judge_live(
            judge_payload, system_prompt, api_client, model, max_tokens, timeout_seconds
        )

    if cassette_mode == "replay":
        record = cassette_store.load(key)
        if record is None:
            raise CassetteMissingError(
                f"Cassette missing: case={case_id} kind=judge_{capability} "
                f"model={model}. Run with OPENHARNESS_EVAL_MODE=record first."
            )
        response = record["response"]
        if not isinstance(response, str):
            # Defensive: cassette schema says judge response is str.
            return str(response)
        return response

    # cassette_mode == "record"
    raw = await _call_judge_live(
        judge_payload, system_prompt, api_client, model, max_tokens, timeout_seconds
    )
    cassette_store.save(key, judge_payload[:200], raw)
    return raw


async def _call_judge_live(
    judge_payload: str,
    system_prompt: str,
    api_client: SupportsStreamingMessages,
    model: str,
    max_tokens: int,
    timeout_seconds: float,
) -> str:
    """The actual judge LLM call. Extracted so cassette helper can branch on mode."""
    return await summarize(
        messages=[
            ConversationMessage(
                role="user",
                content=[TextBlock(text=judge_payload)],
            )
        ],
        system_prompt=system_prompt,
        model=model,
        api_client=api_client,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        tools_disabled=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _summarize_messages_for_cassette(messages: list[ConversationMessage]) -> str:
    """Build a human-readable preview of the input messages for cassette
    file's ``request_summary`` field. Just first message's first block, since
    cassette key already disambiguates the call."""
    if not messages or not messages[0].content:
        return "(no input)"
    first = messages[0].content[0]
    if isinstance(first, TextBlock):
        return f"[{messages[0].role}] {first.text[:160]}"
    return f"[{messages[0].role}] ({type(first).__name__})"
