"""loop-runtime L2 · T1 — ``Settings.permissions`` 声明式规则 schema (RED).

立场(plan §0):对齐 Claude Code 的可移植 ``permissions: {allow, deny, ask}``
嵌套块;与现有 ``permission_mode``(posture) / ``deny_paths``(Tier2) 并存.
形态参照现有 ``CompactSettings`` 嵌套模型(``OPENHARNESS_X__Y`` env 先例).

这些测试现在应当 RED:``PermissionRules`` 与 ``Settings.permissions`` 尚未实现.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from openharness.config import Settings
from openharness.permissions.rules import PermissionRules


class TestPermissionRulesModel:
    def test_defaults_are_empty_tuples(self) -> None:
        rules = PermissionRules()
        assert rules.allow == ()
        assert rules.deny == ()
        assert rules.ask == ()

    def test_holds_explicit_lists(self) -> None:
        rules = PermissionRules(
            allow=("Edit(src/**)",),
            deny=("Write(secrets/**)",),
            ask=("Bash(git push:*)",),
        )
        assert rules.allow == ("Edit(src/**)",)
        assert rules.deny == ("Write(secrets/**)",)
        assert rules.ask == ("Bash(git push:*)",)


class TestSettingsHasPermissionsBlock:
    def test_settings_exposes_permissions_field(self) -> None:
        # Field presence is checked on the class (no instantiation → no env
        # dependency), mirroring how nested CompactSettings is wired.
        assert "permissions" in Settings.model_fields

    def test_settings_permissions_defaults_empty(self) -> None:
        field = Settings.model_fields["permissions"]
        default = field.default_factory() if field.default_factory else field.default  # type: ignore[call-arg,misc]
        assert isinstance(default, PermissionRules)
        assert default.allow == ()
        assert default.deny == ()
        assert default.ask == ()

    def test_permissions_coexists_with_permission_mode_and_deny_paths(self) -> None:
        # The three permission concerns are distinct fields, not collapsed.
        assert "permission_mode" in Settings.model_fields  # posture
        assert "deny_paths" in Settings.model_fields  # Tier 2 legacy
        assert "permissions" in Settings.model_fields  # L2 rule layer


class TestPermissionsEnvParsing:
    """review-fix [5] — the documented ``OPENHARNESS_PERMISSIONS__ALLOW`` env
    var must parse a comma-separated string into a tuple, like ``deny_paths``
    (NoDecode + before-validator). Currently it JSON-decodes → crashes startup.
    """

    def test_comma_separated_env_parses_to_tuple(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        monkeypatch.setenv("OPENHARNESS_PERMISSIONS__ALLOW", "Edit(*),Write(src/**)")
        settings = Settings()
        assert settings.permissions.allow == ("Edit(*)", "Write(src/**)")

    def test_single_rule_env_parses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENHARNESS_API_KEY", "sk-test")
        monkeypatch.setenv("OPENHARNESS_BASE_URL", "https://fake.example.com/v1")
        monkeypatch.setenv("OPENHARNESS_PERMISSIONS__DENY", "Bash(curl:*)")
        settings = Settings()
        assert settings.permissions.deny == ("Bash(curl:*)",)


class TestMalformedRuleRejectedAtLoad:
    """review-fix [6] — a malformed rule must fail at config load (fail-fast),
    not defer an uncaught ValueError to the hot path mid-loop.
    """

    @pytest.mark.parametrize("bad", ["Bash(rm", "Edit(src/**", "()", "(x)"])
    def test_malformed_rule_raises_at_construction(self, bad: str) -> None:
        with pytest.raises(ValidationError):
            PermissionRules(deny=(bad,))

    def test_valid_rules_construct_fine(self) -> None:
        rules = PermissionRules(allow=("Edit(*)", "Bash(npm test:*)"), deny=("Write(secrets/**)",))
        assert rules.allow == ("Edit(*)", "Bash(npm test:*)")
        assert rules.deny == ("Write(secrets/**)",)

    @pytest.mark.parametrize("bad", [[None], [5], 5, [["nested"]]])
    def test_non_str_rule_element_raises_validation_error(self, bad: object) -> None:
        # review round 3 [E]: a non-str element must fail with a clean
        # ValidationError (fail-fast), not a raw AttributeError/TypeError.
        with pytest.raises(ValidationError):
            PermissionRules(allow=bad)

    def test_accepts_non_list_iterable_of_strings(self) -> None:
        # review round 4 [E]: a set/generator of rule strings must still be
        # accepted (round 3 over-narrowed to list/tuple only).
        rules = PermissionRules(allow={"Edit(*)"})
        assert rules.allow == ("Edit(*)",)

    def test_empty_bash_prefix_rejected_at_load(self) -> None:
        # review round 4 [D]: `Bash(:*)` (empty command prefix) is malformed and
        # must fail at config load, not silently match-nothing/everything.
        with pytest.raises(ValidationError):
            PermissionRules(deny=("Bash(:*)",))
