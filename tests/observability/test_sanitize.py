"""Tests for sanitize processor + call-site helpers — P3-T5.5b.

Three responsibility surfaces:

1. ``sanitize_processor`` integrated into ``configure_logging`` — verify
   that redaction happens before the renderer sees the event.
2. Unit-level helpers (``_sanitize_value`` / ``_sanitize_dict``) — verify
   each redaction rule independently.
3. Call-site helpers (``sanitize_path`` / ``sanitize_command``) — verify
   the semantic field sanitizers 5c/5d will lean on.
"""

from __future__ import annotations

import io
import json
import logging
from typing import TYPE_CHECKING

import pytest

from openharness.observability import (
    configure_logging,
    get_logger,
    sanitize_command,
    sanitize_path,
)
from openharness.observability.sanitize import (
    REDACTED,
    REDACTED_TOKEN,
    SENSITIVE_KEYS,
    TOKEN_PATTERNS,
    _is_sensitive_key,
    _redact_token_in_string,
    _sanitize_dict,
    _sanitize_value,
    sanitize_processor,
)

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


@pytest.fixture
def stream() -> Iterator[io.StringIO]:
    s = io.StringIO()
    yield s
    logging.getLogger().handlers.clear()


# --------------------------------------------------------------------------- #
# Sensitive-key lookup                                                        #
# --------------------------------------------------------------------------- #


class TestSensitiveKeyLookup:
    @pytest.mark.parametrize(
        "key",
        [
            "password",
            "PASSWORD",
            "Password",
            "api_key",
            "API_KEY",
            "API-KEY",
            "api-key",
            "Authorization",
            "AUTHORIZATION",
            "aws_secret_access_key",
            "AWS-Secret-Access-Key",
        ],
    )
    def test_case_and_dash_underscore_insensitive(self, key: str) -> None:
        assert _is_sensitive_key(key) is True

    @pytest.mark.parametrize(
        "key",
        ["path", "url", "name", "tool_name", "duration_ms", "is_error"],
    )
    def test_non_sensitive_keys_pass(self, key: str) -> None:
        assert _is_sensitive_key(key) is False

    def test_sensitive_keys_frozenset_is_immutable(self) -> None:
        # Catches accidental .add() / .remove() in future refactors.
        with pytest.raises(AttributeError):
            SENSITIVE_KEYS.add("new_key")  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Token-pattern detection                                                     #
# --------------------------------------------------------------------------- #


class TestTokenPatterns:
    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("openai", "sk-1234567890abcdefghij"),
            ("openai-long", "sk-" + "a" * 48),
            ("github-pat-classic", "ghp_" + "a" * 36),
            ("github-pat-fine", "github_pat_" + "a" * 82),
            ("aws-akid", "AKIAIOSFODNN7EXAMPLE"),
            ("google", "AIza" + "a" * 35),
            (
                "jwt",
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.signature",
            ),
        ],
    )
    def test_known_token_patterns_redacted(self, label: str, value: str) -> None:
        del label
        assert _redact_token_in_string(value) == REDACTED_TOKEN

    def test_innocuous_strings_pass(self) -> None:
        for value in ["hello world", "path/to/file", "/tmp/xyz", "12345"]:
            assert _redact_token_in_string(value) == value

    def test_token_inside_longer_string_still_redacts(self) -> None:
        # `re.search` not `re.fullmatch` — token in middle still hits.
        value = "Authorization: Bearer sk-1234567890abcdefghij"
        assert _redact_token_in_string(value) == REDACTED_TOKEN

    def test_token_patterns_compiled(self) -> None:
        import re

        # Catch refactors that downgrade to plain strings.
        for pattern in TOKEN_PATTERNS:
            assert isinstance(pattern, re.Pattern)


# --------------------------------------------------------------------------- #
# Recursive sanitize_value                                                    #
# --------------------------------------------------------------------------- #


class TestSanitizeValueRecursion:
    def test_primitive_pass_through(self) -> None:
        for value in [42, 3.14, True, False, None]:
            assert _sanitize_value(value) == value

    def test_flat_dict_redacts_sensitive_key(self) -> None:
        result = _sanitize_value({"api_key": "abc", "model": "qwen-plus"})
        assert result == {"api_key": REDACTED, "model": "qwen-plus"}

    def test_nested_dict_recurses(self) -> None:
        result = _sanitize_value(
            {
                "outer": {"inner": {"password": "secret", "level": 3}},
                "level": 1,
            }
        )
        assert result == {
            "outer": {"inner": {"password": REDACTED, "level": 3}},
            "level": 1,
        }

    def test_list_recurses(self) -> None:
        result = _sanitize_value(
            [{"token": "abc"}, "ok", {"name": "foo"}],
        )
        assert result == [{"token": REDACTED}, "ok", {"name": "foo"}]

    def test_tuple_becomes_list(self) -> None:
        # Documented in sanitize.py: tuple identity is lost after sanitize.
        result = _sanitize_value(("a", "b"))
        assert result == ["a", "b"]

    def test_string_with_token_redacted(self) -> None:
        result = _sanitize_value("Bearer sk-1234567890abcdefghij")
        assert result == REDACTED_TOKEN

    def test_unknown_type_pass_through(self) -> None:
        class Custom:
            pass

        obj = Custom()
        assert _sanitize_value(obj) is obj  # not expanded, not wrapped


class TestSanitizeDict:
    def test_returns_new_dict_not_in_place(self) -> None:
        original = {"api_key": "abc", "model": "x"}
        result = _sanitize_dict(original)
        assert result is not original
        # Original unchanged — important for callers reusing dict literals.
        assert original == {"api_key": "abc", "model": "x"}


# --------------------------------------------------------------------------- #
# Processor integration with structlog                                        #
# --------------------------------------------------------------------------- #


class TestSanitizeProcessor:
    def test_signature_accepts_logger_and_method_name(self) -> None:
        # structlog passes logger + method_name; sanitize_processor must
        # accept (and ignore) them.
        result = sanitize_processor(None, "info", {"password": "abc", "k": "v"})
        assert result == {"password": REDACTED, "k": "v"}

    def test_processor_redacts_in_real_log_call(self, stream: io.StringIO) -> None:
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        log.info(
            "credentials_check",
            api_key="abc-secret",
            token="ghp_" + "a" * 36,
            tool="Read",
        )

        record = json.loads(stream.getvalue().strip())
        assert record["api_key"] == REDACTED
        assert record["token"] == REDACTED  # SENSITIVE_KEYS key match
        assert record["tool"] == "Read"

    def test_processor_redacts_token_in_value_under_innocuous_key(
        self, stream: io.StringIO
    ) -> None:
        # Key name is innocuous but value contains a known token shape.
        configure_logging(level="DEBUG", format="json", stream=stream)
        log = get_logger("test")
        log.info(
            "request_dump",
            headers={"Authorization": "Bearer sk-1234567890abcdefghij"},
        )

        record = json.loads(stream.getvalue().strip())
        # Authorization is itself a sensitive key — but even if it weren't,
        # the value would have been redacted by token pattern. This test
        # locks the key-level behavior; the value-level case is covered
        # by ``test_token_inside_longer_string_still_redacts``.
        assert record["headers"]["Authorization"] == REDACTED


# --------------------------------------------------------------------------- #
# sanitize_path helper                                                        #
# --------------------------------------------------------------------------- #


class TestSanitizePath:
    def test_path_inside_cwd_returns_relative(self, tmp_path: Path) -> None:
        nested = tmp_path / "subdir" / "file.txt"
        nested.parent.mkdir(parents=True)
        nested.touch()

        assert sanitize_path(nested, tmp_path) == "subdir/file.txt"

    def test_path_outside_cwd_redacted(self, tmp_path: Path) -> None:
        # /etc/passwd is reliably outside tmp_path.
        result = sanitize_path("/etc/passwd", tmp_path)
        assert result == REDACTED

    def test_nonexistent_path_inside_cwd_still_relative(self, tmp_path: Path) -> None:
        # Phase 3 Write uses sanitize_path on paths that don't exist yet.
        ghost = tmp_path / "not_yet.md"
        assert sanitize_path(ghost, tmp_path) == "not_yet.md"

    def test_string_path_accepted(self, tmp_path: Path) -> None:
        nested = tmp_path / "file.txt"
        nested.touch()
        # Pass string, not Path — call sites often have already-stringified.
        assert sanitize_path(str(nested), tmp_path) == "file.txt"


# --------------------------------------------------------------------------- #
# sanitize_command helper                                                     #
# --------------------------------------------------------------------------- #


class TestSanitizeCommand:
    def test_short_command_emits_first_token_and_length(self) -> None:
        assert sanitize_command("ls -la") == "ls (len=6)"

    def test_long_command_with_creds_only_logs_first_token(self) -> None:
        cmd = 'curl -H "Authorization: Bearer sk-1234567890" https://api.example.com'
        result = sanitize_command(cmd)
        assert result.startswith("curl (len=")
        # Critical: token MUST NOT appear in the returned string.
        assert "sk-1234567890" not in result
        assert "Bearer" not in result

    def test_empty_command_returns_sentinel(self) -> None:
        assert sanitize_command("") == "(empty)"
        assert sanitize_command("   ") == "(empty)"

    def test_env_prefix_command_still_only_logs_first_token(self) -> None:
        # `API_KEY=sk-... command args` — first token is the env prefix.
        # We log its length so suspicious-long first tokens are visible.
        cmd = "API_KEY=sk-1234567890abcdefghij python script.py"
        result = sanitize_command(cmd)
        assert "sk-1234567890" not in result
        # First token IS "API_KEY=sk-..." — but its length is in the output.
        assert "(len=" in result
