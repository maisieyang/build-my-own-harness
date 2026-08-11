"""T6 — ``oh bench swebench`` wiring: sub-app registered on the main app,
commands visible, run fails helpfully without a fetched dataset, model
pinned via the same settings chain the child ``oh ask`` would use."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from openharness.cli import app
from openharness.swebench.cli import _pin_config

runner = CliRunner()


class TestBenchWiring:
    def test_bench_swebench_help_lists_commands(self) -> None:
        result = runner.invoke(app, ["dev", "bench", "swebench", "--help"])

        assert result.exit_code == 0
        assert "fetch" in result.output
        assert "run" in result.output

    def test_run_without_dataset_points_to_fetch(self, tmp_path: object) -> None:
        result = runner.invoke(
            app,
            ["dev", "bench", "swebench", "run", "--dataset-file", "does/not/exist.jsonl"],
        )

        assert result.exit_code == 1
        assert "fetch" in result.output


class TestPinConfig:
    """D40.8 fidelity: a batch pins model AND endpoint/key explicitly —
    resolved once at bench cwd, injected into the child via env vars (which
    beat .env files). The child runs with workspace cwd, where the project
    .env is invisible; without pinning, config silently drifts to the
    user-global layer and the records lie about what ran."""

    def _plant_user_global_env(self) -> None:
        # conftest points HOME at an isolated tmp dir — plant the
        # user-global .env exactly where the child `oh ask` would read it.
        env_file = Path.home() / ".openharness" / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(
            "OPENHARNESS_API_KEY=sk-test\n"
            "OPENHARNESS_BASE_URL=https://example.invalid/v1\n"
            "OPENHARNESS_MODEL=pinned-model-from-env\n",
            encoding="utf-8",
        )

    def test_resolves_model_and_endpoint_from_settings_chain(self) -> None:
        self._plant_user_global_env()

        model, overrides = _pin_config(None)

        assert model == "pinned-model-from-env"
        assert overrides is not None
        assert overrides["OPENHARNESS_MODEL"] == "pinned-model-from-env"
        assert overrides["OPENHARNESS_BASE_URL"] == "https://example.invalid/v1"
        assert overrides["OPENHARNESS_API_KEY"] == "sk-test"

    def test_explicit_model_wins_but_endpoint_still_pinned(self) -> None:
        self._plant_user_global_env()

        model, overrides = _pin_config("deepseek-v3.2")

        assert model == "deepseek-v3.2"
        assert overrides is not None
        assert overrides["OPENHARNESS_MODEL"] == "deepseek-v3.2"
        assert overrides["OPENHARNESS_BASE_URL"] == "https://example.invalid/v1"

    def test_extra_body_forwarded_to_child_env(self) -> None:
        """RUNLOG 节点 8: extra_body (e.g. enable_thinking=false) must ride
        the same pin channel as key/model — the child's workspace cwd can't
        see the project .env, so an unforwarded extra_body silently vanishes
        and the batch runs in a different (thinking-on) condition."""
        env_file = Path.home() / ".openharness" / ".env"
        env_file.parent.mkdir(parents=True, exist_ok=True)
        env_file.write_text(
            "OPENHARNESS_API_KEY=sk-test\n"
            "OPENHARNESS_BASE_URL=https://example.invalid/v1\n"
            "OPENHARNESS_MODEL=m\n"
            'OPENHARNESS_EXTRA_BODY={"enable_thinking": false}\n',
            encoding="utf-8",
        )

        _, overrides = _pin_config(None)

        assert overrides is not None
        assert json.loads(overrides["OPENHARNESS_EXTRA_BODY"]) == {"enable_thinking": False}

    def test_unresolvable_settings_fall_back_to_no_overrides(self) -> None:
        # no .env anywhere, no OPENHARNESS_* vars (conftest isolation):
        # the child then surfaces the real configuration error itself.
        model, overrides = _pin_config("explicit-model")

        assert model == "explicit-model"
        assert overrides is None
