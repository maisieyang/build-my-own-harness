"""tool_choice eval consumer — deterministic core (D41 P0, decisions/41 §四).

Covers: dataset loader, three `=`-grade scorers, infer request assembly
(fake client — asserts production registry + system prompt are the subject
under test), cassette record/replay round-trip. Live-model behavior is out
of scope here (spike script + N≥4 stability profile per D41.5).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from openharness.eval.cassette import CassetteMissingError, CassetteStore
from openharness.eval.tool_choice import (
    ToolChoiceOutput,
    ToolChoiceSample,
    cassetted_infer_tool_choice,
    infer_tool_choice,
    load_tool_choice_dataset,
)
from openharness.eval.tool_choice_scorers import (
    ExpectedToolScorer,
    ForbiddenToolScorer,
    InputFieldScorer,
)
from openharness.protocols.content import TextBlock, ToolUseBlock
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.stream_events import ApiMessageCompleteEvent
from openharness.protocols.usage import UsageSnapshot
from openharness.tools import create_default_tool_registry

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from openharness.protocols.requests import ApiMessageRequest
    from openharness.protocols.stream_events import ApiStreamEvent

DATASET_PATH = Path(__file__).parents[2] / "evals" / "tool_choice" / "dataset.yaml"


def make_sample(
    *,
    expected_tool: str | None = "Grep",
    expected_input_contains: dict[str, list[str]] | None = None,
    forbidden_tools: tuple[str, ...] = (),
    project_instructions: str | None = None,
    tool_posture: str = "default",
) -> ToolChoiceSample:
    return ToolChoiceSample(
        case_id="synthetic",
        capability="TC1",
        shape="single-user-msg",
        messages=[
            ConversationMessage(role="user", content=[TextBlock(text="找 summarize")]),
        ],
        expected_tool=expected_tool,
        expected_input_contains=expected_input_contains or {},
        forbidden_tools=forbidden_tools,
        project_instructions=project_instructions,
        tool_posture=tool_posture,
        notes="",
    )


def make_output(*tool_uses: ToolUseBlock, text: str = "") -> ToolChoiceOutput:
    return ToolChoiceOutput(tool_uses=tuple(tool_uses), text=text, stop_reason="tool_use")


def grep_call(pattern: str = "summarize", **extra: str) -> ToolUseBlock:
    return ToolUseBlock(id="c1", name="Grep", input={"pattern": pattern, **extra})


class TestLoadDataset:
    def test_loads_twelve_ratified_samples_including_permission_case(self) -> None:
        samples = load_tool_choice_dataset(DATASET_PATH)

        assert len(samples) == 12
        by_id = {s.case_id: s for s in samples}
        project_context = by_id["TC3-project-test-command"]
        assert project_context.expected_tool == "Bash"
        assert "uv run pytest" in (project_context.project_instructions or "")
        planted = by_id["TC4-unknown-tool-recovery"]
        assert planted.expected_tool == "Grep"
        assert "Search" in planted.forbidden_tools
        # planted history: user → assistant(tool_use Search) → user(tool_result error)
        assert len(planted.messages) == 3
        restraint = by_id["TC5-greeting-no-tool"]
        assert restraint.expected_tool is None
        plan_cases = [sample for sample in samples if sample.capability == "TC6"]
        assert [sample.case_id for sample in plan_cases] == [
            "TC6-plan-read",
            "TC6-plan-mutation-restraint",
        ]
        assert all(sample.tool_posture == "plan" for sample in plan_cases)
        mutation_restraint = plan_cases[1]
        assert mutation_restraint.expected_tool == "Read"
        assert mutation_restraint.expected_input_contains == {"path": ["README.md"]}
        assert mutation_restraint.forbidden_tools == ("Write", "Edit", "Bash", "Agent")
        permission_case = by_id["TC7-write-external-path-for-review"]
        assert permission_case.expected_tool == "Write"
        assert permission_case.expected_input_contains == {
            "path": ["/tmp/openharness-permission-dogfood.txt"],
            "content": ["permission dogfood"],
        }
        assert permission_case.forbidden_tools == ("Bash",)

    def test_missing_required_field_raises(self, tmp_path: Path) -> None:
        bad = tmp_path / "bad.yaml"
        bad.write_text("samples:\n  - case_id: x\n    capability: TC1\n", encoding="utf-8")
        with pytest.raises(KeyError):
            load_tool_choice_dataset(bad)


class TestExpectedToolScorer:
    async def test_pass_on_expected_first_tool(self) -> None:
        score = await ExpectedToolScorer().score(make_sample(), make_output(grep_call()))
        assert score.value == 1.0

    async def test_fail_on_wrong_tool(self) -> None:
        wrong = ToolUseBlock(id="c1", name="Bash", input={"command": "grep -r summarize"})
        score = await ExpectedToolScorer().score(make_sample(), make_output(wrong))
        assert score.value == 0.0
        assert "Bash" in score.reason

    async def test_fail_when_no_tool_called_but_expected(self) -> None:
        score = await ExpectedToolScorer().score(make_sample(), make_output())
        assert score.value == 0.0

    async def test_restraint_pass_when_none_expected_and_none_called(self) -> None:
        sample = make_sample(expected_tool=None)
        score = await ExpectedToolScorer().score(sample, make_output(text="你好!"))
        assert score.value == 1.0

    async def test_restraint_fail_when_none_expected_but_tool_called(self) -> None:
        sample = make_sample(expected_tool=None)
        score = await ExpectedToolScorer().score(sample, make_output(grep_call()))
        assert score.value == 0.0


class TestForbiddenToolScorer:
    async def test_fail_when_forbidden_tool_used(self) -> None:
        sample = make_sample(expected_tool="Grep", forbidden_tools=("Bash",))
        bash = ToolUseBlock(id="c2", name="Bash", input={"command": "grep -r x"})
        score = await ForbiddenToolScorer().score(sample, make_output(grep_call(), bash))
        assert score.value == 0.0

    async def test_vacuous_pass_on_empty_forbidden_list(self) -> None:
        score = await ForbiddenToolScorer().score(make_sample(), make_output(grep_call()))
        assert score.value == 1.0


class TestInputFieldScorer:
    async def test_pass_when_all_fields_hit_any_of(self) -> None:
        sample = make_sample(expected_input_contains={"pattern": ["summarize"], "path": ["src"]})
        score = await InputFieldScorer().score(
            sample, make_output(grep_call(path="src/openharness"))
        )
        assert score.value == 1.0

    async def test_case_insensitive_match(self) -> None:
        sample = make_sample(expected_input_contains={"pattern": ["SUMMARIZE"]})
        score = await InputFieldScorer().score(sample, make_output(grep_call()))
        assert score.value == 1.0

    async def test_fail_on_missing_field(self) -> None:
        sample = make_sample(expected_input_contains={"path": ["tests"]})
        score = await InputFieldScorer().score(sample, make_output(grep_call()))
        assert score.value == 0.0
        assert "path" in score.reason

    async def test_fail_when_expected_tool_never_called(self) -> None:
        sample = make_sample(expected_input_contains={"pattern": ["summarize"]})
        wrong = ToolUseBlock(id="c1", name="Bash", input={"command": "x"})
        score = await InputFieldScorer().score(sample, make_output(wrong))
        assert score.value == 0.0

    async def test_vacuous_pass_on_empty_expectation(self) -> None:
        score = await InputFieldScorer().score(make_sample(), make_output(grep_call()))
        assert score.value == 1.0


class FakeApiClient:
    """Captures the request; replies with one canned complete event."""

    def __init__(self, reply_blocks: list[ToolUseBlock | TextBlock]) -> None:
        self.reply_blocks = reply_blocks
        self.requests: list[ApiMessageRequest] = []

    def stream_message(self, request: ApiMessageRequest) -> AsyncIterator[ApiStreamEvent]:
        self.requests.append(request)

        async def _gen() -> AsyncIterator[ApiStreamEvent]:
            yield ApiMessageCompleteEvent(
                message=ConversationMessage(role="assistant", content=list(self.reply_blocks)),
                usage=UsageSnapshot(input_tokens=10, output_tokens=5),
                stop_reason="tool_use"
                if any(isinstance(b, ToolUseBlock) for b in self.reply_blocks)
                else "end_turn",
            )

        return _gen()


class TestInferToolChoice:
    async def test_subject_under_test_is_production_registry_and_prompt(self) -> None:
        client = FakeApiClient([grep_call()])
        sample = make_sample()

        output = await infer_tool_choice(sample=sample, api_client=client, model="qwen-max")

        assert len(client.requests) == 1  # single-shot: exactly one round-trip
        request = client.requests[0]
        expected_names = [t.name for t in create_default_tool_registry().to_api_schema()]
        assert [t.name for t in request.tools or []] == expected_names
        assert request.system is not None and "## Tools" in request.system
        # no local-machine leakage in the eval system prompt (D40.3 hygiene)
        assert "/Users/" not in request.system
        assert output.tool_uses[0].name == "Grep"

    async def test_project_instructions_are_injected_for_the_case(self) -> None:
        client = FakeApiClient(
            [ToolUseBlock(id="c1", name="Bash", input={"command": "uv run pytest -q"})]
        )
        sample = make_sample(
            expected_tool="Bash",
            project_instructions="Run tests with uv run pytest -q.",
        )

        await infer_tool_choice(sample=sample, api_client=client, model="qwen-max")

        system = client.requests[0].system
        assert system is not None
        assert "## Project Instructions" in system
        assert "Run tests with uv run pytest -q." in system

    async def test_plan_subject_exposes_only_read_tools_and_plan_posture(self) -> None:
        client = FakeApiClient([grep_call()])
        sample = make_sample(tool_posture="plan")

        await infer_tool_choice(sample=sample, api_client=client, model="qwen-max")

        request = client.requests[0]
        assert [tool.name for tool in request.tools or []] == ["Read", "Grep"]
        assert request.system is not None
        assert "You are in plan mode" in request.system

    async def test_planted_history_passed_through_verbatim(self) -> None:
        client = FakeApiClient([grep_call("TODO")])
        samples = load_tool_choice_dataset(DATASET_PATH)
        planted = next(s for s in samples if s.case_id == "TC4-unknown-tool-recovery")

        await infer_tool_choice(sample=planted, api_client=client, model="qwen-max")

        sent = client.requests[0].messages
        assert len(sent) == 3
        assert sent[1].role == "assistant"


class TestCassetteRoundTrip:
    async def test_record_then_replay_without_client(self, tmp_path: Path) -> None:
        store = CassetteStore(tmp_path)
        client = FakeApiClient([grep_call()])
        sample = make_sample()

        recorded = await cassetted_infer_tool_choice(
            sample=sample,
            api_client=client,
            model="m1",
            cassette_mode="record",
            cassette_store=store,
        )
        replayed = await cassetted_infer_tool_choice(
            sample=sample,
            api_client=None,
            model="m1",
            cassette_mode="replay",
            cassette_store=store,
        )

        assert replayed == recorded
        assert replayed.tool_uses[0].name == "Grep"

    async def test_replay_missing_cassette_raises(self, tmp_path: Path) -> None:
        with pytest.raises(CassetteMissingError):
            await cassetted_infer_tool_choice(
                sample=make_sample(),
                api_client=None,
                model="m1",
                cassette_mode="replay",
                cassette_store=CassetteStore(tmp_path),
            )
