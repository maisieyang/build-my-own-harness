""":class:`Memory` dataclass + enums + signature helper — P10-T1.1a.

Per ``decisions/25-phase-10-boundary.md`` D28.3: a memory is a markdown
file with YAML frontmatter declaring 14 fields plus a free-form body.
Required fields are ``id`` / ``name`` / ``description`` / ``type`` /
``scope`` / ``created_at`` / ``updated_at`` / ``signature``;
``ttl_days`` / ``disabled`` / ``supersedes`` / ``importance`` / ``tags``
/ ``use_count`` / ``last_used_at`` carry defaults.

::

    ---
    id: 01HXXXXXXXX
    name: stripe-sdk-version
    description: Project uses Stripe SDK 8.x with the legacy API
    type: project
    scope: private
    created_at: 2026-05-26T10:00:00+00:00
    updated_at: 2026-05-26T10:00:00+00:00
    signature: <sha256-hex>
    importance: 0.5
    tags: ["payment", "stripe"]
    ---

    Body content here. Markdown. Free-form.

This module owns ONLY the dataclass + enum + signature helper (1a).
:func:`parse_memory` from frontmatter dict lands in 1b.

D28.4 — the dataclass satisfies :class:`MarkdownDocument` Protocol via
``name`` + ``source_path`` so :class:`FilesystemMemoryStore` (T2) can
host it via the shared :class:`FilesystemMarkdownStore[T]` primitive.
"""

from __future__ import annotations

import enum
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from openharness.markdown_store import NAME_PATTERN, read_frontmatter_dict
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from pathlib import Path

_logger = get_logger("memory")


class MemoryType(str, enum.Enum):
    """The four memory categories per D28.3.

    Mirrors Claude Code's auto-memory taxonomy:

    - ``user`` — facts about the person (role, expertise, preferences)
    - ``feedback`` — corrections / preferences the user gave explicitly
    - ``project`` — facts about this codebase / project / domain
    - ``reference`` — pointers to external systems (URLs, doc paths)

    ``str + Enum`` mixin so ``MemoryType.PROJECT == "project"`` and YAML
    serialization is the string value directly (no ``MemoryType.PROJECT``
    repr leaking into frontmatter).
    """

    USER = "user"
    FEEDBACK = "feedback"
    PROJECT = "project"
    REFERENCE = "reference"


class MemoryScope(str, enum.Enum):
    """Memory scope. **Phase 11 D29.10 adds TEAM** alongside extraction's
    write path (since secret scanning belongs in the write layer).

    - ``private`` (Phase 10) — local-only memories. No scanning, no
      sharing. Lives at ``<storage-dir>/`` root.
    - ``team`` (Phase 11) — shared across team members. Lives under
      ``<storage-dir>/team/`` subdirectory. Subject to regex-based
      secret scanning at write time via
      :func:`openharness.memory.team.check_team_memory_secrets` —
      records with detected secrets are silently dropped + warned.

    ``str + Enum`` mixin so YAML serialization is the string value
    directly. :func:`parse_memory` accepts both values; downstream
    code routes by ``scope is MemoryScope.TEAM``.
    """

    PRIVATE = "private"
    TEAM = "team"


@dataclass(frozen=True)
class Memory:
    """A single durable memory file discovered from the filesystem.

    Constructed by :func:`parse_memory` (1b);the dataclass itself
    trusts its inputs and :meth:`__post_init__` only re-asserts the
    load-bearing invariants so tests can build fixtures without
    round-tripping through markdown.

    Field order: required-without-defaults first (positional-safe),
    then optional-with-defaults. Frozen so instances are hashable
    + safe to share across the relevance + injection pipeline (T3 +
    T4).

    Default-mutable fields use immutable :class:`tuple` (not
    :class:`list`) — required for frozen dataclasses without
    ``field(default_factory=list)`` boilerplate, and prevents accidental
    mutation that would silently change a shared default across
    instances.

    **Phase 17 D37.1**: ``id`` moved to the optional section as
    ``str | None = None``. ``parse_memory`` always provides an id —
    either from frontmatter or auto-generated via
    ``sha1(name + path)`` — so loaded memories still have a stable id.
    Allowing None at the dataclass level lets the Claude-Code memory
    write contract (D36.10) ship frontmatter without an id field.
    """

    # Identity — required (name + description) immutable across rewrites
    name: str
    description: str

    # Classification — required enums
    type: MemoryType
    scope: MemoryScope

    # Timestamps — required (created_at + updated_at); last_used_at
    # is optional (None = "never injected via relevance yet")
    created_at: datetime
    updated_at: datetime

    # Body + integrity — required (signature is computed by parser if
    # absent from the file; see :func:`compute_memory_signature`)
    body: str
    signature: str

    # Source location — for :class:`MarkdownDocument` Protocol
    # (P10-T2 will read ``.name`` + ``.source_path``)
    source_path: Path

    # Identity — Phase 17 D37.1: optional. parse_memory generates from
    # ``sha1(name + str(path.resolve()))[:16]`` when frontmatter omits it.
    id: str | None = None

    # Lifecycle — defaults
    ttl_days: int | None = None
    disabled: bool = False
    supersedes: tuple[str, ...] = ()

    # Relevance tuning — defaults
    importance: float = 0.5
    tags: tuple[str, ...] = ()
    use_count: int = 0
    last_used_at: datetime | None = None

    def __post_init__(self) -> None:
        # name: shared safe-identifier regex from markdown_store/
        # (same value as Command / Skill / Bundle / PluginManifest)
        if not NAME_PATTERN.match(self.name):
            raise ValueError(
                f"invalid memory name {self.name!r}: must match "
                f"{NAME_PATTERN.pattern}"
                " (alphanumeric + ``_-``, starts with letter)"
            )
        # id: Phase 17 D37.1 — optional at the dataclass level. If
        # provided, must be non-empty (catches ``id: ''`` frontmatter
        # bugs without rejecting CC-style files that omit the field).
        if self.id is not None and not self.id.strip():
            raise ValueError(f"memory {self.name!r}: id, when provided, must be non-empty")
        # description: non-empty after strip (matches Command / Skill)
        if not self.description.strip():
            raise ValueError(f"memory {self.name!r}: description must be non-empty")
        # signature: non-empty (computed by parser if absent)
        if not self.signature or not self.signature.strip():
            raise ValueError(f"memory {self.name!r}: signature must be non-empty")
        # importance: 0.0-1.0 range matters because relevance scoring
        # (T3) multiplies it into score; out-of-range values would skew
        # ranking. Reject at construction.
        if not 0.0 <= self.importance <= 1.0:
            raise ValueError(
                f"memory {self.name!r}: importance {self.importance} out of range [0.0, 1.0]"
            )
        # use_count: never negative; capped at 5 inside relevance scoring
        # (D28.7) to prevent popularity feedback loops, but the field
        # itself can grow unbounded
        if self.use_count < 0:
            raise ValueError(
                f"memory {self.name!r}: use_count {self.use_count} must be non-negative"
            )
        # ttl_days: positive or None; zero / negative would mean
        # "already expired", which we should reject at write time rather
        # than store as a valid memory
        if self.ttl_days is not None and self.ttl_days <= 0:
            raise ValueError(
                f"memory {self.name!r}: ttl_days {self.ttl_days} must be positive or None"
            )


def compute_memory_signature(
    body: str,
    memory_type: MemoryType,
    scope: MemoryScope,
) -> str:
    """SHA-256 hex digest over ``(body, type, scope)`` — D28.3 dedup primitive.

    Used by Phase 11 extraction for content-based deduplication: a memory
    with the same body + type + scope hash is considered the same fact and
    should overwrite the existing file rather than create a new one. Phase
    10 only computes it (1b uses this when the parsed frontmatter lacks
    ``signature``) so the field is always present on :class:`Memory`.

    Whitespace at body edges is stripped before hashing so cosmetic
    differences (trailing newlines from editors) don't fragment dedup.
    Null-byte separators between fields prevent cross-field collisions
    (e.g. ``body="x" type="y"`` vs ``body="xy" type=""``).
    """
    h = hashlib.sha256()
    h.update(body.strip().encode("utf-8"))
    h.update(b"\0")
    h.update(memory_type.value.encode("utf-8"))
    h.update(b"\0")
    h.update(scope.value.encode("utf-8"))
    return h.hexdigest()


# ---------------------------------------------------------------------------
# parse_memory — frontmatter → Memory (P10-T1.1b)
# ---------------------------------------------------------------------------


def _parse_datetime_value(
    value: Any,
    *,
    field_name: str,
    name: str,
    path: Path,
) -> datetime | None:
    """Coerce a non-None YAML value to a timezone-aware :class:`datetime`.

    Handles two shapes ``yaml.safe_load`` produces for ISO 8601:

    - ``datetime`` (when YAML parses ``2026-05-26T10:00:00Z`` natively)
      — may be naive if no offset in the source; we fill ``UTC``.
    - ``str`` (when frontmatter quotes the timestamp) — we parse via
      :meth:`datetime.fromisoformat` and fill ``UTC`` on naive results.

    Returns ``None`` + warning log on any other shape or unparseable
    string. **Caller must check for ``None`` upstream** (this helper
    treats ``None`` input as "wrong type" because required-field
    presence is a separate concern handled in :func:`parse_memory`).
    """
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            _logger.warning(
                "memory_invalid_timestamp",
                source_path=str(path),
                name=name,
                field=field_name,
                value=value,
            )
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    _logger.warning(
        "memory_invalid_timestamp_type",
        source_path=str(path),
        name=name,
        field=field_name,
        got=type(value).__name__,
    )
    return None


def _coerce_string_tuple(
    value: Any,
    *,
    field_name: str,
    name: str,
    path: Path,
) -> tuple[str, ...]:
    """Coerce a YAML list-of-strings to ``tuple[str, ...]``.

    ``None`` → ``()`` (field omitted is fine, defaults apply).
    Non-list → ``()`` + warning (frontmatter has the wrong shape).
    Non-string items inside the list → dropped + per-item warning
    (graceful degradation: load the valid items, complain about bad ones).
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        _logger.warning(
            "memory_invalid_list",
            source_path=str(path),
            name=name,
            field=field_name,
            got=type(value).__name__,
        )
        return ()
    out: list[str] = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        else:
            _logger.warning(
                "memory_invalid_list_item",
                source_path=str(path),
                name=name,
                field=field_name,
                got=type(item).__name__,
            )
    return tuple(out)


def parse_memory(path: Path) -> Memory | None:
    """Read a memory markdown file and build a :class:`Memory`.

    Returns ``None`` and emits a warning log on **required-field**
    errors (file read failure, malformed YAML, missing ``name`` /
    ``description`` / ``type``, invalid enum value, name regex
    violation). The store's discovery loop treats ``None`` as "skip"
    so one bad memory never prevents others from loading.

    **Phase 17 D37.1 + D37.2 schema acceptance**: the parser accepts
    both the legacy Phase 10/11 frontmatter (14 fields) AND the
    Claude-Code-style frontmatter mandated by D36.10 (name +
    description + metadata.type only, all other Phase 10 fields
    omitted). When a Phase 10 field is missing:

    - ``id`` → auto-generated as ``sha1(name + str(path.resolve()))[:16]``.
      Deterministic across reparses of the same file. Phase 11-written
      memories that DID carry an id keep theirs (frontmatter wins).
    - ``scope`` → defaults to ``MemoryScope.PRIVATE``.
    - ``created_at`` → defaults to ``datetime.now(timezone.utc)``.
    - ``updated_at`` → defaults to ``created_at`` value.
    - ``signature`` → computed via :func:`compute_memory_signature`
      (unchanged from Phase 10 behavior).
    - ``type`` is read from either the top-level ``type:`` field (Phase 10)
      OR the nested ``metadata.type`` (D36.10 CC schema); top-level
      wins when both are present.

    Optional fields with wrong type → graceful degrade (default + warn);
    truly required fields with wrong type → skip entirely (None + warn).
    Same skip-not-fail discipline as :func:`parse_skill` / :func:`parse_command`.
    """
    # P17-T3 (D37.5): MEMORY.md is reserved as the LLM-visible index
    # file (D36.10), never a memory body. Skip silently — the file
    # isn't malformed, it's just not what we're looking for. The
    # store's ``_merge_dir`` loop continues past a ``None`` return,
    # same as for any other parse-rejected file.
    if path.name == "MEMORY.md":
        return None
    parsed, body = read_frontmatter_dict(path, logger_name="memory")
    if parsed is None:
        return None

    # ---- Required fields (no auto-fill — D36.10 contract) ----

    name_raw = parsed.get("name")
    if not isinstance(name_raw, str) or not name_raw:
        _logger.warning("memory_missing_name", source_path=str(path))
        return None
    name: str = name_raw  # locked for downstream log labels

    description_raw = parsed.get("description")
    if not isinstance(description_raw, str) or not description_raw.strip():
        _logger.warning("memory_missing_description", source_path=str(path), name=name)
        return None

    # ``type`` accepted from both Phase 10 top-level and D36.10
    # ``metadata.type`` nested location. Top-level wins when both
    # exist (Phase 10 legacy files take precedence over migration).
    type_raw = parsed.get("type")
    if type_raw is None:
        metadata_raw = parsed.get("metadata")
        if isinstance(metadata_raw, dict):
            type_raw = metadata_raw.get("type")
    if not isinstance(type_raw, str):
        _logger.warning("memory_missing_type", source_path=str(path), name=name)
        return None
    try:
        memory_type = MemoryType(type_raw)
    except ValueError:
        _logger.warning(
            "memory_invalid_type",
            source_path=str(path),
            name=name,
            value=type_raw,
            valid=[t.value for t in MemoryType],
        )
        return None

    # ---- Phase 17 D37.2 auto-fill: optional Phase 10/11 fields ----
    # ``id``: frontmatter wins; else sha1(name + resolved path)[:16].
    id_raw = parsed.get("id")
    if isinstance(id_raw, str) and id_raw.strip():
        memory_id: str = id_raw
    else:
        memory_id = hashlib.sha1(
            (name + str(path.resolve())).encode("utf-8"),
            usedforsecurity=False,
        ).hexdigest()[:16]

    # ``scope``: default PRIVATE if missing. Phase 10/11 files that
    # carry ``scope: team`` still resolve correctly via the enum
    # constructor.
    scope_raw = parsed.get("scope")
    if scope_raw is None:
        scope = MemoryScope.PRIVATE
    elif not isinstance(scope_raw, str):
        _logger.warning(
            "memory_invalid_scope",
            source_path=str(path),
            name=name,
            value=scope_raw,
            phase_10_supported=[s.value for s in MemoryScope],
        )
        return None
    else:
        try:
            scope = MemoryScope(scope_raw)
        except ValueError:
            _logger.warning(
                "memory_invalid_scope",
                source_path=str(path),
                name=name,
                value=scope_raw,
                phase_10_supported=[s.value for s in MemoryScope],
            )
            return None

    # ``created_at`` / ``updated_at``: default to now() / created_at
    # when frontmatter omits them. Wrong-type values that won't parse
    # still skip (existing strict path).
    now = datetime.now(timezone.utc)
    created_raw = parsed.get("created_at")
    if created_raw is None:
        created_at = now
    else:
        parsed_created = _parse_datetime_value(
            created_raw, field_name="created_at", name=name, path=path
        )
        if parsed_created is None:
            return None
        created_at = parsed_created

    updated_raw = parsed.get("updated_at")
    if updated_raw is None:
        updated_at = created_at
    else:
        parsed_updated = _parse_datetime_value(
            updated_raw, field_name="updated_at", name=name, path=path
        )
        if parsed_updated is None:
            return None
        updated_at = parsed_updated

    # ---- Optional fields (wrong type → log + use default) ----

    # signature: compute if absent or wrong type — the dataclass requires
    # non-empty so we MUST produce something. Computing it preserves the
    # ability to dedup hand-written memories by content.
    signature_raw = parsed.get("signature")
    signature: str
    if isinstance(signature_raw, str) and signature_raw.strip():
        signature = signature_raw
    else:
        if signature_raw is not None:
            _logger.warning(
                "memory_invalid_signature",
                source_path=str(path),
                name=name,
                got=type(signature_raw).__name__,
            )
        signature = compute_memory_signature(body, memory_type, scope)

    ttl_raw = parsed.get("ttl_days")
    ttl_days: int | None
    if ttl_raw is None:
        ttl_days = None
    elif isinstance(ttl_raw, int) and not isinstance(ttl_raw, bool):
        # ``bool`` is a subclass of ``int`` in Python — explicitly exclude
        # so ``ttl_days: true`` doesn't silently become ``1``.
        ttl_days = ttl_raw
    else:
        _logger.warning(
            "memory_invalid_ttl_days",
            source_path=str(path),
            name=name,
            got=type(ttl_raw).__name__,
        )
        ttl_days = None

    disabled_raw = parsed.get("disabled", False)
    disabled: bool
    if isinstance(disabled_raw, bool):
        disabled = disabled_raw
    else:
        _logger.warning(
            "memory_invalid_disabled",
            source_path=str(path),
            name=name,
            got=type(disabled_raw).__name__,
        )
        disabled = False

    supersedes = _coerce_string_tuple(
        parsed.get("supersedes"), field_name="supersedes", name=name, path=path
    )

    importance_raw = parsed.get("importance", 0.5)
    importance: float
    if isinstance(importance_raw, (int, float)) and not isinstance(importance_raw, bool):
        importance = float(importance_raw)
    else:
        _logger.warning(
            "memory_invalid_importance",
            source_path=str(path),
            name=name,
            got=type(importance_raw).__name__,
        )
        importance = 0.5

    tags = _coerce_string_tuple(parsed.get("tags"), field_name="tags", name=name, path=path)

    use_count_raw = parsed.get("use_count", 0)
    use_count: int
    if isinstance(use_count_raw, int) and not isinstance(use_count_raw, bool):
        use_count = use_count_raw
    else:
        _logger.warning(
            "memory_invalid_use_count",
            source_path=str(path),
            name=name,
            got=type(use_count_raw).__name__,
        )
        use_count = 0

    last_used_raw = parsed.get("last_used_at")
    last_used_at: datetime | None
    if last_used_raw is None:
        last_used_at = None
    else:
        # Optional — failure to coerce drops to None (already logged inside
        # the helper); the memory itself still loads.
        last_used_at = _parse_datetime_value(
            last_used_raw, field_name="last_used_at", name=name, path=path
        )

    # ---- Dataclass construction + __post_init__ re-validation ----

    try:
        return Memory(
            id=memory_id,
            name=name,
            description=description_raw,
            type=memory_type,
            scope=scope,
            created_at=created_at,
            updated_at=updated_at,
            body=body,
            signature=signature,
            source_path=path,
            ttl_days=ttl_days,
            disabled=disabled,
            supersedes=supersedes,
            importance=importance,
            tags=tags,
            use_count=use_count,
            last_used_at=last_used_at,
        )
    except ValueError as exc:
        # ``__post_init__`` caught a violation (name regex / out-of-range
        # importance / negative use_count / non-positive ttl). Log + skip —
        # never crash the discovery loop.
        _logger.warning(
            "memory_validation_failed",
            source_path=str(path),
            name=name,
            error=str(exc),
        )
        return None
