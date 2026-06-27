"""loop-runtime L2 — 声明式权限规则引擎(对齐 Claude Code 的 ``permissions``).

在 L1 fail-closed 基线上,给 :class:`~openharness.permissions.tier_based.TierBasedPermissionChecker`
织进一层声明式规则.形态 = **规则引擎**(不是加档),precedence **deny > ask > allow**.

规则语法 ``ToolName(specifier)``(plan T0 缝2):

- **file 工具**(``Edit/Write/Read``):specifier 是 glob,走 cwd-relative 匹配
  (复用 Tier2 的 :func:`~openharness.permissions.tier_based._matches_tier2` 语义).
- **``Bash(prefix:*)``**:specifier 是命令前缀(尾 ``:*`` 表前缀);无 ``:*`` 则精确匹配.
  ⚠ Bash allow 规则是**便利不是安全墙**——CC 自己警告前缀可被 ``;``/``&&``/子shell 绕;
  真正护栏仍是 checker 步1 的 Bash 灾难 deny-list + Tier1 红线.
- **裸 ``ToolName``**(无括号)/ ``ToolName()``:规范化为 specifier ``*``(任意调用).

``acceptEdits`` 收编为规则预设(:func:`accept_edits_preset`),不是新枚举值.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel

from openharness.permissions.checker import DecisionResult
from openharness.permissions.tier_based import _extract_path_arg, _matches_tier2

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel as _PydanticModel


class PermissionRules(BaseModel):
    """声明式权限规则三列表(对齐 CC ``permissions: {allow, deny, ask}``).

    嵌套进 :class:`~openharness.config.Settings` 的 ``permissions`` 块,env 走
    ``OPENHARNESS_PERMISSIONS__ALLOW`` 等(``env_nested_delimiter='__'``).
    与 ``permission_mode``(posture) / ``deny_paths``(Tier2 legacy) 并存——三者关注点不同.
    """

    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    ask: tuple[str, ...] = ()


@dataclass(frozen=True)
class PermissionRule:
    """一条解析后的规则:``tool`` + 规范化 ``specifier``(裸名/空括号 → ``*``)."""

    tool: str
    specifier: str


def parse_rule(rule: str) -> PermissionRule:
    """把 ``ToolName(specifier)`` 串解析成 :class:`PermissionRule`.

    非法串 **明确 raise ValueError**——绝不静默吞成"匹配全部/匹配为空"
    (对照 autopilot ``_looks_available`` 静默筛教训).
    """
    s = rule.strip()
    if not s:
        raise ValueError("empty permission rule")

    if s.endswith(")"):
        open_idx = s.find("(")
        if open_idx == -1:
            raise ValueError(f"permission rule missing '(': {rule!r}")
        tool = s[:open_idx].strip()
        specifier = s[open_idx + 1 : -1].strip()
        if not tool:
            raise ValueError(f"permission rule missing tool name: {rule!r}")
        return PermissionRule(tool=tool, specifier=specifier or "*")

    # No trailing ')': must be a bare tool name (no '(' allowed → unbalanced).
    if "(" in s:
        raise ValueError(f"permission rule has unbalanced '(': {rule!r}")
    return PermissionRule(tool=s, specifier="*")


def rule_matches(
    rule: PermissionRule,
    tool_name: str,
    args: _PydanticModel,
    cwd: Path,
) -> bool:
    """True iff ``rule`` matches this ``(tool_name, args)`` call.

    - tool name must match exactly.
    - specifier ``*`` → matches any invocation of that tool.
    - Bash-style args (has ``command``): ``prefix:*`` → ``startswith``;
      otherwise exact command match.
    - file-style args (has ``path``): cwd-relative glob via Tier2 semantics.
    """
    if rule.tool != tool_name:
        return False
    if rule.specifier == "*":
        return True

    command = getattr(args, "command", None)
    if isinstance(command, str):
        spec = rule.specifier
        if spec.endswith(":*"):
            return command.startswith(spec[:-2])
        return command == spec

    path = _extract_path_arg(args)
    if path is not None:
        return _matches_tier2(path, (rule.specifier,), cwd) is not None
    return False


def match_rules(
    tool_name: str,
    args: _PydanticModel,
    cwd: Path,
    *,
    allow: tuple[str, ...],
    deny: tuple[str, ...],
    ask: tuple[str, ...],
) -> DecisionResult | None:
    """按 precedence **deny > ask > allow** 求规则裁决.

    无任何规则命中返回 ``None``(调用方 checker 交回 Tier 链 / fallthrough).
    """
    for spec in deny:
        if rule_matches(parse_rule(spec), tool_name, args, cwd):
            return DecisionResult.deny(f"matches deny rule {spec!r}")
    for spec in ask:
        if rule_matches(parse_rule(spec), tool_name, args, cwd):
            return DecisionResult.ask(f"matches ask rule {spec!r}")
    for spec in allow:
        if rule_matches(parse_rule(spec), tool_name, args, cwd):
            return DecisionResult.allow()
    return None


def accept_edits_preset() -> tuple[str, ...]:
    """``acceptEdits`` 规则预设:放行文件编辑,**不含 Bash**(命令仍 fail-closed 拦).

    收编为规则(``Edit(*)`` / ``Write(*)``)而非新 PermissionMode 枚举值(守 plan 立场1).
    """
    return ("Edit(*)", "Write(*)")
