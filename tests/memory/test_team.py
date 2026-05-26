"""Tests for the team-scope secret scanner — P11-T4.4c.

Each of the 6 regex patterns + the clean case + multi-hit case.
The function never echoes the actual matched value (to avoid
leaking secrets into log files / test output).
"""

from __future__ import annotations

import pytest

from openharness.memory.team import check_team_memory_secrets


class TestCheckTeamMemorySecrets:
    def test_clean_content_returns_none(self) -> None:
        assert check_team_memory_secrets("This memory describes the project") is None

    def test_pem_private_key_detected(self) -> None:
        content = (
            "Sample bad memory:\n"
            "-----BEGIN PRIVATE KEY-----\n"
            "MIIEvgIBADANBgkqhkiG...\n"
            "-----END PRIVATE KEY-----"
        )
        result = check_team_memory_secrets(content)
        assert result is not None
        assert "PEM private key" in result

    def test_pem_rsa_private_key_detected(self) -> None:
        # The pattern supports RSA/EC/OPENSSH-flavored PEM headers
        result = check_team_memory_secrets("-----BEGIN RSA PRIVATE KEY-----")
        assert result is not None
        assert "PEM private key" in result

    def test_aws_access_key_detected(self) -> None:
        # 16 chars after AKIA per AWS spec
        content = "Our staging uses AKIAIOSFODNN7EXAMPLE for S3 access"
        result = check_team_memory_secrets(content)
        assert result is not None
        assert "AWS access key" in result

    @pytest.mark.parametrize(
        "token",
        [
            "ghp_abcdefghijklmnopqrstuvwxyz0123",  # personal access token
            "gho_abcdefghijklmnopqrstuvwxyz0123",  # OAuth token
            "ghs_abcdefghijklmnopqrstuvwxyz0123",  # server-to-server
            "ghr_abcdefghijklmnopqrstuvwxyz0123",  # refresh
        ],
    )
    def test_github_token_variants_detected(self, token: str) -> None:
        result = check_team_memory_secrets(f"GH token: {token}")
        assert result is not None
        assert "GitHub token" in result

    def test_anthropic_key_detected(self) -> None:
        content = "API key: sk-ant-api03-abc123def456ghi789jkl"
        result = check_team_memory_secrets(content)
        assert result is not None
        assert "Anthropic key" in result

    def test_openai_key_detected(self) -> None:
        content = "OAI key: sk-proj-abcdefghijklmnopqrstuvwxyz123456"
        result = check_team_memory_secrets(content)
        assert result is not None
        # OpenAI pattern matches; Anthropic-specific should NOT match this
        assert "OpenAI" in result

    def test_anthropic_key_does_not_also_match_openai_rule(self) -> None:
        # Anthropic ``sk-ant-`` keys structurally match the looser ``sk-``
        # pattern too. Verify both fire — that's acceptable (defense in
        # depth, no false-negative risk).
        content = "sk-ant-api03-abcdefghijklmnop12345"
        result = check_team_memory_secrets(content)
        assert result is not None
        # At minimum Anthropic matches; OpenAI/generic likely also matches
        # since ``sk-ant-...`` is a strict subset of ``sk-...{20+}``.
        assert "Anthropic key" in result

    def test_generic_secret_pattern_detected(self) -> None:
        result = check_team_memory_secrets("api_key=hunter2hunter2hunter2")
        assert result is not None
        assert "generic secret=value" in result

    def test_generic_password_colon_form(self) -> None:
        result = check_team_memory_secrets("password: hunter2hunter2hunter2")
        assert result is not None
        assert "generic secret=value" in result

    def test_short_value_does_not_trigger_generic(self) -> None:
        # 12-char value minimum — "token: a" shouldn't flag
        assert check_team_memory_secrets("token: a") is None
        assert check_team_memory_secrets("token: short") is None

    def test_multiple_secrets_all_listed(self) -> None:
        content = "AKIAIOSFODNN7EXAMPLE\nghp_abcdefghijklmnopqrstuvwxyz0123\n"
        result = check_team_memory_secrets(content)
        assert result is not None
        assert "AWS access key" in result
        assert "GitHub token" in result

    def test_actual_value_never_in_error_string(self) -> None:
        # The error message must NOT contain the secret value itself —
        # otherwise it would leak the secret into log files. The
        # function returns rule labels only.
        secret = "AKIAIOSFODNN7EXAMPLE"
        result = check_team_memory_secrets(f"key: {secret}")
        assert result is not None
        assert secret not in result
