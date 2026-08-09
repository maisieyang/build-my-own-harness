"""Deny-only semantic guard for tool actions.

This policy is deliberately incapable of granting authority.  A match returns
``DenyResult``; no match returns ``None``.  Positive authorization and exact
boundary grants belong to the permission runtime, not this policy.
"""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

    from openharness.tools.base import ToolExecutionContext, ToolRegistry


_SENSITIVE_PATHS: tuple[str, ...] = (
    "~/.ssh/**",
    "~/.aws/**",
    "~/.gnupg/**",
    "~/.kube/**",
    "~/.config/gh/**",
    "/etc/passwd",
    "/etc/shadow",
    "/etc/sudoers",
)
_CATASTROPHIC_COMMANDS: tuple[str, ...] = (
    "rm -rf /",
    "rm -rf /*",
    ":(){ :|:& };:",
    "mkfs",
    "dd if=/dev/zero of=/dev/",
    "> /dev/sda",
    "chmod -R 777 /",
)
_IRREVERSIBLE_GIT_SUBCOMMANDS = frozenset({"commit", "push"})
_GIT_OPTIONS_WITH_ARGUMENT = frozenset(
    {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}
)


def _glob_match(path: str, pattern: str) -> bool:
    normalized_path = os.path.expanduser(path)
    normalized_pattern = os.path.expanduser(pattern)
    if normalized_pattern.endswith("/**"):
        root = normalized_pattern[:-3]
        return normalized_path == root or normalized_path.startswith(root + "/")
    return fnmatch.fnmatch(normalized_path, normalized_pattern)


def _matches_sensitive_path(path: str) -> str | None:
    return next((pattern for pattern in _SENSITIVE_PATHS if _glob_match(path, pattern)), None)


def _matches_catastrophic_command(command: str) -> str | None:
    return next((pattern for pattern in _CATASTROPHIC_COMMANDS if pattern in command), None)


def _git_subcommand(tokens: list[str]) -> tuple[str, list[str]] | None:
    if not tokens or tokens[0] != "git":
        return None
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token.startswith("-"):
            index += 2 if token in _GIT_OPTIONS_WITH_ARGUMENT else 1
            continue
        return token, tokens[index + 1 :]
    return None


def _matches_irreversible_git_action(command: str) -> str | None:
    for segment in re.split(r"&&|\|\||;|\||\n", command):
        try:
            parsed = _git_subcommand(shlex.split(segment))
        except ValueError:
            continue
        if parsed is None:
            continue
        subcommand, rest = parsed
        if subcommand in _IRREVERSIBLE_GIT_SUBCOMMANDS and "--dry-run" not in rest:
            return f"git {subcommand}"
    return None


def _extract_path_arg(args: BaseModel) -> str | None:
    for attribute in ("path", "file_path"):
        value = getattr(args, attribute, None)
        if isinstance(value, str):
            return value
    return None


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
    """Framework semantic red lines that a profile cannot fully express.

    User filesystem/network/external intent belongs exclusively to the
    canonical profile. This policy contains only non-granting framework
    tripwires and therefore has no configuration constructor.
    """

    def evaluate(
        self,
        tool_name: str,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> DenyResult | None:
        del context
        if tool_name == "Bash":
            command = getattr(args, "command", None)
            if isinstance(command, str):
                bash_pattern = _matches_catastrophic_command(command)
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
            sensitive_pattern = _matches_sensitive_path(path)
            if sensitive_pattern is not None:
                return DenyResult(
                    kind=ActionDenyKind.SENSITIVE_PATH,
                    reason=(f"path {path!r} matches sensitive system path ({sensitive_pattern})"),
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
