"""``HookResult`` — what a hook returns to the dispatch loop.

Per P3-T4 Three-Axis F:**single class, additive fields, classmethods for
ergonomics, no runtime validation of field/decision consistency**.

The 5 events share this one type;each event honors only the relevant
fields (the executor in 4d enforces the per-event semantics — this module
ships only the dataclass).

Per-event field semantics (informal contract, validated by tests +
``test_executor.py``):

::

    Event          Honors decision   Modify uses
    ─────────────  ────────────────  ────────────────────────────────────
    PreToolUse     allow/deny/modify new_input(dict re-validated after)
    PostToolUse    allow/modify      new_output, new_metadata, new_is_error
                   (deny → soft:    Note:deny on Post is auto-converted
                    auto modify-as- to modify(new_is_error=True,
                    error)           new_output=reason) by the executor
    PreApiCall     allow/deny/modify new_request(ApiMessageRequest)
    PostApiCall    allow/(deny→soft) Phase 3:no modify supported
    OnError        (ignored)         Observe only;异常永远 propagate

Field-decision contradictions (e.g., ``decision="allow"`` with
``new_input`` set) are **not** validated at construction time — the
classmethods encode the convention and the executor only reads the
relevant fields per decision. Power users who construct directly get
silent ignore of irrelevant fields, not a runtime error.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class HookResult:
    """A hook's decision + (for modify) the new state.

    The 5 ``new_*`` fields all default to ``None``;each event honors only
    the relevant subset. See module docstring for the per-event table.

    Construction:prefer the classmethods (:meth:`allow` / :meth:`deny` /
    :meth:`modify_input` / :meth:`modify_request` / :meth:`modify_output`)
    over direct dataclass construction — they encode per-event
    correctness conventions.
    """

    decision: Literal["deny", "modify", "allow"]
    message: str | None = None

    # PreToolUse modify:replace the tool's input dict.
    new_input: dict[str, Any] | None = None

    # PreApiCall modify:replace the entire ApiMessageRequest.
    # Typed ``object`` to avoid circular import on ``openharness.protocols``;
    # docstring + Pydantic re-validation at the call site enforce shape.
    # Phase 5 may tighten to ``ApiMessageRequest | None``.
    new_request: object | None = None

    # PostToolUse modify:replace the ToolResult that the LLM sees.
    new_output: str | None = None
    new_metadata: dict[str, Any] | None = None
    new_is_error: bool | None = None

    # ── Classmethods (ergonomic constructors) ────────────────────────── #

    @classmethod
    def allow(cls) -> HookResult:
        """Explicit pass-through.

        Equivalent in semantics to returning ``None`` from the hook;use
        when you want to be explicit that you considered the event and
        chose not to act.
        """
        return cls(decision="allow")

    @classmethod
    def deny(cls, message: str) -> HookResult:
        """Reject the call (Pre-event) or mark result as error (Post-event,
        auto-converted to ``modify(new_is_error=True, new_output=message)``).

        ``message`` should be LLM-readable — it's fed back as the
        ``ToolResult.output`` so the agent can adapt (errors are messages
        principle, D8.5).
        """
        return cls(decision="deny", message=message)

    @classmethod
    def modify_input(cls, new_input: dict[str, Any]) -> HookResult:
        """PreToolUse only:replace the tool's input before validate+execute.

        ``new_input`` is the raw dict (not a typed Pydantic instance);
        dispatch will re-run ``tool.input_model.model_validate`` on it
        before calling ``execute``.
        """
        return cls(decision="modify", new_input=new_input)

    @classmethod
    def modify_request(cls, new_request: object) -> HookResult:
        """PreApiCall only:replace the ApiMessageRequest before sending to LLM.

        Caller should pass an ``ApiMessageRequest`` instance (typed as
        ``object`` here to avoid a circular import). Phase 5 may tighten.
        """
        return cls(decision="modify", new_request=new_request)

    @classmethod
    def modify_output(
        cls,
        new_output: str,
        new_is_error: bool | None = None,
        new_metadata: dict[str, Any] | None = None,
    ) -> HookResult:
        """PostToolUse only:replace the ToolResult fields the LLM will see.

        Common uses:

        - Secret redaction:``modify_output("[REDACTED]", ...)``
        - Prompt-injection annotation:``modify_output("[WARNING ...] " + original)``
        - Soft post-execution deny:``modify_output(reason, new_is_error=True)``

        Only the provided fields are accumulated by the executor;
        unspecified ones inherit from the prior result.
        """
        return cls(
            decision="modify",
            new_output=new_output,
            new_is_error=new_is_error,
            new_metadata=new_metadata,
        )
