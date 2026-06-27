"""loop-runtime L2 · T3 — 规则匹配器 + precedence (RED).

立场(plan §0 立场2):precedence = **deny > ask > allow**(对齐 CC).
``match_rules`` 是纯函数,不碰 checker;无命中返回 ``None``(交回 Tier 链).

file specifier 走 cwd-relative glob(比照 Tier2 ``_matches_tier2`` 语义);
Bash specifier 走命令前缀(尾 ``:*``)/精确/``*`` 通配.

这些测试现在应当 RED:``match_rules`` 尚不存在.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from openharness.permissions import Decision
from openharness.permissions.rules import match_rules

if TYPE_CHECKING:
    from pathlib import Path


class _PathInput(BaseModel):
    path: str


class _BashInput(BaseModel):
    command: str


class TestNoMatchReturnsNone:
    def test_empty_rule_sets_return_none(self, tmp_path: Path) -> None:
        result = match_rules(
            "Write",
            _PathInput(path=str(tmp_path / "src" / "x.py")),
            tmp_path,
            allow=(),
            deny=(),
            ask=(),
        )
        assert result is None

    def test_unrelated_rule_returns_none(self, tmp_path: Path) -> None:
        result = match_rules(
            "Write",
            _PathInput(path=str(tmp_path / "README.md")),
            tmp_path,
            allow=("Edit(src/**)",),
            deny=(),
            ask=(),
        )
        assert result is None


class TestFileGlobMatching:
    def test_allow_glob_under_cwd_matches(self, tmp_path: Path) -> None:
        result = match_rules(
            "Write",
            _PathInput(path=str(tmp_path / "src" / "main.py")),
            tmp_path,
            allow=("Write(src/**)",),
            deny=(),
            ask=(),
        )
        assert result is not None
        assert result.decision is Decision.ALLOW

    def test_star_specifier_matches_any_path(self, tmp_path: Path) -> None:
        result = match_rules(
            "Write",
            _PathInput(path=str(tmp_path / "anywhere.txt")),
            tmp_path,
            allow=("Write(*)",),
            deny=(),
            ask=(),
        )
        assert result is not None
        assert result.decision is Decision.ALLOW


class TestBashPrefixMatching:
    def test_prefix_specifier_matches_command_with_prefix(self, tmp_path: Path) -> None:
        result = match_rules(
            "Bash",
            _BashInput(command="npm run test -- foo"),
            tmp_path,
            allow=("Bash(npm run test:*)",),
            deny=(),
            ask=(),
        )
        assert result is not None
        assert result.decision is Decision.ALLOW

    def test_prefix_specifier_does_not_match_other_command(self, tmp_path: Path) -> None:
        result = match_rules(
            "Bash",
            _BashInput(command="rm -rf build"),
            tmp_path,
            allow=("Bash(npm run test:*)",),
            deny=(),
            ask=(),
        )
        assert result is None

    def test_star_matches_any_command(self, tmp_path: Path) -> None:
        result = match_rules(
            "Bash",
            _BashInput(command="echo hi"),
            tmp_path,
            allow=("Bash(*)",),
            deny=(),
            ask=(),
        )
        assert result is not None
        assert result.decision is Decision.ALLOW


class TestPrecedenceDenyAskAllow:
    def test_deny_beats_allow_on_same_target(self, tmp_path: Path) -> None:
        # Same path matches both an allow and a deny rule → deny wins.
        target = str(tmp_path / "secrets" / "k.txt")
        result = match_rules(
            "Write",
            _PathInput(path=target),
            tmp_path,
            allow=("Write(*)",),
            deny=("Write(secrets/**)",),
            ask=(),
        )
        assert result is not None
        assert result.decision is Decision.DENY

    def test_ask_beats_allow_on_same_target(self, tmp_path: Path) -> None:
        target = str(tmp_path / "config" / "app.toml")
        result = match_rules(
            "Write",
            _PathInput(path=target),
            tmp_path,
            allow=("Write(*)",),
            deny=(),
            ask=("Write(config/**)",),
        )
        assert result is not None
        assert result.decision is Decision.ASK

    def test_deny_beats_ask(self, tmp_path: Path) -> None:
        target = str(tmp_path / "config" / "secret.toml")
        result = match_rules(
            "Write",
            _PathInput(path=target),
            tmp_path,
            allow=(),
            deny=("Write(config/secret.toml)",),
            ask=("Write(config/**)",),
        )
        assert result is not None
        assert result.decision is Decision.DENY

    def test_matched_deny_carries_reason(self, tmp_path: Path) -> None:
        result = match_rules(
            "Write",
            _PathInput(path=str(tmp_path / "secrets" / "k.txt")),
            tmp_path,
            allow=(),
            deny=("Write(secrets/**)",),
            ask=(),
        )
        assert result is not None
        assert result.decision is Decision.DENY
        assert result.reason  # non-empty: explains which rule denied
