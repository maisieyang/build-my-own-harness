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

import re
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

    Env parsing + load-time validation live in :meth:`_parse_and_validate`
    (the single canonical explanation); ``NoDecode`` lets that validator see the
    raw env string instead of a JSON-decoded value.
    """

    allow: Annotated[tuple[str, ...], NoDecode] = ()
    deny: Annotated[tuple[str, ...], NoDecode] = ()
    ask: Annotated[tuple[str, ...], NoDecode] = ()

    @field_validator("allow", "deny", "ask", mode="before")
    @classmethod
    def _parse_and_validate(cls, value: Any) -> Any:
        # env comma-split (review-fix [5]) — same shape as Settings._parse_deny_paths.
        items: tuple[Any, ...]
        if isinstance(value, str):
            items = tuple(p.strip() for p in value.split(",") if p.strip())
        elif value is None:
            items = ()
        else:
            # Accept any non-str iterable (list/tuple/set/generator); round 4 [E]
            # restores that flexibility while still failing cleanly on a scalar
            # (e.g. a bare int) with a ValidationError rather than a raw TypeError.
            try:
                items = tuple(value)
            except TypeError:
                raise ValueError(
                    f"permission rules must be a string or iterable of strings, "
                    f"got {type(value).__name__}"
                ) from None
        # fail-fast at load (review-fix [6] + [E]): reject malformed / non-str
        # rules now, not as an opaque crash on the per-call hot path mid-loop.
        for spec in items:
            if not isinstance(spec, str):
                raise ValueError(
                    f"permission rule must be a string, got {type(spec).__name__}: {spec!r}"
                )
            parse_rule(spec)
        return items


@dataclass(frozen=True)
class PermissionRule:
    """一条解析后的规则:``tool`` + 规范化 ``specifier``(裸名/空括号 → ``*``)."""

    tool: str
    specifier: str


def parse_rule(rule: str) -> PermissionRule:
    """把 ``ToolName(specifier)`` 串解析成 :class:`PermissionRule`.

    非法串 **明确 raise ValueError**——绝不静默吞成"匹配全部/匹配为空".
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
        # round 4 [D]: reject an empty command prefix (``Bash(:*)``) at load —
        # an empty token would otherwise match every / no command depending on
        # allow-vs-deny, a silent footgun. Fail-fast like every other malformed rule.
        if specifier.endswith(":*") and not specifier[:-2].strip():
            raise ValueError(f"permission rule has empty command prefix: {rule!r}")
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
    deny: bool = False,
) -> bool:
    """True iff ``rule`` matches this ``(tool_name, args)`` call.

    ``deny`` selects the allow-vs-deny **asymmetry**: a deny rule is a security
    boundary, so it over-matches on the safe side; an allow rule grants access,
    so it under-matches on the safe side.

    - **Bash-style args** (has ``command``) — see :func:`_bash_matches`:
      deny → command-token-boundary substring; allow/ask → narrow prefix/exact.
    - **file-style args** (has ``path``):
      *deny* ``*`` → any path (over-deny, review round 3 [A]); otherwise matched
      via Tier2 semantics, so *allow/ask* ``*`` and relative globs are
      **cwd-scoped** ([2]) and only explicit absolute/tilde specifiers escape cwd.
    """
    if rule.tool != tool_name:
        return False

    command = getattr(args, "command", None)
    if isinstance(command, str):
        return _bash_matches(rule.specifier, command, deny=deny)

    path = _extract_path_arg(args)
    if path is not None:
        if deny and rule.specifier == "*":
            return True  # [A] a wildcard deny blocks any path, in or outside cwd
        return _matches_tier2(path, (rule.specifier,), cwd) is not None

    # Pathless, command-less tool: only ``*`` (any invocation) matches.
    return rule.specifier == "*"


def _bash_matches(specifier: str, command: str, *, deny: bool) -> bool:
    """Match a Bash ``specifier`` against a ``command`` (allow/deny asymmetric)."""
    if specifier == "*":
        return True
    token = specifier[:-2] if specifier.endswith(":*") else specifier
    if not token:
        # review round 3 [D]: an empty-prefix ``Bash(:*)`` must match nothing,
        # never every command (which would silently bypass fail-closed).
        return False
    if deny:
        # review round 3 [C]: match the token only at command-token boundaries —
        # catches ``; rm``, ``bash -c 'rm'`` but NOT ``terraform`` / ``format``.
        # Over-match on the safe side without breaking unrelated commands. Still
        # a best-effort tripwire, not a jail — the real boundary is Tier 1 +
        # sandbox (L7).
        return re.search(rf"(?<!\w){re.escape(token)}(?!\w)", command) is not None
    # allow/ask: narrow — prefix for ``:*``, exact otherwise.
    return command.startswith(token) if specifier.endswith(":*") else command == specifier


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
        # deny is a security boundary → over-match on the safe side (review-fix
        # [3] Bash substring + round 3 [A] wildcard-any-path).
        if rule_matches(parse_rule(spec), tool_name, args, cwd, deny=True):
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


def plan_mode_preset() -> tuple[str, ...]:
    """Legacy plan deny preset retained for public/config compatibility.

    Production plan mode capability-shapes the model catalog and uses a
    deny-only dispatch guard; it no longer wires this preset.
    """
    return ("Edit(*)", "Write(*)", "Bash(*)")
