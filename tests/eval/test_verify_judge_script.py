from pathlib import Path

import pytest

from scripts import spike_verify_judge_eval


def test_replay_model_reads_project_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENHARNESS_MODEL", raising=False)
    (tmp_path / ".env").write_text("OPENHARNESS_MODEL=qwen3.7-max\n", encoding="utf-8")

    assert spike_verify_judge_eval._resolve_replay_model(tmp_path) == "qwen3.7-max"


def test_replay_model_requires_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENHARNESS_MODEL", raising=False)

    with pytest.raises(SystemExit, match="OPENHARNESS_MODEL"):
        spike_verify_judge_eval._resolve_replay_model(tmp_path)
