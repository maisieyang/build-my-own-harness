"""Deny-only semantic guard for tool actions.

This policy is deliberately incapable of granting authority.  A match returns
``DenyResult``; no match returns ``None``.  Positive authorization and exact
boundary grants belong to the permission runtime, not this policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

from openharness.permissions.rules import parse_rule, rule_matches
from openharness.permissions.tier_based import (
    _extract_path_arg,
    _matches_bash_deny,
    _matches_irreversible_git_action,
    _matches_tier1,
    _matches_tier2,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

    from openharness.config import Settings
    from openharness.permissions.rules import PermissionRule
    from openharness.tools.base import ToolExecutionContext, ToolRegistry


class ActionDenyKind(Enum):
    """Stable source category for a semantic action denial."""

    CATASTROPHIC_COMMAND = "catastrophic_command"
    IRREVERSIBLE_GIT = "irreversible_git"
    SENSITIVE_PATH = "sensitive_path"
    CONFIGURED_PATH = "configured_path"
    CONFIGURED_RULE = "configured_rule"
    PLAN_CAPABILITY = "plan_capability"
    POLICY_FAILURE = "policy_failure"


@dataclass(frozen=True)
class DenyResult:
    """A negative policy match; the type intentionally has no allow/ask API."""

    kind: ActionDenyKind
    reason: str


class ActionDenyPolicy(Protocol):
    """Reject an action or return no match, without granting authority."""

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DenyResult | None:
        """Return a denial for a forbidden action, otherwise ``None``."""
        ...


class ConfiguredActionDenyPolicy:
    """Framework red lines plus the user's negative-only legacy rules.

    This is the canonical negative authority beside the legacy host checker. It intentionally
    excludes ``permissions.allow``, ``permissions.ask``, Tier 3 ASK, and the
    headless fail-closed gate because none of those are deny-only action facts.
    """

    def __init__(self, settings: Settings) -> None:
        self._deny_paths = settings.deny_paths
        self._deny_rules: tuple[tuple[str, PermissionRule], ...] = tuple(
            (spec, parse_rule(spec)) for spec in settings.permissions.deny
        )

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DenyResult | None:
        if tool_name == "Bash":
            command = getattr(args, "command", None)
            if isinstance(command, str):
                bash_pattern = _matches_bash_deny(command)
                if bash_pattern is not None:
                    return DenyResult(
                        kind=ActionDenyKind.CATASTROPHIC_COMMAND,
                        reason=f"matches catastrophic Bash pattern {bash_pattern!r}",
                    )

                git_action = _matches_irreversible_git_action(command)
                if git_action is not None:
                    return DenyResult(
                        kind=ActionDenyKind.IRREVERSIBLE_GIT,
                        reason=f"irreversible git action: {git_action}",
                    )

        path = _extract_path_arg(args)
        if path is not None:
            sensitive_pattern = _matches_tier1(path)
            if sensitive_pattern is not None:
                return DenyResult(
                    kind=ActionDenyKind.SENSITIVE_PATH,
                    reason=(f"path {path!r} matches sensitive system path ({sensitive_pattern})"),
                )

            configured_pattern = _matches_tier2(path, self._deny_paths, context.cwd)
            if configured_pattern is not None:
                return DenyResult(
                    kind=ActionDenyKind.CONFIGURED_PATH,
                    reason=f"path {path!r} matches deny rule {configured_pattern!r}",
                )

        for spec, rule in self._deny_rules:
            if rule_matches(rule, tool_name, args, context.cwd, deny=True):
                return DenyResult(
                    kind=ActionDenyKind.CONFIGURED_RULE,
                    reason=f"matches deny rule {spec!r}",
                )

        return None


class PlanActionDenyPolicy:
    """Plan-mode capability clamp layered over the canonical deny policy.

    This policy cannot grant an action. It preserves every denial from the
    configured policy, then rejects mutation and delegated execution even if a
    caller forges a tool call that was absent from the model-visible catalog.
    """

    def __init__(self, *, registry: ToolRegistry, base: ActionDenyPolicy) -> None:
        self._registry = registry
        self._base = base

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DenyResult | None:
        from openharness.tools.base import ExecutionDomain

        base_deny = self._base.evaluate(tool_name, args, context)
        if base_deny is not None:
            return base_deny

        try:
            tool = self._registry.get(tool_name)
        except KeyError:
            return None
        if tool.execution_domain is ExecutionDomain.DELEGATED_RUNTIME or not tool.is_read_only:
            return DenyResult(
                kind=ActionDenyKind.PLAN_CAPABILITY,
                reason=f"{tool_name} is unavailable in plan mode",
            )
        return None
