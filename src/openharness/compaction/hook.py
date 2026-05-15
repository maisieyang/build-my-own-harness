"""``TruncateToolResultHook`` — Layer 1 of Phase 4 compaction.

PostToolUse hook that wraps :func:`head_tail_truncate`:when a tool's
output exceeds ``cap_tokens``, replace the output (in place via
:meth:`HookResult.modify_output`) with a head-tail-truncated version
that carries a marker noting how many tokens were dropped.

Emits a ``tool_truncated`` log event (the 9th in the Phase 3 observability
inventory) so trace consumers can see *when* compaction fired:

::

    {"event": "tool_truncated", "tool_use_id": "toolu_01", "tool_name": "Read",
     "original_tokens": 35012, "truncated_tokens": 9876, "cap_tokens": 10000}

This is dogfood of the Phase 3 hook system — no engine changes, just
register the hook in :class:`QueryContext.hook_registry` and Layer 1
is on. Default registration lands in cli.py (P4-T4).

Per D14 deferred sub-decision §1:this hook should typically be
registered **first** in the PostToolUse chain so user hooks see the
truncated output (truncate-then-decorate). Callers can flip the order
by registering their hooks before this one if they need the full output
(e.g., a cost-tracking hook that wants total bytes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.compaction.tokenize import count_tokens
from openharness.compaction.truncate import head_tail_truncate
from openharness.hooks import HookResult, PostToolUseContext
from openharness.observability import get_logger

if TYPE_CHECKING:
    from openharness.hooks import HookContext


logger = get_logger("compaction")


class TruncateToolResultHook:
    """PostToolUse hook that truncates oversized ``tool_result`` outputs.

    Structurally satisfies the ``Hook`` callable contract (no inheritance
    required — same Protocol pattern as ``PermissionChecker``).

    Construction:

        hook = TruncateToolResultHook(cap_tokens=10_000, model="qwen-plus")
        registry.register("PostToolUse", hook)
    """

    def __init__(self, cap_tokens: int, model: str) -> None:
        """Args:

        cap_tokens: maximum tokens before truncation fires. Per D14.1
            default 10_000 (Codex recommendation). Passing ``cap_tokens
            <= 0`` disables truncation (every output passes through).
        model: model name for tokenization. Should match the model
            ``QueryContext.model`` is configured with so the token
            count reflects what the LLM actually consumes. The user
            constructs this hook with knowledge of the same model.
        """
        self._cap_tokens = cap_tokens
        self._model = model

    async def __call__(self, ctx: HookContext) -> HookResult | None:
        # Defensive isinstance — hook can technically be registered on
        # other events; only PostToolUse is meaningful here.
        if not isinstance(ctx, PostToolUseContext):
            return None
        if self._cap_tokens <= 0:
            return None

        output = ctx.result.output
        original_tokens = count_tokens(output, self._model)
        if original_tokens <= self._cap_tokens:
            return None  # under cap → passthrough

        truncated_output = head_tail_truncate(
            output,
            cap_tokens=self._cap_tokens,
            model=self._model,
        )
        truncated_tokens = count_tokens(truncated_output, self._model)

        # Emit the 9th log event in the Phase 3 inventory. ``info`` level
        # because truncation is expected on long-output tools (Read large
        # file / Bash with verbose stdout) — surfacing at WARNING would
        # spam the default level.
        logger.info(
            "tool_truncated",
            tool_use_id=ctx.tool_use_id,
            tool_name=ctx.tool_name,
            original_tokens=original_tokens,
            truncated_tokens=truncated_tokens,
            cap_tokens=self._cap_tokens,
        )

        return HookResult.modify_output(new_output=truncated_output)
