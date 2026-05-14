"""Allow ``python -m openharness`` invocation.

Typer's ``app()`` already raises :class:`SystemExit` with the appropriate
exit code, so we just call :func:`main`; an explicit ``raise SystemExit``
wrapper is unnecessary and would never run.
"""

from __future__ import annotations

from openharness.cli import main

if __name__ == "__main__":  # pragma: no cover - module entry shim
    main()
