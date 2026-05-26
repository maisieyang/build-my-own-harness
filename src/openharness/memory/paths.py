"""Memory storage path resolver — P10-T1.1c.

Per ``decisions/25-phase-10-boundary.md`` D28.1: memory storage lives
**outside the repo** at
``~/.openharness/memory/<basename(cwd)>-<sha1(cwd)[:12]>/``.

Three properties hold by construction:

1. **Same cwd → same dir** — deterministic via SHA-1 hashing of the
   resolved (symlink-followed) absolute path.
2. **Different cwd with same basename → distinct dirs** — the
   12-character SHA-1 suffix collides only with 2^48 probability per
   pair, which is effectively never for any realistic project count.
3. **No accidental commit** — the dir lives under the user's home
   directory, not the working tree. Even if the user runs
   ``git add .openharness/``, nothing memory-related is in the repo.

The directory is **not** created here; callers create lazily on first
write so a non-existent dir on read returns an empty store rather than
mkdir-ing an empty directory the user never asked for.
"""

from __future__ import annotations

from hashlib import sha1
from pathlib import Path


def get_project_memory_dir(cwd: str | Path) -> Path:
    """Resolve the per-project memory directory for ``cwd``.

    The path is computed from the **resolved** absolute path of ``cwd``
    so symlinks point to the same memory dir as the canonical location
    (running ``oh ask`` from ``/private/var/foo`` and ``/var/foo``
    — where ``/var`` symlinks to ``/private/var`` on macOS — shares
    memory, not splits it).

    Format:
    ``~/.openharness/memory/<basename>-<sha1(resolved_cwd)[:12]>/``

    Examples:

    - ``cwd=/Users/alice/projects/payment`` →
      ``~/.openharness/memory/payment-<sha1>/``
    - ``cwd=/tmp/scratch`` →
      ``~/.openharness/memory/scratch-<sha1>/``
    - ``cwd=/`` (the filesystem root, basename is empty) →
      ``~/.openharness/memory/-<sha1>/``

    This function does NOT call :meth:`Path.mkdir`. The directory may
    not exist yet — that's expected on first read for a project that
    hasn't written any memories. Phase 11 writers create the dir
    lazily on first :func:`extract_memories_from_turn` output.

    :func:`Path.home` is evaluated **at call time**, not at module
    import — so tests' ``monkeypatch.setenv("HOME", ...)`` isolation
    fixture (``tests/conftest.py``) takes effect. The same reason
    ``cli.py`` builds ``Path.home() / ".openharness" / "commands"``
    inline at bootstrap rather than caching at module scope.
    """
    resolved = Path(cwd).resolve()
    # SHA-1 over the resolved absolute path string. 12 hex chars = 48
    # bits = collision probability ~2^-48 per pair (effectively zero
    # for any realistic project count). Matches HKUDS upstream.
    digest = sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    return Path.home() / ".openharness" / "memory" / f"{resolved.name}-{digest}"
