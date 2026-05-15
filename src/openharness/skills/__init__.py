"""Skills subsystem — Phase 5c.

Curated expertise(markdown + YAML frontmatter)lazy-loaded into LLM
context via tool calling. Per ``decisions/12`` boundary:

- L1: markdown + YAML frontmatter shape
- L2: ``~/.openharness/skills/`` global + ``<project>/.openharness/skills/``
  project; project overrides global on same ``name``
- L3: catalog(``name`` + ``description`` only)injected into system prompt
  at bootstrap, always-on
- L4: skill body lazy-loaded via ``LoadSkill(name)`` tool —— body lands in
  ``messages[]`` as ``tool_result``, subject to Phase 4 compaction
- L5: ``LoadSkill.is_read_only = True``
- L6: required frontmatter:``name``(safe identifier)+ ``description``
- L7: no skill-level size limit — Phase 4 Layer 1 handles oversized bodies

Cross-cutting invariant: **Skill integration must NOT modify**
``permissions/checker.py``, ``hooks/executor.py``, ``engine/query.py``,
``observability/logging.py``. Skills are a tenant of the existing tool
dispatch pattern, not a new dispatch path — the **third tenant**
validation after MCP(Phase 5a)and Sandbox(Phase 6 preview).

Sub-units land incrementally(see ``tasks/phase-5c-skills-plan.md``):

- T1(this commit area):``Skill`` dataclass + frontmatter parse +
  ``FilesystemSkillStore``
- T2:``LoadSkillTool(BaseTool)`` + ``QueryContext.skill_store``
- T3:catalog injection into ``prompts.py`` + CLI bootstrap
- T4:end-to-end smoke + invariant verification
- T5:coverage + retro

Public API(so far):

    from openharness.skills import Skill, parse_skill, FilesystemSkillStore
"""

from __future__ import annotations

from openharness.skills.model import Skill, parse_skill
from openharness.skills.store import FilesystemSkillStore, SkillStore

__all__ = ["FilesystemSkillStore", "Skill", "SkillStore", "parse_skill"]
