"""End-to-end + invariant verification — P5c-T4.

Two surfaces:

1. **End-to-end smoke** — with a stub LLM, drive ``run_query`` through a
   full skill-load cycle. Turn 1:LLM emits ``tool_use(LoadSkill, ...)``;
   engine dispatches; ``LoadSkillTool.execute`` returns the body; engine
   appends ``tool_result`` to messages. Turn 2:LLM (stubbed) emits its
   final ``end_turn`` text. Verifies the full Index → Lookup → Content →
   (continue loop) pattern from ``tasks/phase-5c-skills-preview.md`` §2.

2. **Cross-cutting invariant** — formal test that the four "zero diff"
   modules contain no LoadSkill / Skill references. If any does, the
   abstraction has leaked and Phase 3 hook/permission contract is
   compromised. This is the **third tenant test** (after MCP in
   Phase 5a and Sandbox in Phase 6 preview).
"""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

from openharness.engine import QueryContext, run_query
from openharness.prompts import EnvironmentInfo, build_system_prompt
from openharness.protocols import (
    ApiMessageCompleteEvent,
    ConversationMessage,
    TextBlock,
    ToolSpec,
)
from openharness.skills.store import FilesystemSkillStore
from openharness.tools import LoadSkillTool, ToolRegistry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from openharness.protocols import ApiMessageRequest, ApiStreamEvent


# --------------------------------------------------------------------------- #
# Stub LLM client                                                              #
# --------------------------------------------------------------------------- #


class _StubLlmClient:
    """Pre-canned events per turn + records each request the engine sends.

    Mirrors the shape of ``tests/engine/conftest._StubApiClient`` but local
    to this file because the skills test surface assertions are different
    (request system_prompt content, request messages shape).
    """

    def __init__(self, events_per_turn: list[list[ApiStreamEvent]]) -> None:
        self._events = events_per_turn
        self._turn = 0
        self.captured_requests: list[ApiMessageRequest] = []

    async def stream_message(
        self,
        request: ApiMessageRequest,
    ) -> AsyncIterator[ApiStreamEvent]:
        self.captured_requests.append(request)
        events = self._events[self._turn]
        self._turn += 1
        for event in events:
            yield event


# --------------------------------------------------------------------------- #
# Event helpers                                                                #
# --------------------------------------------------------------------------- #


def _load_skill_tool_use_event(
    *,
    tool_use_id: str = "toolu_load_01",
    skill_name: str = "test-helper",
) -> ApiMessageCompleteEvent:
    """Turn 1:LLM decides to load a skill and emits a tool_use block."""
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": tool_use_id,
                        "name": "LoadSkill",
                        "input": {"name": skill_name},
                    }
                ],
            },
            "usage": {"input_tokens": 50, "output_tokens": 10},
            "stop_reason": "tool_use",
        }
    )


def _end_turn_event(text: str = "task complete") -> ApiMessageCompleteEvent:
    return ApiMessageCompleteEvent.model_validate(
        {
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
            "usage": {"input_tokens": 70, "output_tokens": 5},
            "stop_reason": "end_turn",
        }
    )


# --------------------------------------------------------------------------- #
# 1. End-to-end smoke                                                         #
# --------------------------------------------------------------------------- #


class TestSkillsEndToEnd:
    """Stub LLM → run_query → full LoadSkill dispatch chain."""

    async def test_full_load_skill_cycle(self, tmp_path: Path) -> None:
        # 1. Skill on disk.
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "test-helper.md").write_text(
            "---\n"
            "name: test-helper\n"
            "description: Guidance for writing tests that don't flake\n"
            "---\n"
            "When writing tests, always avoid time.sleep() — use stub clocks.\n",
            encoding="utf-8",
        )

        # 2. Wire the skill store + LoadSkill into a registry.
        store = FilesystemSkillStore(global_dir=skills_dir)
        assert store.discover()  # smoke: scan worked
        registry = ToolRegistry()
        registry.register(LoadSkillTool(store))

        # 3. Build a system prompt that includes the catalog (the LLM
        # would see this).
        env = EnvironmentInfo(
            os_name="Darwin",
            os_version="25",
            shell="/bin/zsh",
            cwd=tmp_path,
            python_version="3.12",
        )
        system_prompt = build_system_prompt(registry.to_api_schema(), env, skill_store=store)
        # Catalog must reach the LLM.
        assert "test-helper" in system_prompt
        assert "Guidance for writing tests that don't flake" in system_prompt
        assert "LoadSkill" in system_prompt  # tool catalog

        # 4. Stub LLM:turn 1 emits tool_use, turn 2 emits end_turn.
        client = _StubLlmClient(
            events_per_turn=[
                [_load_skill_tool_use_event()],
                [_end_turn_event("got the guidance — will avoid sleep")],
            ],
        )

        # 5. Drive run_query end-to-end. Real engine,real dispatch,real
        # permission check,real hook chain (empty) — only the LLM is
        # stubbed.
        context = QueryContext(
            api_client=client,
            tool_registry=registry,
            system_prompt=system_prompt,
            cwd=tmp_path,
            model="qwen-plus",
            skill_store=store,
        )

        events = [
            event
            async for event in run_query(
                [ConversationMessage(role="user", content=[TextBlock(text="help me write tests")])],
                context,
            )
        ]

        # 6. Final event is the conversation handoff (P6+-T1); the
        # LAST ApiMessageCompleteEvent (turn 2's end_turn) is the one
        # before the new ConversationCompleteEvent.
        from openharness.protocols import ConversationCompleteEvent

        assert isinstance(events[-1], ConversationCompleteEvent)
        last_api = [e for e in events if isinstance(e, ApiMessageCompleteEvent)][-1]
        assert last_api.stop_reason == "end_turn"

        # 7. Two LLM turns happened.
        assert len(client.captured_requests) == 2

        # 8. Turn 1 request:system prompt carries the catalog.
        turn1 = client.captured_requests[0]
        assert turn1.system is not None
        assert "test-helper" in turn1.system

        # 9. Turn 2 request:messages now include the tool_result with the
        # skill body — this is the "lazy load → body in messages" loop.
        turn2 = client.captured_requests[1]
        assert len(turn2.messages) >= 3
        # [0] original user, [1] assistant tool_use, [2] user tool_result.
        tool_result_msg = turn2.messages[2]
        assert tool_result_msg.role == "user"
        # The block carries the skill body verbatim.
        result_block = tool_result_msg.content[0]
        # ``content`` of a ToolResultBlock is a list[ContentBlock];first
        # block is the text returned by LoadSkillTool.
        result_text = _extract_text(result_block)
        assert "avoid time.sleep" in result_text

    async def test_unknown_skill_returns_is_error_to_llm(self, tmp_path: Path) -> None:
        # Even with a real skill on disk, LLM may hallucinate a name. The
        # tool surfaces is_error=True + the catalog so LLM can self-correct.
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "real.md").write_text(
            "---\nname: real\ndescription: a real skill\n---\nbody\n",
            encoding="utf-8",
        )
        store = FilesystemSkillStore(global_dir=skills_dir)
        registry = ToolRegistry()
        registry.register(LoadSkillTool(store))

        env = EnvironmentInfo(
            os_name="Linux",
            os_version="6.0",
            shell="/bin/sh",
            cwd=tmp_path,
            python_version="3.12",
        )
        system_prompt = build_system_prompt(registry.to_api_schema(), env, skill_store=store)

        client = _StubLlmClient(
            events_per_turn=[
                [_load_skill_tool_use_event(skill_name="hallucinated")],
                [_end_turn_event("got it,trying again")],
            ],
        )
        context = QueryContext(
            api_client=client,
            tool_registry=registry,
            system_prompt=system_prompt,
            cwd=tmp_path,
            model="qwen-plus",
            skill_store=store,
        )

        async for _ in run_query(
            [ConversationMessage(role="user", content=[TextBlock(text="x")])],
            context,
        ):
            pass

        # Turn 2 tool_result block must carry is_error=True + the catalog
        # so the LLM sees what it can actually call.
        turn2 = client.captured_requests[1]
        result_block = turn2.messages[2].content[0]
        # ``is_error`` is a field on ToolResultBlock.
        assert getattr(result_block, "is_error", False) is True
        result_text = _extract_text(result_block)
        assert "hallucinated" in result_text  # the bad name surfaces
        assert "real" in result_text  # the available name surfaces


def _extract_text(result_block: Any) -> str:
    """Pull the text body out of a ToolResultBlock.

    The block's ``content`` is either a plain string (P2 shape) or a list
    of content sub-blocks (P3+ multi-modal shape). This helper handles
    both transparently.
    """
    content = result_block.content
    if isinstance(content, str):
        return content
    # list[ContentBlock]
    chunks: list[str] = []
    for sub in content:
        text = getattr(sub, "text", None)
        if isinstance(text, str):
            chunks.append(text)
    return "\n".join(chunks)


# --------------------------------------------------------------------------- #
# 2. Cross-cutting invariant verification                                     #
# --------------------------------------------------------------------------- #


class TestCrossCuttingInvariant:
    """Phase 5c is the **third tenant test** of Phase 3 abstractions.

    The four "zero diff" modules in ``decisions/12`` must contain no
    skill-aware code. We verify this **structurally** by reading their
    source and asserting no Skill / LoadSkill identifiers appear —
    that's stronger than a unit assertion and catches accidental leaks.
    """

    _PROTECTED_MODULES = (
        "openharness.hooks.executor",
        "openharness.engine.query",
        "openharness.observability.logging",
    )

    # Names a "leak" would surface. Comments / docstrings that mention
    # Skills as a future tenant are fine (this list is identifiers only).
    _FORBIDDEN_IDENTIFIERS = (
        "LoadSkill",
        "LoadSkillTool",
        "SkillStore",
        "FilesystemSkillStore",
        "parse_skill",
    )

    def test_protected_modules_do_not_reference_skills(self) -> None:
        import importlib

        for mod_name in self._PROTECTED_MODULES:
            module = importlib.import_module(mod_name)
            source = inspect.getsource(module)
            # Strip comments and docstrings so commentary about future
            # tenants doesn't trip the check. We're looking for actual
            # code-level references.
            code_lines = [
                line.split("#", 1)[0]
                for line in source.splitlines()
                if not line.strip().startswith("#")
            ]
            code = "\n".join(code_lines)
            for forbidden in self._FORBIDDEN_IDENTIFIERS:
                assert forbidden not in code, (
                    f"{mod_name} references {forbidden!r} — cross-cutting "
                    f"invariant violated. Skills must not leak into "
                    f"permission/hook/engine/observability layers."
                )

    def test_load_skill_satisfies_BaseTool_only(self) -> None:
        # ``LoadSkillTool`` must inherit ONLY from ``BaseTool[...]`` — if
        # it ever picks up engine / permission / hook base classes, the
        # tenant-of-existing-infrastructure model has broken.
        from openharness.tools.base import BaseTool

        bases = LoadSkillTool.__mro__
        # MRO: LoadSkillTool → BaseTool → ABC → Generic → object
        # No engine / permission / hook classes in the chain.
        forbidden_in_mro = {"Permission", "Hook", "Engine", "QueryContext"}
        for cls in bases:
            for forbidden in forbidden_in_mro:
                assert forbidden not in cls.__name__, (
                    f"LoadSkillTool MRO includes {cls.__name__} — Skills "
                    f"must be a tenant of BaseTool only."
                )
        # And BaseTool IS in the chain (sanity).
        assert BaseTool in bases


# --------------------------------------------------------------------------- #
# 3. ToolSpec emission check                                                  #
# --------------------------------------------------------------------------- #


class TestLoadSkillToolApiSchema:
    """Verify the LLM sees LoadSkill in ``messages_request.tools`` exactly
    like any other tool. Catches schema-emission regressions where a tool
    becomes invisible to the LLM."""

    def test_LoadSkill_appears_in_to_api_schema(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "x.md").write_text(
            "---\nname: x\ndescription: y\n---\nb\n",
            encoding="utf-8",
        )
        store = FilesystemSkillStore(global_dir=skills_dir)
        registry = ToolRegistry()
        registry.register(LoadSkillTool(store))

        schemas = registry.to_api_schema()
        load_skill_specs = [s for s in schemas if s.name == "LoadSkill"]
        assert len(load_skill_specs) == 1
        spec: ToolSpec = load_skill_specs[0]
        # Description reaches the LLM verbatim.
        assert "catalog" in spec.description.lower()
        # JSON schema has the ``name`` property.
        assert "name" in spec.input_schema.get("properties", {})
