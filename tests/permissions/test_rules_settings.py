"""loop-runtime L2 · T1 — ``Settings.permissions`` 声明式规则 schema (RED).

立场(plan §0):对齐 Claude Code 的可移植 ``permissions: {allow, deny, ask}``
嵌套块;与现有 ``permission_mode``(posture) / ``deny_paths``(Tier2) 并存.
形态参照现有 ``CompactSettings`` 嵌套模型(``OPENHARNESS_X__Y`` env 先例).

这些测试现在应当 RED:``PermissionRules`` 与 ``Settings.permissions`` 尚未实现.
"""

from __future__ import annotations

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
