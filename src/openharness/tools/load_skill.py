"""``LoadSkillTool`` — P5c-T2.

The **single** tool that exposes the skills subsystem to the LLM. Per
``decisions/12`` L4:skill bodies are NOT pre-loaded into ``system_prompt``;
the LLM sees only the catalog(names + descriptions, injected by
``prompts.py`` in T3)and calls ``LoadSkill(name="...")`` when it decides
a skill is relevant. The body lands in ``messages[]`` as a ``tool_result``
block — standard tool dispatch path, no special handling.

Per L5:``is_read_only=True`` so the AuthZ Tier 3 lax path applies
automatically. The :class:`openharness.permissions.PermissionChecker`
sees this just like ``Read`` / ``Grep`` and routes accordingly — no
``isinstance(LoadSkillTool)`` branch anywhere, satisfying the
cross-cutting invariant.

Construction takes a :class:`SkillStore`(constructor injection — same
pattern as ``McpToolAdapter``)so tests inject in-memory stubs without
touching the filesystem.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from openharness.tools.base import BaseTool, ExecutionDomain, ToolResult

if TYPE_CHECKING:
    from openharness.skills.store import SkillStore
    from openharness.tools.base import ToolExecutionContext


class LoadSkillInput(BaseModel):
    """Single ``name`` field matched against the discovered catalog.

    Validation is intentionally **not** done at the Pydantic layer:an
    invalid / unknown name flows through to ``execute`` where it
    produces a uniform ``is_error=True`` ``ToolResult`` with the
    available catalog. Same LLM-facing experience whether the LLM
    hallucinates a name or misremembers an old one.
    """

    name: str = Field(
        description=(
            "Skill name from the 'Available Skills' section of the system prompt. Case-sensitive."
        ),
    )


class LoadSkillTool(BaseTool[LoadSkillInput]):
    """Load a named skill's body for expert guidance."""

    execution_domain = ExecutionDomain.TRUSTED_CONTROL
    name = "LoadSkill"
    description = (
        "Load the body of a named skill from the 'Available Skills' catalog "
        "in the system prompt. Returns the skill's full expert guidance text "
        "as the tool result. Use when the user's task matches a skill's "
        "description listed in the catalog."
    )
    input_model = LoadSkillInput
    is_read_only = True

    def __init__(self, store: SkillStore) -> None:
        self._store = store

    async def execute(
        self,
        args: LoadSkillInput,
        context: ToolExecutionContext,  # noqa: ARG002 — Skills are cwd-independent
    ) -> ToolResult:
        skill = self._store.get(args.name)
        if skill is None:
            # Surface the catalog so the LLM can self-correct on a typo or
            # hallucinated name — errors-as-payload(framing §4.2).
            available = sorted(self._store.discover().keys())
            catalog = ", ".join(available) if available else "(none)"
            return ToolResult(
                is_error=True,
                output=f"no skill named {args.name!r}; available skills: {catalog}",
            )
        return ToolResult(output=skill.body)
