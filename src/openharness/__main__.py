"""Allow `python -m openharness` invocation."""

from __future__ import annotations

from openharness.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
