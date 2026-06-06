"""Tests for :class:`Memory` dataclass + enums + signature — P10-T1.1a.

Three surfaces:

1. **Enum shape** — ``MemoryType`` / ``MemoryScope`` round-trip via str.
2. **Dataclass shape** — frozen, all validators in ``__post_init__``
   enforced (name regex, non-empty id / description / signature,
   importance range, non-negative use_count, positive-or-None ttl_days).
3. **Signature helper** — :func:`compute_memory_signature` is
   deterministic + sensitive to all three inputs + stable under
   cosmetic whitespace.

:func:`parse_memory` (the frontmatter → dataclass path) lands in 1b
with its own test file.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from pathlib import Path

import pytest

from openharness.memory.model import (
    Memory,
    MemoryScope,
    MemoryType,
    compute_memory_signature,
    parse_memory,
)

_UTC = timezone.utc


def _build_memory(**overrides: object) -> Memory:
    """Test fixture builder — minimal-valid Memory + per-test overrides.

    Centralized so 20+ tests don't repeat 10 fields each. Each test
    overrides only the field(s) it cares about.
    """
    defaults: dict[str, object] = {
        "id": "01HXXXXXXXX",
        "name": "x",
        "description": "d",
        "type": MemoryType.PROJECT,
        "scope": MemoryScope.PRIVATE,
        "created_at": datetime(2026, 1, 1, tzinfo=_UTC),
        "updated_at": datetime(2026, 1, 1, tzinfo=_UTC),
        "body": "hello",
        "signature": "a" * 64,
        "source_path": Path("/tmp/x.md"),
    }
    defaults.update(overrides)
    return Memory(**defaults)  # type: ignore[arg-type]


class TestMemoryTypeEnum:
    def test_values(self) -> None:
        assert MemoryType.USER.value == "user"
        assert MemoryType.FEEDBACK.value == "feedback"
        assert MemoryType.PROJECT.value == "project"
        assert MemoryType.REFERENCE.value == "reference"

    def test_str_mixin(self) -> None:
        # ``str + Enum`` so equality with raw string holds — important
        # for YAML round-trip where frontmatter loads ``type: project``
        # as a plain string. Tested in both directions to confirm
        # commutativity (the ``__eq__`` lives on ``str``).
        assert MemoryType.PROJECT == "project"
        plain_str = "project"
        assert plain_str == MemoryType.PROJECT

    def test_construct_from_value(self) -> None:
        assert MemoryType("project") is MemoryType.PROJECT

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            MemoryType("bogus")


class TestMemoryScopeEnum:
    def test_private_and_team(self) -> None:
        # Phase 10 D28.5 shipped only PRIVATE; Phase 11 D29.10 adds
        # TEAM alongside extraction's write path + secret scanning.
        # This test pins both values — accidentally dropping TEAM
        # would silently break extraction's team-scope routing.
        assert set(MemoryScope) == {MemoryScope.PRIVATE, MemoryScope.TEAM}
        assert MemoryScope.PRIVATE.value == "private"
        assert MemoryScope.TEAM.value == "team"

    def test_str_mixin(self) -> None:
        assert MemoryScope.PRIVATE == "private"
        assert MemoryScope.TEAM == "team"


class TestMemoryDataclass:
    def test_minimal_construction_defaults(self) -> None:
        m = _build_memory()
        assert m.name == "x"
        assert m.id == "01HXXXXXXXX"
        assert m.description == "d"
        assert m.type is MemoryType.PROJECT
        assert m.scope is MemoryScope.PRIVATE
        assert m.body == "hello"
        # Defaults per D28.3
        assert m.ttl_days is None
        assert m.disabled is False
        assert m.supersedes == ()
        assert m.importance == 0.5
        assert m.tags == ()
        assert m.use_count == 0
        assert m.last_used_at is None

    def test_is_frozen(self) -> None:
        m = _build_memory()
        with pytest.raises(dataclasses.FrozenInstanceError):
            m.name = "y"  # type: ignore[misc]

    def test_is_hashable(self) -> None:
        # Frozen dataclasses with hashable fields are hashable; required
        # so relevance / store layers can dedupe via set membership.
        m1 = _build_memory()
        m2 = _build_memory()
        # Same value → same hash (frozen dataclass identity-by-field)
        assert hash(m1) == hash(m2)
        # Different value → likely different hash
        m3 = _build_memory(name="y")
        assert hash(m1) != hash(m3)

    @pytest.mark.parametrize(
        "name",
        ["a", "abc", "stripe-sdk-version", "snake_case", "PascalCase", "X1"],
    )
    def test_valid_names_accepted(self, name: str) -> None:
        _build_memory(name=name)  # no raise

    @pytest.mark.parametrize(
        "name",
        [
            "",  # empty
            "1abc",  # starts with digit
            "_underscore",  # starts with underscore
            "-dash",  # starts with dash
            "with space",
            "with.dot",  # collides with potential namespace separator
            "with/slash",
            "name$dollar",
        ],
    )
    def test_invalid_names_rejected(self, name: str) -> None:
        with pytest.raises(ValueError, match="invalid memory name"):
            _build_memory(name=name)

    def test_empty_id_still_rejected(self) -> None:
        """Phase 17 D37.1: id is Optional[str] = None at dataclass level
        (so CC-style frontmatter can omit it), but an EMPTY id is still
        a frontmatter bug worth catching. ``__post_init__`` accepts
        None but rejects empty / whitespace-only strings."""
        with pytest.raises(ValueError, match="id, when provided, must be non-empty"):
            _build_memory(id="")

    def test_whitespace_only_id_still_rejected(self) -> None:
        with pytest.raises(ValueError, match="id, when provided, must be non-empty"):
            _build_memory(id="   ")

    def test_none_id_accepted(self) -> None:
        """Phase 17 D37.1: Memory(id=None, ...) is valid construction.
        parse_memory always provides a non-None id (auto-generated when
        the frontmatter omits the field), but direct callers may
        construct with id=None for test/spike purposes."""
        m = _build_memory(id=None)
        assert m.id is None

    def test_empty_description_rejected(self) -> None:
        with pytest.raises(ValueError, match="description must be non-empty"):
            _build_memory(description="   ")

    def test_empty_signature_rejected(self) -> None:
        with pytest.raises(ValueError, match="signature must be non-empty"):
            _build_memory(signature="")

    @pytest.mark.parametrize("importance", [-0.01, 1.01, -1.0, 2.0])
    def test_importance_out_of_range_rejected(self, importance: float) -> None:
        with pytest.raises(ValueError, match="importance"):
            _build_memory(importance=importance)

    @pytest.mark.parametrize("importance", [0.0, 0.5, 1.0])
    def test_importance_at_bounds_accepted(self, importance: float) -> None:
        _build_memory(importance=importance)  # no raise

    def test_negative_use_count_rejected(self) -> None:
        with pytest.raises(ValueError, match="use_count"):
            _build_memory(use_count=-1)

    @pytest.mark.parametrize("ttl", [0, -1, -100])
    def test_non_positive_ttl_rejected(self, ttl: int) -> None:
        with pytest.raises(ValueError, match="ttl_days"):
            _build_memory(ttl_days=ttl)

    def test_ttl_none_accepted(self) -> None:
        m = _build_memory(ttl_days=None)
        assert m.ttl_days is None

    def test_ttl_positive_accepted(self) -> None:
        m = _build_memory(ttl_days=30)
        assert m.ttl_days == 30


class TestComputeMemorySignature:
    def test_deterministic(self) -> None:
        s1 = compute_memory_signature("body", MemoryType.PROJECT, MemoryScope.PRIVATE)
        s2 = compute_memory_signature("body", MemoryType.PROJECT, MemoryScope.PRIVATE)
        assert s1 == s2

    def test_is_sha256_hex(self) -> None:
        s = compute_memory_signature("x", MemoryType.PROJECT, MemoryScope.PRIVATE)
        # SHA-256 hex = 64 lowercase hex chars
        assert len(s) == 64
        assert all(c in "0123456789abcdef" for c in s)

    def test_different_body_different_signature(self) -> None:
        s1 = compute_memory_signature("a", MemoryType.PROJECT, MemoryScope.PRIVATE)
        s2 = compute_memory_signature("b", MemoryType.PROJECT, MemoryScope.PRIVATE)
        assert s1 != s2

    def test_different_type_different_signature(self) -> None:
        s1 = compute_memory_signature("x", MemoryType.PROJECT, MemoryScope.PRIVATE)
        s2 = compute_memory_signature("x", MemoryType.USER, MemoryScope.PRIVATE)
        assert s1 != s2

    def test_whitespace_normalization_at_edges(self) -> None:
        # ``strip()`` on body before hashing — editors that add trailing
        # newline shouldn't fragment dedup.
        s1 = compute_memory_signature("hello", MemoryType.PROJECT, MemoryScope.PRIVATE)
        s2 = compute_memory_signature("  hello  \n", MemoryType.PROJECT, MemoryScope.PRIVATE)
        assert s1 == s2

    def test_inner_whitespace_preserved(self) -> None:
        # Inner whitespace is meaningful and stays in the hash.
        s1 = compute_memory_signature("a b", MemoryType.PROJECT, MemoryScope.PRIVATE)
        s2 = compute_memory_signature("ab", MemoryType.PROJECT, MemoryScope.PRIVATE)
        assert s1 != s2

    def test_null_separator_prevents_collision(self) -> None:
        # ``body="x" type="user"`` should NOT collide with
        # ``body="xuser" type=""`` — null-byte separators guard against
        # this. The empty-type case isn't reachable (type is required +
        # enum-validated), but the test pins the property explicitly.
        s1 = compute_memory_signature("x", MemoryType.USER, MemoryScope.PRIVATE)
        # Manually concat: body="xuser", type=USER, scope=PRIVATE → should differ
        s2 = compute_memory_signature("xuser", MemoryType.USER, MemoryScope.PRIVATE)
        assert s1 != s2


# ---------------------------------------------------------------------------
# parse_memory — frontmatter → Memory (P10-T1.1b)
# ---------------------------------------------------------------------------


_VALID_FRONTMATTER = (
    "---\n"
    "id: 01HXXXXXXXX\n"
    "name: stripe-sdk-version\n"
    "description: Stripe SDK 8.x with legacy API\n"
    "type: project\n"
    "scope: private\n"
    "created_at: 2026-05-26T10:00:00+00:00\n"
    "updated_at: 2026-05-26T10:00:00+00:00\n"
    "---\n"
    "\n"
    "Body content describing the stripe SDK behavior.\n"
)


class TestParseMemoryHappy:
    """Happy paths: minimal valid frontmatter + full frontmatter both parse."""

    def test_minimal_valid(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(_VALID_FRONTMATTER)
        m = parse_memory(path)
        assert m is not None
        assert m.id == "01HXXXXXXXX"
        assert m.name == "stripe-sdk-version"
        assert m.description == "Stripe SDK 8.x with legacy API"
        assert m.type is MemoryType.PROJECT
        assert m.scope is MemoryScope.PRIVATE
        assert m.created_at == datetime(2026, 5, 26, 10, 0, 0, tzinfo=_UTC)
        assert m.updated_at == datetime(2026, 5, 26, 10, 0, 0, tzinfo=_UTC)
        assert "Body content" in m.body
        assert m.source_path == path
        # Defaults applied when optional fields are omitted
        assert m.ttl_days is None
        assert m.disabled is False
        assert m.supersedes == ()
        assert m.importance == 0.5
        assert m.tags == ()
        assert m.use_count == 0
        assert m.last_used_at is None
        # Signature computed because frontmatter omitted it
        assert m.signature == compute_memory_signature(
            m.body, MemoryType.PROJECT, MemoryScope.PRIVATE
        )

    def test_full_frontmatter_all_14_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\n"
            "id: 01HFULL000000\n"
            "name: full-test\n"
            "description: All fields populated\n"
            "type: feedback\n"
            "scope: private\n"
            "created_at: 2026-01-01T00:00:00+00:00\n"
            "updated_at: 2026-02-01T00:00:00+00:00\n"
            "signature: " + "b" * 64 + "\n"
            "ttl_days: 90\n"
            "disabled: true\n"
            "supersedes: [01HOLDID0001, 01HOLDID0002]\n"
            "importance: 0.75\n"
            "tags: [billing, refund]\n"
            "use_count: 7\n"
            "last_used_at: 2026-05-01T12:34:56+00:00\n"
            "---\n"
            "Body text.\n"
        )
        m = parse_memory(path)
        assert m is not None
        assert m.type is MemoryType.FEEDBACK
        assert m.signature == "b" * 64  # NOT recomputed when present
        assert m.ttl_days == 90
        assert m.disabled is True
        assert m.supersedes == ("01HOLDID0001", "01HOLDID0002")
        assert m.importance == 0.75
        assert m.tags == ("billing", "refund")
        assert m.use_count == 7
        assert m.last_used_at == datetime(2026, 5, 1, 12, 34, 56, tzinfo=_UTC)

    def test_body_preserved_verbatim(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            "---\n"
            "id: x\n"
            "name: x\n"
            "description: y\n"
            "type: project\n"
            "scope: private\n"
            "created_at: 2026-01-01T00:00:00+00:00\n"
            "updated_at: 2026-01-01T00:00:00+00:00\n"
            "---\n"
            "Line 1\n\nLine 3  with  spaces\n```\ncode\n```\n"
        )
        m = parse_memory(path)
        assert m is not None
        assert m.body == "Line 1\n\nLine 3  with  spaces\n```\ncode\n```\n"

    def test_extra_frontmatter_fields_ignored(self, tmp_path: Path) -> None:
        # Forward-compat: unknown fields tolerated (matches parse_skill).
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "---\n\n", "future_field: future_value\nanother: [a, b]\n---\n\n"
            )
        )
        m = parse_memory(path)
        assert m is not None

    def test_naive_datetime_gets_utc_filled(self, tmp_path: Path) -> None:
        # Hand-written timestamps without offset → assume UTC.
        path = tmp_path / "x.md"
        path.write_text(
            "---\n"
            "id: x\n"
            "name: x\n"
            "description: y\n"
            "type: project\n"
            "scope: private\n"
            "created_at: 2026-01-01T00:00:00\n"  # no offset
            "updated_at: 2026-01-01T00:00:00\n"
            "---\nbody\n"
        )
        m = parse_memory(path)
        assert m is not None
        assert m.created_at.tzinfo is not None
        assert m.created_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=_UTC)

    def test_string_timestamp_parses(self, tmp_path: Path) -> None:
        # Quoted timestamp → str → fromisoformat.
        path = tmp_path / "x.md"
        path.write_text(
            "---\n"
            "id: x\n"
            "name: x\n"
            "description: y\n"
            "type: project\n"
            "scope: private\n"
            'created_at: "2026-01-01T00:00:00+00:00"\n'
            'updated_at: "2026-01-01T00:00:00+00:00"\n'
            "---\nbody\n"
        )
        m = parse_memory(path)
        assert m is not None
        assert m.created_at == datetime(2026, 1, 1, 0, 0, 0, tzinfo=_UTC)


class TestParseMemoryErrors:
    """Every malformed input returns ``None``,never raises."""

    def test_file_not_exist(self, tmp_path: Path) -> None:
        assert parse_memory(tmp_path / "missing.md") is None

    def test_no_frontmatter(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("Just a body\n")
        assert parse_memory(path) is None

    def test_malformed_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text("---\nname: : :\n[unbalanced\n---\nbody\n")
        assert parse_memory(path) is None

    @pytest.mark.parametrize(
        "field",
        ["name", "description", "type"],
    )
    def test_missing_required_field(self, tmp_path: Path, field: str) -> None:
        """Phase 17 D37.1/D37.2 narrowed the strict-required set to
        name + description + type (matching the D36.10 CC contract).
        The other Phase 10 fields (id, scope, created_at, updated_at)
        now auto-fill — see test_missing_optional_field_auto_fills."""
        content_lines = _VALID_FRONTMATTER.splitlines()
        filtered = [ln for ln in content_lines if not ln.startswith(f"{field}:")]
        path = tmp_path / "x.md"
        path.write_text("\n".join(filtered) + "\n")
        assert parse_memory(path) is None

    @pytest.mark.parametrize(
        "field",
        ["id", "scope", "created_at", "updated_at"],
    )
    def test_missing_optional_field_auto_fills(self, tmp_path: Path, field: str) -> None:
        """Phase 17 D37.1/D37.2: id / scope / created_at / updated_at
        all auto-fill when frontmatter omits them. Parser produces a
        valid Memory rather than warning-skipping."""
        content_lines = _VALID_FRONTMATTER.splitlines()
        filtered = [ln for ln in content_lines if not ln.startswith(f"{field}:")]
        path = tmp_path / "x.md"
        path.write_text("\n".join(filtered) + "\n")
        memory = parse_memory(path)
        assert memory is not None, f"omitting optional field {field!r} should auto-fill, not skip"
        # Verify the field has a sensible auto-fill value (truthy in each case).
        assert getattr(memory, field) is not None

    def test_missing_id_produces_deterministic_hash(self, tmp_path: Path) -> None:
        """D37.1 auto-generated id is sha1(name + resolved path)[:16] and
        stable across reparses of the same file."""
        content_lines = _VALID_FRONTMATTER.splitlines()
        filtered = [ln for ln in content_lines if not ln.startswith("id:")]
        path = tmp_path / "x.md"
        path.write_text("\n".join(filtered) + "\n")
        m1 = parse_memory(path)
        m2 = parse_memory(path)
        assert m1 is not None and m2 is not None
        assert m1.id == m2.id
        # 16 hex chars (the sha1[:16] slice).
        assert m1.id is not None and len(m1.id) == 16
        assert all(c in "0123456789abcdef" for c in m1.id)

    def test_cc_style_frontmatter_parses_cleanly(self, tmp_path: Path) -> None:
        """D36.10 mandated frontmatter (name + description +
        metadata.type only) is the canonical CC schema. Parser must
        accept it without warning-skipping any required field.
        The 2026-06-06 dogfood was the regression case that motivated
        D37.1/D37.2."""
        cc_content = (
            "---\n"
            "name: user-role-and-values\n"
            "description: Engineer between jobs, solo-building with AI\n"
            "metadata:\n"
            "  type: user\n"
            "---\n"
            "\n"
            "Body text here.\n"
        )
        path = tmp_path / "user_role_and_values.md"
        path.write_text(cc_content)
        memory = parse_memory(path)
        assert memory is not None
        assert memory.name == "user-role-and-values"
        assert memory.type.value == "user"
        # Auto-filled fields all present + valid.
        assert memory.id is not None
        assert memory.scope.value == "private"
        assert memory.created_at is not None
        assert memory.updated_at is not None

    def test_invalid_name_regex(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(_VALID_FRONTMATTER.replace("name: stripe-sdk-version", "name: with space"))
        assert parse_memory(path) is None

    def test_invalid_type_enum(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(_VALID_FRONTMATTER.replace("type: project", "type: bogus"))
        assert parse_memory(path) is None

    def test_scope_team_accepted_in_phase_11(self, tmp_path: Path) -> None:
        # Phase 11 D29.10 enables team scope (Phase 10 D28.5 rejected it).
        # parse_memory now accepts ``scope: team`` — secret scanning
        # is the writer's responsibility, not the parser's.
        path = tmp_path / "x.md"
        path.write_text(_VALID_FRONTMATTER.replace("scope: private", "scope: team"))
        m = parse_memory(path)
        assert m is not None
        assert m.scope is MemoryScope.TEAM

    def test_invalid_scope_enum(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(_VALID_FRONTMATTER.replace("scope: private", "scope: bogus"))
        assert parse_memory(path) is None

    def test_unparseable_timestamp(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "created_at: 2026-05-26T10:00:00+00:00",
                'created_at: "not-a-date"',
            )
        )
        assert parse_memory(path) is None

    def test_empty_description(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "description: Stripe SDK 8.x with legacy API",
                'description: "   "',
            )
        )
        assert parse_memory(path) is None

    def test_post_init_violation_caught(self, tmp_path: Path) -> None:
        # importance > 1.0 → __post_init__ raises ValueError → parser returns None.
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", "scope: private\nimportance: 2.0\n")
        )
        assert parse_memory(path) is None


class TestParseMemoryCoercion:
    """Optional fields with wrong type → log + use default (graceful degrade)."""

    def test_ttl_days_wrong_type_uses_default(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", 'scope: private\nttl_days: "thirty"\n')
        )
        m = parse_memory(path)
        assert m is not None
        assert m.ttl_days is None  # invalid → default

    def test_bool_not_accepted_as_ttl_days(self, tmp_path: Path) -> None:
        # ``bool`` is ``int`` subclass — must NOT silently become ``1``.
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", "scope: private\nttl_days: true\n")
        )
        m = parse_memory(path)
        assert m is not None
        assert m.ttl_days is None

    def test_disabled_wrong_type_uses_default(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", 'scope: private\ndisabled: "yes"\n')
        )
        m = parse_memory(path)
        assert m is not None
        assert m.disabled is False

    def test_supersedes_non_list_uses_default(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "scope: private\n", "scope: private\nsupersedes: just-a-string\n"
            )
        )
        m = parse_memory(path)
        assert m is not None
        assert m.supersedes == ()

    def test_supersedes_drops_non_string_items(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "scope: private\n",
                "scope: private\nsupersedes: [good, 42, also-good]\n",
            )
        )
        m = parse_memory(path)
        assert m is not None
        assert m.supersedes == ("good", "also-good")

    def test_importance_int_coerced_to_float(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", "scope: private\nimportance: 1\n")
        )
        m = parse_memory(path)
        assert m is not None
        assert m.importance == 1.0
        assert isinstance(m.importance, float)

    def test_importance_wrong_type_uses_default(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", 'scope: private\nimportance: "high"\n')
        )
        m = parse_memory(path)
        assert m is not None
        assert m.importance == 0.5

    def test_tags_drops_non_string_items(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "scope: private\n", "scope: private\ntags: [good, 99, also]\n"
            )
        )
        m = parse_memory(path)
        assert m is not None
        assert m.tags == ("good", "also")

    def test_use_count_wrong_type_uses_default(self, tmp_path: Path) -> None:
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", 'scope: private\nuse_count: "many"\n')
        )
        m = parse_memory(path)
        assert m is not None
        assert m.use_count == 0

    def test_signature_invalid_recomputed(self, tmp_path: Path) -> None:
        # Wrong-type signature → recompute (must satisfy non-empty invariant).
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace("scope: private\n", "scope: private\nsignature: 42\n")
        )
        m = parse_memory(path)
        assert m is not None
        assert m.signature == compute_memory_signature(
            m.body, MemoryType.PROJECT, MemoryScope.PRIVATE
        )

    def test_last_used_at_invalid_becomes_none(self, tmp_path: Path) -> None:
        # Optional field — failure to coerce is silent-default.
        path = tmp_path / "x.md"
        path.write_text(
            _VALID_FRONTMATTER.replace(
                "scope: private\n", 'scope: private\nlast_used_at: "bogus"\n'
            )
        )
        m = parse_memory(path)
        assert m is not None
        assert m.last_used_at is None
