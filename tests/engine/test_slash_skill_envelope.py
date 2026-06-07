"""Unit tests for ``engine.slash_skill.synthesize_skill_envelope`` — P18-T1.

Covers D38.2 / D38.3 acceptance:

- 2-message form when args is empty / whitespace-only
- 3-message form when args is non-empty
- ``synth_`` ID prefix on both ToolUseBlock.id and ToolResultBlock.tool_use_id
- Two calls mint different IDs (default uuid factory)
- ID factory injection works deterministically
- skill.body lands verbatim — markdown, multi-line, literal ``{args}``
  all pass through untouched (no template engine, no substitution)

Architecture isolation: the test file imports only protocols + Skill +
slash_skill helper. If a future maintainer adds an import of
``tools.load_skill`` / ``permissions`` / ``hooks`` / ``observability`` /
``cli`` into ``engine/slash_skill.py``, ``test_isolation_no_leaky_imports``
catches it.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from openharness.engine.slash_skill import (
    SYNTH_ID_PREFIX,
    synthesize_skill_envelope,
)
from openharness.protocols import (
    TextBlock,
    ToolUseBlock,
)
from openharness.protocols.content import ToolResultBlock
from openharness.skills.model import Skill


def _make_skill(name: str = "parse-credit-report", body: str = "skill body") -> Skill:
    return Skill(
        name=name,
        description="a description",
        body=body,
        version=None,
        source_path=Path("/tmp/fake.md"),
    )


class TestEnvelopeShape:
    def test_args_empty_yields_two_messages(self) -> None:
        skill = _make_skill()
        envelope = synthesize_skill_envelope(skill, args="")

        assert len(envelope) == 2
        assert envelope[0].role == "assistant"
        assert envelope[1].role == "user"

    def test_args_whitespace_only_yields_two_messages(self) -> None:
        # D38.3: args.strip() decides — pure whitespace counts as empty,
        # mirroring "user pressed enter without typing".
        skill = _make_skill()
        envelope = synthesize_skill_envelope(skill, args="   \n\t")
        assert len(envelope) == 2

    def test_args_nonempty_yields_three_messages(self) -> None:
        skill = _make_skill()
        envelope = synthesize_skill_envelope(skill, args="申请号12345")

        assert len(envelope) == 3
        assert envelope[2].role == "user"
        text = envelope[2].content[0]
        assert isinstance(text, TextBlock)
        assert text.text == "申请号12345"


class TestSchemaContract:
    """Lock D38.2 wire shape — block types, names, IDs."""

    def test_msg0_is_assistant_with_load_skill_tool_use(self) -> None:
        skill = _make_skill(name="my-skill")
        envelope = synthesize_skill_envelope(skill, args="")

        msg = envelope[0]
        assert msg.role == "assistant"
        assert len(msg.content) == 1
        tool_use = msg.content[0]
        assert isinstance(tool_use, ToolUseBlock)
        assert tool_use.name == "LoadSkill"
        assert tool_use.input == {"name": "my-skill"}

    def test_msg1_is_user_with_tool_result_carrying_body(self) -> None:
        skill = _make_skill(body="expert guidance text")
        envelope = synthesize_skill_envelope(skill, args="")

        msg = envelope[1]
        assert msg.role == "user"
        assert len(msg.content) == 1
        tool_result = msg.content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.content == "expert guidance text"
        assert tool_result.is_error is False

    def test_tool_use_id_matches_tool_result_id(self) -> None:
        skill = _make_skill()
        envelope = synthesize_skill_envelope(skill, args="")

        tool_use = envelope[0].content[0]
        tool_result = envelope[1].content[0]
        assert isinstance(tool_use, ToolUseBlock)
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_use.id == tool_result.tool_use_id

    def test_synth_id_prefix(self) -> None:
        skill = _make_skill()
        envelope = synthesize_skill_envelope(skill, args="")

        tool_use = envelope[0].content[0]
        assert isinstance(tool_use, ToolUseBlock)
        assert tool_use.id.startswith(SYNTH_ID_PREFIX)
        assert SYNTH_ID_PREFIX == "synth_"

    def test_two_calls_mint_different_ids_by_default(self) -> None:
        skill = _make_skill()
        env1 = synthesize_skill_envelope(skill, args="")
        env2 = synthesize_skill_envelope(skill, args="")

        id1 = env1[0].content[0].id  # type: ignore[union-attr]
        id2 = env2[0].content[0].id  # type: ignore[union-attr]
        assert id1 != id2


class TestIdFactoryInjection:
    def test_custom_factory_drives_id(self) -> None:
        skill = _make_skill()
        envelope = synthesize_skill_envelope(
            skill, args="", tool_use_id_factory=lambda: "synth_test"
        )

        tool_use = envelope[0].content[0]
        tool_result = envelope[1].content[0]
        assert isinstance(tool_use, ToolUseBlock)
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_use.id == "synth_test"
        assert tool_result.tool_use_id == "synth_test"


class TestBodyVerbatim:
    """D38.3: skill.body lands verbatim — no substitution, no truncation."""

    def test_multiline_body_preserved(self) -> None:
        body = "line 1\nline 2\n\nline 4 with **markdown**\n"
        skill = _make_skill(body=body)
        envelope = synthesize_skill_envelope(skill, args="ignored")

        tool_result = envelope[1].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.content == body

    def test_literal_args_placeholder_not_substituted(self) -> None:
        # If the user wrote "{args}" inside their SKILL.md body, it must
        # survive verbatim — M1 does not implement {args} substitution
        # (per D38.3). This guards against a future maintainer "helpfully"
        # adding format() and silently breaking long-form skill bodies.
        body = "Apply this skill to: {args}. Other text {name}."
        skill = _make_skill(body=body)
        envelope = synthesize_skill_envelope(skill, args="real value")

        tool_result = envelope[1].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.content == body
        assert "{args}" in tool_result.content
        # Plus the args lives in the trailing user msg, not inside the body.
        trailing = envelope[2].content[0]
        assert isinstance(trailing, TextBlock)
        assert trailing.text == "real value"

    def test_markdown_special_chars_preserved(self) -> None:
        body = "# Heading\n\n- bullet\n- `code` and **bold**\n\n```python\nx = 1\n```\n"
        skill = _make_skill(body=body)
        envelope = synthesize_skill_envelope(skill, args="")

        tool_result = envelope[1].content[0]
        assert isinstance(tool_result, ToolResultBlock)
        assert tool_result.content == body


class TestArchitectureIsolation:
    """Static check: the helper must NOT import the forbidden modules
    (per P18-T1 acceptance + D38 §六 wiring audit). If a future maintainer
    introduces a cross-layer import, this test fails loudly so the leak
    surfaces at code-review time rather than dogfood time.

    Phase 19 T1.0 proactive guard (per learnings/phase-18.md §3 high-
    confidence prediction): ``openharness.plugins`` joins the forbidden
    list **before** the Phase 19 CC plugin parser lands. Phase 19 is the
    first compounding test of D38.2's synth envelope substrate — the
    prediction was "M2 (CCPluginLoader) reuses synthesize_skill_envelope
    with zero diff." Adding the guard here makes that prediction
    physically enforced, not just observed.
    """

    FORBIDDEN_MODULE_PREFIXES = (
        "openharness.tools.load_skill",
        "openharness.permissions",
        "openharness.hooks",
        "openharness.observability",
        "openharness.cli",
        "openharness.plugins",
    )

    def _imports_in(self, path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_no_forbidden_imports(self) -> None:
        import openharness.engine.slash_skill as mod

        src_path = Path(mod.__file__)  # type: ignore[arg-type]
        imports = self._imports_in(src_path)

        leaks = [
            imp for imp in imports if any(imp.startswith(p) for p in self.FORBIDDEN_MODULE_PREFIXES)
        ]
        assert leaks == [], (
            "engine/slash_skill.py must remain isolated from "
            f"tool / permission / hook / observability / cli layers (leaks: {leaks})"
        )


@pytest.mark.parametrize("args", ["", "x", "  ", "multi word args"])
def test_envelope_messages_are_fresh_list(args: str) -> None:
    # Caller pattern is ``history.extend(envelope)``; the helper returning a
    # private internal list would let mutation propagate. Confirm it's a
    # fresh list — mutating doesn't affect a second call.
    skill = _make_skill()
    env1 = synthesize_skill_envelope(skill, args=args)
    env2 = synthesize_skill_envelope(skill, args=args)
    env1.append("poison")  # type: ignore[arg-type]
    expected_len = 3 if args.strip() else 2
    assert len(env2) == expected_len
