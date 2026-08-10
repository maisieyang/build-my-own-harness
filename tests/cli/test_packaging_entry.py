"""Packaging exposes one executable name and no Python module shims."""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[2]


def test_only_oh_console_script_is_declared() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert project["scripts"] == {"oh": "openharness.cli:main"}


def test_package_module_entry_shim_is_absent() -> None:
    assert not (ROOT / "src" / "openharness" / "__main__.py").exists()
