"""Extract durable memories from a completed turn — P11-T4.4d/4e.

Per ``decisions/26-phase-11-boundary.md`` D29.5: at the end of each
user turn (after :func:`update_session_memory_file` writes the
deterministic checkpoint), run a focused **secondary LLM pass** that
proposes 0-3 durable memories from the turn's content. Approved
records are written into the Phase 10 memory store via
:meth:`FilesystemMemoryStore.add_or_update`.

This is the agent's **write path** — Phase 10 was strictly read-only.
The closed loop:

1. ``extract_memories_from_turn`` → 0-3 new ``.md`` files in the
   memory dir
2. Next turn's :func:`select_relevant_memories` discovers them
3. Picked memories → injected into system prompt + ``mark_memory_used``
   increments ``use_count`` atomically
4. Phase 14+ (future): ``find_stale_memory_candidates`` GCs the ones
   that never get picked (Phase 13 chose snapshot rotation over memory
   GC; ``auto-dream subprocess`` + ``oh memory add`` are now Phase 14+
   candidates per ``tasks/phase-13-plan.md`` §"Risks specifically NOT
   mitigated")

Why a separate LLM pass (vs a tool in the main loop): cleaner failure
isolation (extraction errors don't poison the turn), focused prompt
(specialized guidance about what's worth saving), and natural rate
limiting (one pass per turn, max 3 records per pass).

Skip conditions (D29.5) — early-return without LLM call:

1. ``len(messages) < 2`` — no actual turn happened
2. ``has_memory_writes_since(messages, memory_dir)`` returns True —
   main conversation already wrote to memory via ``write_file`` /
   ``edit_file``; extracting would duplicate
3. ``not settings.enabled`` — caller opted out

Tools disabled on the secondary pass (D29.5 sub-decision: no in-tool
sandbox in Phase 11). Future Phase 12+ may add read-only tool
allowlist when real demand surfaces.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha1
from typing import TYPE_CHECKING, Any

from openharness.memory.model import (
    Memory,
    MemoryScope,
    MemoryType,
    compute_memory_signature,
)
from openharness.memory.team import check_team_memory_secrets
from openharness.observability.logging import get_logger
from openharness.protocols.content import (
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.services.summarize import summarize

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api.client import SupportsStreamingMessages
    from openharness.memory.store import FilesystemMemoryStore
    from openharness.protocols.messages import ConversationMessage

_logger = get_logger("memory.extract")


# ---------------------------------------------------------------------------
# Extraction system prompt (Claude-Code-style guidance) — D29.5
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM_PROMPT = """\
You are extracting durable memories from a completed conversation turn,
so that future conversations in this project can reuse what was learned.

Save ONLY stable, future-useful facts that are NOT derivable from
current files, git history, or project documentation. Examples of
good memories:

- "Project uses Stripe SDK 8.x with the legacy refund API"
- "Tests run via `uv run pytest -q`, not `python -m pytest`"
- "User prefers terse responses with no trailing summaries"
- "Refunds must include metadata.original_charge_id for audit trail"

Examples of bad memories (do NOT save):

- Step-by-step debugging traces (derivable from git log + code)
- API surface details already in docstrings (derivable from files)
- One-off questions ("how do I quit vim") — not stable
- Secrets (API keys, passwords, tokens) — security risk

Output JSON only. Schema:

{
  "memories": [
    {
      "type": "user" | "feedback" | "project" | "reference",
      "scope": "private" | "team",
      "name": "kebab-case-identifier",
      "description": "one-line summary",
      "body": "free-form markdown explanation"
    }
  ]
}

Use "team" scope when the fact applies to the whole team (project
conventions, shared tools); "private" otherwise (personal
preferences, individual workflow notes).

Return AT MOST 3 records. If nothing is worth saving, return
{"memories": []}.

Output ONLY the JSON object — no analysis, no explanation, no markdown
fences. Just the raw JSON."""


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of an :func:`extract_memories_from_turn` call.

    - ``written``: tuple of :class:`Memory` instances actually
      persisted to disk via ``add_or_update``
    - ``skipped``: True when an early-return skip fired
      (no LLM call made)
    - ``reason``: human-readable explanation when ``skipped=True``
    - ``error``: error string when the extraction failed at any phase
      (LLM call, JSON parse, write); None on success or skip
    """

    written: tuple[Memory, ...] = field(default_factory=tuple)
    skipped: bool = False
    reason: str | None = None
    error: str | None = None

    @classmethod
    def skip(cls, reason: str) -> ExtractionResult:
        return cls(skipped=True, reason=reason)


# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------


def has_memory_writes_since(
    messages: list[ConversationMessage],
    memory_dir: Path,
) -> bool:
    """Detect whether the main conversation already wrote into the
    memory directory via filesystem tools.

    Scans for ``write_file`` / ``edit_file`` :class:`ToolUseBlock`
    occurrences whose ``input["path"]`` starts with ``memory_dir``.
    Used as a skip condition for ``extract_memories_from_turn`` so
    extraction doesn't duplicate what the agent already explicitly
    wrote.

    Returns ``True`` if any such tool_use is found in the messages
    list.
    """
    memory_dir_str = str(memory_dir.resolve())
    for msg in messages:
        for block in msg.content:
            if not isinstance(block, ToolUseBlock):
                continue
            if block.name not in ("write_file", "edit_file"):
                continue
            path = block.input.get("path")
            if not isinstance(path, str):
                continue
            # Compare resolved paths so symlinks + relative paths
            # don't slip past the check
            try:
                from pathlib import Path as _Path

                resolved = str(_Path(path).resolve())
            except OSError:
                continue
            if resolved.startswith(memory_dir_str):
                return True
    return False


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


async def extract_memories_from_turn(
    *,
    cwd: Path,
    api_client: SupportsStreamingMessages,
    model: str,
    messages: list[ConversationMessage],
    memory_store: FilesystemMemoryStore,
    enabled: bool = True,
    max_records: int = 3,
    timeout_seconds: float = 30.0,
) -> ExtractionResult:
    """Run the secondary LLM pass on a completed turn.

    Returns an :class:`ExtractionResult` describing what was written,
    skipped, or failed. **Never raises** — extraction failures are
    contained so they can't poison the turn's success.
    """
    if not enabled:
        return ExtractionResult.skip("extraction disabled")

    if len(messages) < 2:
        return ExtractionResult.skip("not enough messages")

    memory_dir = memory_store._memory_project_dir
    if has_memory_writes_since(messages, memory_dir):
        return ExtractionResult.skip("main conversation already wrote memory")

    # --- Build user prompt: serialize the turn's content ---
    turn_text = _render_messages_for_extraction(messages)

    # --- Call summarize() with extraction system prompt ---
    try:
        raw = await summarize(
            messages=[
                _build_user_message(turn_text),
            ],
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            model=model,
            api_client=api_client,
            max_tokens=2048,
            timeout_seconds=timeout_seconds,
            tools_disabled=True,
        )
    except Exception as exc:
        _logger.warning(
            "memory_extract_failed",
            phase="llm_call",
            cwd=str(cwd),
            error=str(exc),
        )
        return ExtractionResult(error=str(exc))

    # --- Parse JSON response ---
    try:
        parsed = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        _logger.warning(
            "memory_extract_failed",
            phase="json_parse",
            cwd=str(cwd),
            error=str(exc),
        )
        return ExtractionResult(error=f"json parse: {exc}")

    if not isinstance(parsed, dict):
        _logger.warning(
            "memory_extract_failed",
            phase="json_schema",
            cwd=str(cwd),
            error="root not a dict",
        )
        return ExtractionResult(error="json schema: root not a dict")

    records = parsed.get("memories")
    if not isinstance(records, list):
        return ExtractionResult(error="json schema: memories not a list")

    # --- Cap at max_records, validate, write ---
    written_memories: list[Memory] = []
    for record in records[:max_records]:
        memory = _build_memory_from_record(record, cwd)
        if memory is None:
            continue
        # Team-scope: scan for secrets (silently drop on hit per D29.10)
        if memory.scope is MemoryScope.TEAM:
            secret_hit = check_team_memory_secrets(memory.body)
            if secret_hit is not None:
                _logger.warning(
                    "memory_team_secret_blocked",
                    name=memory.name,
                    reason=secret_hit,
                )
                continue
        try:
            memory_store.add_or_update(memory)
            written_memories.append(memory)
        except OSError as exc:
            _logger.warning(
                "memory_extract_failed",
                phase="write",
                name=memory.name,
                error=str(exc),
            )
            # Continue — other records still get a chance

    return ExtractionResult(written=tuple(written_memories))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _render_messages_for_extraction(messages: list[ConversationMessage]) -> str:
    """Serialize the turn's messages into a single text prompt for the
    extraction LLM. One-liner per message, similar to session_memory's
    Recent Conversation slot."""
    parts: list[str] = ["Recent conversation turn:", ""]
    for msg in messages:
        if not msg.content:
            continue
        block = msg.content[0]
        if isinstance(block, TextBlock):
            parts.append(f"[{msg.role}] {block.text}")
        elif isinstance(block, ToolUseBlock):
            parts.append(f"[{msg.role}/tool] {block.name}({block.input})")
        elif isinstance(block, ToolResultBlock):
            parts.append(f"[{msg.role}/tool_result] {block.content[:500]}")
    return "\n\n".join(parts)


def _build_user_message(text: str) -> ConversationMessage:
    """Wrap a plain string into a ConversationMessage for summarize()."""
    from openharness.protocols.messages import ConversationMessage as _CM

    return _CM(role="user", content=[TextBlock(text=text)])


def _build_memory_from_record(record: Any, cwd: Path) -> Memory | None:
    """Validate a single JSON record and build a :class:`Memory`.

    Returns ``None`` on any validation failure (and logs warning).
    Survives one bad record — extraction continues for the rest.
    """
    if not isinstance(record, dict):
        _logger.warning("memory_extract_invalid_record", reason="not a dict")
        return None
    try:
        memory_type = MemoryType(record.get("type", "project"))
    except ValueError:
        _logger.warning(
            "memory_extract_invalid_record",
            reason=f"invalid type {record.get('type')!r}",
        )
        return None
    try:
        scope = MemoryScope(record.get("scope", "private"))
    except ValueError:
        _logger.warning(
            "memory_extract_invalid_record",
            reason=f"invalid scope {record.get('scope')!r}",
        )
        return None
    name = record.get("name")
    description = record.get("description")
    body = record.get("body")
    if not isinstance(name, str) or not name:
        _logger.warning("memory_extract_invalid_record", reason="missing name")
        return None
    if not isinstance(description, str) or not description.strip():
        _logger.warning("memory_extract_invalid_record", reason="missing description")
        return None
    if not isinstance(body, str) or not body.strip():
        _logger.warning("memory_extract_invalid_record", reason="missing body")
        return None

    signature = compute_memory_signature(body, memory_type, scope)
    now = datetime.now(timezone.utc)
    # Synthesize a stable-enough id from signature; uniqueness within
    # the store comes from signature dedup anyway.
    memory_id = f"01H{sha1(signature.encode()).hexdigest()[:11].upper()}"

    try:
        return Memory(
            id=memory_id,
            name=name,
            description=description.strip(),
            type=memory_type,
            scope=scope,
            created_at=now,
            updated_at=now,
            body=body.strip(),
            signature=signature,
            source_path=cwd / "placeholder.md",  # overridden by add_or_update
        )
    except ValueError as exc:
        # __post_init__ validation (name regex etc.) failed
        _logger.warning(
            "memory_extract_invalid_record",
            reason=f"dataclass validation: {exc}",
        )
        return None
