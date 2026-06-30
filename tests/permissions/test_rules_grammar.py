"""loop-runtime L2 · T2 — 规则语法 parser ``ToolName(specifier)`` (RED).

立场(plan T0 缝2):
- file 工具 ``Edit/Write/Read(glob)`` 的 specifier 是 glob.
- ``Bash(prefix:*)`` 的 specifier 是命令前缀(CC 语法,尾 ``:*`` 表前缀).
- 裸 ``ToolName``(无括号)规范化为 ``ToolName(*)``(任意调用).
- 非法串 **明确报错**,不静默吞(对照 autopilot ``_looks_available`` 静默筛教训).

这些测试现在应当 RED:``openharness.permissions.rules`` 模块尚不存在.
"""

from __future__ import annotations

import pytest

from openharness.permissions.rules import PermissionRule, parse_rule


class TestParseFileRule:
    def test_glob_specifier_preserved(self) -> None:
        rule = parse_rule("Edit(src/**)")
        assert rule == PermissionRule(tool="Edit", specifier="src/**")

    def test_write_with_extension_glob(self) -> None:
        rule = parse_rule("Write(*.py)")
        assert rule.tool == "Write"
        assert rule.specifier == "*.py"


class TestParseBashRule:
    def test_prefix_specifier_preserved_raw(self) -> None:
        # Parser keeps the raw specifier; the matcher interprets the ``:*``
        # prefix semantics (kept as one responsibility per layer).
        rule = parse_rule("Bash(npm run test:*)")
        assert rule.tool == "Bash"
        assert rule.specifier == "npm run test:*"


class TestBareToolName:
    def test_bare_name_normalizes_to_star(self) -> None:
        # No parens → matches any invocation of that tool.
        rule = parse_rule("Read")
        assert rule == PermissionRule(tool="Read", specifier="*")

    def test_empty_parens_normalizes_to_star(self) -> None:
        rule = parse_rule("Edit()")
        assert rule.specifier == "*"


class TestInvalidGrammarRaises:
    @pytest.mark.parametrize(
        "bad",
        [
            "",  # empty
            "()",  # no tool name
            "Edit(src/**",  # unbalanced paren
            "Edit src/**)",  # missing open paren
            "(src/**)",  # specifier without tool
            "Bash(:*)",  # round 4 [D]: empty command prefix → reject at load
        ],
    )
    def test_malformed_rule_raises_valueerror(self, bad: str) -> None:
        # Must fail loud, never silently treat a malformed rule as "matches
        # nothing" / "matches everything".
        with pytest.raises(ValueError):
            parse_rule(bad)
