# OpenHarness

[![CI](https://github.com/yangxiyue/build-my-own-harness/actions/workflows/ci.yml/badge.svg)](https://github.com/yangxiyue/build-my-own-harness/actions/workflows/ci.yml)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11-blue)
![License](https://img.shields.io/badge/license-MIT-green)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
![Type checked: mypy](https://img.shields.io/badge/type%20checked-mypy%20strict-1f5082)

> **A production-grade Python harness for LLM agents — built from scratch as a learning project.**
>
> 🚧 **Status**: Phase 1 (Foundation). CLI not yet usable.

---

## What is this

This repo is a learning project that re-implements a Claude-Code–style LLM agent harness in
Python, from scratch, to a production-grade quality bar.

The project is staged in 7 phases (see [ARCHITECTURE.md](./ARCHITECTURE.md)):

| Phase | Goal | Status |
|-------|------|--------|
| 0 | Architecture map: tier division, module dependency graph, scope boundary | ✅ |
| 1 | **Foundation + Hello LLM** — toolchain, data models, API client, CLI, Print mode | 🟡 in progress |
| 2 | Tool Loop — `BaseTool` / `ToolRegistry` / `run_query()` / Read+Write+Edit+Bash+Grep | ⏸ |
| 3 | Safety + Production Hardening — full permissions, hooks, retries, test coverage | ⏸ |
| 4 | Context Management — auto-compaction (microcompact + boundary detection) | ⏸ |
| 5 | Extensibility — MCP, slash commands, Skills/Plugins | ⏸ |
| 6 | One Advanced module (sub-agents / Docker sandbox / full compaction) | ⏸ |
| 7 | Polish + publish to PyPI | ⏸ |

The project specification (objective / commands / structure / style / testing / boundaries) lives
in [SPEC.md](./SPEC.md). The reverse-engineered OpenHarness reference (the project we draw
inspiration from) is in [REFERENCE.md](./REFERENCE.md). Per-decision trade-offs and rationale
live under [`decisions/`](./decisions). Per-module retrospectives live under
[`learnings/`](./learnings).

---

## Quick start

Requires Python ≥ 3.10 and [uv](https://docs.astral.sh/uv/).

```bash
# Install uv (one-time)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies (creates .venv automatically)
uv sync

# Smoke check — package can be imported and basic invariants hold
uv run python -m openharness
uv run pytest
```

---

## Development workflow

```bash
# Lint + format
uv run ruff check
uv run ruff format

# Type check (mypy strict mode)
uv run mypy --strict src/

# Tests with coverage
uv run pytest

# Install pre-commit hooks (one-time)
uv run pre-commit install

# Manually run all hooks
uv run pre-commit run --all-files
```

---

## Project structure

```
.
├── SPEC.md                   # Project specification (objective / commands / structure / style / testing / boundaries)
├── ARCHITECTURE.md           # Multi-phase strategy (tiers, dependency graph, phase ordering)
├── REFERENCE.md              # Reverse-engineered OpenHarness reference (study source, read-only)
├── pyproject.toml            # Single source of truth: deps, ruff, mypy, pytest
├── decisions/                # Decision records: trade-offs + rationale per module
├── learnings/                # Per-module retrospectives (Python patterns + product decisions)
├── tasks/plan.md             # Current phase plan
├── tasks/todo.md             # Running task list
├── docs/ideas/               # Blog drafts and ideation outputs
├── docs/learning/            # Living learning resources (book lists, etc.)
├── src/openharness/          # Source (src layout)
│   ├── __init__.py
│   ├── __main__.py           # `python -m openharness` entry
│   └── cli.py                # CLI entry point (Typer-based — Phase 1 Module 4)
├── tests/                    # pytest suite (asyncio_mode = auto)
├── .github/workflows/ci.yml  # Lint + type-check + test on Python 3.10 / 3.11
└── .pre-commit-config.yaml   # Fast hooks only (ruff + hygiene)
```

---

## Design decisions at a glance

| Concern | Choice | See |
|---------|--------|-----|
| Build / package mgmt | `uv` + `hatchling` | [decisions/01-scaffolding.md](./decisions/01-scaffolding.md) |
| Lint + format | `ruff` (replaces flake8/black/isort) | ↑ |
| Type checking | `mypy --strict` | ↑ |
| Test framework | `pytest` + `pytest-asyncio` + `pytest-cov` | ↑ |
| Pre-commit | enabled, **ruff only** (mypy/pytest in CI) | ↑ |
| CI | GitHub Actions, matrix Python 3.10 / 3.11 | [.github/workflows/ci.yml](./.github/workflows/ci.yml) |

---

## License

MIT — see [LICENSE](./LICENSE).
