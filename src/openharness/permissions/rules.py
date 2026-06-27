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
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import BaseModel, field_validator
from pydantic_settings import NoDecode

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

    The ``_parse_and_validate`` before-validator does two jobs (review-fix [5]+[6]):

    1. **env comma-split**: ``OPENHARNESS_PERMISSIONS__ALLOW='Edit(*),Write(src/**)'``
       → ``("Edit(*)", "Write(src/**)")`` — mirrors ``Settings.deny_paths`` so the
       documented env form works instead of crashing on JSON decode.
    2. **fail-fast validation**: every rule string is run through :func:`parse_rule`
       at config-load time, so a malformed rule raises ``ValidationError`` here
       rather than an uncaught ``ValueError`` from the per-call hot path mid-loop.
    """

    # ``NoDecode`` stops pydantic-settings from JSON-decoding the env value
    # before ``_parse_and_validate`` runs (same reason Settings.deny_paths uses
    # it) — so ``OPENHARNESS_PERMISSIONS__ALLOW='Edit(*)'`` reaches the
    # comma-split validator instead of crashing on a JSON parse error.
    allow: Annotated[tuple[str, ...], NoDecode] = ()
    deny: Annotated[tuple[str, ...], NoDecode] = ()
    ask: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("allow", "deny", "ask", mode="before")
    @classmethod
    def _parse_and_validate(cls, value: Any) -> Any:
        # env comma-split (review-fix [5]) — same shape as Settings._parse_deny_paths.
        if isinstance(value, str):
            items: tuple[str, ...] = tuple(p.strip() for p in value.split(",") if p.strip())
        elif value is None:
            items = ()
        else:
            items = tuple(value)
        # fail-fast at load (review-fix [6]): reject malformed rules now, not mid-loop.
        for spec in items:
            parse_rule(spec)
        return items


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
    *,
    substring_bash: bool = False,
) -> bool:
    """True iff ``rule`` matches this ``(tool_name, args)`` call.

    - tool name must match exactly.
    - **Bash-style args** (has ``command``): ``*`` → any command.
      ``substring_bash=True`` (deny rules, review-fix [3]) → the token (specifier
      minus trailing ``:*``) must appear **anywhere** in the command — over-match
      is the safe direction for a deny boundary, catching ``; curl`` /
      ``bash -c '...'``. Otherwise (allow/ask) → narrow ``prefix:*`` startswith /
      exact match (under-match is the safe direction for allow).
    - **file-style args** (has ``path``): matched via Tier2 semantics, so ``*``
      and relative globs are **cwd-scoped** (review-fix [2]) and only explicit
      absolute/tilde specifiers reach outside cwd.
    """
    if rule.tool != tool_name:
        return False

    command = getattr(args, "command", None)
    if isinstance(command, str):
        spec = rule.specifier
        if spec == "*":
            return True
        token = spec[:-2] if spec.endswith(":*") else spec
        if substring_bash:
            return token in command
        return command.startswith(token) if spec.endswith(":*") else command == spec

    path = _extract_path_arg(args)
    if path is not None:
        # Routing through _matches_tier2 (not a blanket ``*`` early-return) is
        # what makes wildcard/relative allows cwd-scoped — ``*`` is a relative
        # pattern, so it only matches paths under cwd; ``/abs/**`` / ``~/x/**``
        # match directly and can escape cwd.
        return _matches_tier2(path, (rule.specifier,), cwd) is not None

    # Pathless, command-less tool: only ``*`` (any invocation) matches.
    return rule.specifier == "*"


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
        # deny matches Bash by substring (review-fix [3]) — a security boundary
        # over-matches on the safe side.
        if rule_matches(parse_rule(spec), tool_name, args, cwd, substring_bash=True):
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
