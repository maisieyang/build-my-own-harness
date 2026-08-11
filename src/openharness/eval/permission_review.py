"""Permission-review eval consumer for the production exact reviewer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import yaml

from openharness.eval.cassette import (
    CassetteKey,
    CassetteMissingError,
    CassetteMode,
    CassetteStore,
)
from openharness.eval.selection import select_cases
from openharness.execution import BoundaryViolation
from openharness.permissions import (
    ExternalToolPolicy,
    PermissionDelta,
    PermissionDeltaRequest,
    PermissionResolutionStatus,
    PermissionReviewDecision,
    PermissionReviewVerdict,
    PermissionRuntime,
    RuntimePermissionProfile,
    workspace_runtime_profile,
)
from openharness.services.permission_reviewer import LlmPermissionReviewer

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.api import SupportsStreamingMessages
    from openharness.eval.protocol import Score


@dataclass(frozen=True)
class PermissionReviewSample:
    case_id: str
    capability: str
    authorization_context: tuple[str, ...]
    tool_name: str
    final_arguments: dict[str, Any]
    surface: str
    effect_kind: str
    trust_source: str
    server_identity: str | None
    hard_deny: bool
    crossing_evidence: str
    data_sources: tuple[str, ...]
    data_destinations: tuple[str, ...]
    expected_decision: PermissionReviewDecision
    review_expected: bool
    notes: str


@dataclass(frozen=True)
class PermissionReviewOutput:
    decision: PermissionReviewDecision
    reason: str
    review_called: bool
    resolution_status: PermissionResolutionStatus


def load_permission_review_dataset(path: Path) -> list[PermissionReviewSample]:
    data: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        PermissionReviewSample(
            case_id=entry["case_id"],
            capability=entry["capability"],
            authorization_context=tuple(entry["authorization_context"]),
            tool_name=entry["tool_name"],
            final_arguments=dict(entry["final_arguments"]),
            surface=entry["surface"],
            effect_kind=entry["effect_kind"],
            trust_source=entry["trust_source"],
            server_identity=entry.get("server_identity"),
            hard_deny=bool(entry.get("hard_deny", False)),
            crossing_evidence=entry["crossing_evidence"],
            data_sources=tuple(entry.get("data_sources", ())),
            data_destinations=tuple(entry.get("data_destinations", ())),
            expected_decision=PermissionReviewDecision(entry["expected_decision"]),
            review_expected=bool(entry.get("review_expected", True)),
            notes=entry.get("notes", ""),
        )
        for entry in data["samples"]
    ]


@dataclass
class _CountingReviewer:
    delegate: LlmPermissionReviewer
    calls: int = 0
    verdict: PermissionReviewVerdict | None = None

    async def review(self, request: PermissionDeltaRequest) -> PermissionReviewVerdict:
        self.calls += 1
        self.verdict = await self.delegate.review(request)
        return self.verdict


def _request_for_sample(
    sample: PermissionReviewSample,
) -> tuple[RuntimePermissionProfile, PermissionDeltaRequest]:
    profile = workspace_runtime_profile().model_copy(
        update={"external_tools": ExternalToolPolicy.model_validate({sample.surface: "ask"})}
    )
    delta = PermissionDelta.external_tool(sample.surface).model_copy(
        update={"hard_deny": sample.hard_deny}
    )
    request = PermissionDeltaRequest.create_external(
        tool_use_id=f"eval-{sample.case_id}",
        tool_name=sample.tool_name,
        final_arguments=sample.final_arguments,
        profile=profile,
        policy=profile.external_tools,
        surface=sample.surface,
        effect_kind=sample.effect_kind,
        trust_source=sample.trust_source,
        tool_identity=sample.tool_name,
        server_identity=sample.server_identity,
        delta=delta,
        crossing=BoundaryViolation(
            dimension=f"external.{sample.surface}",
            requested=sample.tool_name,
            evidence=sample.crossing_evidence,
            hard_deny=sample.hard_deny,
        ),
        authorization_context=sample.authorization_context,
        data_sources=sample.data_sources,
        data_destinations=sample.data_destinations,
    )
    return profile, request


async def infer_permission_review(
    *,
    sample: PermissionReviewSample,
    api_client: SupportsStreamingMessages,
    model: str,
) -> PermissionReviewOutput:
    profile, request = _request_for_sample(sample)
    reviewer = _CountingReviewer(LlmPermissionReviewer(api_client=api_client, model=model))
    runtime = PermissionRuntime(profile=profile, boundary=None, reviewer=reviewer)

    resolution = await runtime.resolve_external(request)
    if reviewer.verdict is None:
        decision = PermissionReviewDecision.DENY
        reason = resolution.reason
    else:
        decision = reviewer.verdict.decision
        reason = reviewer.verdict.reason
    return PermissionReviewOutput(
        decision=decision,
        reason=reason,
        review_called=reviewer.calls == 1,
        resolution_status=resolution.status,
    )


def _serialize_output(output: PermissionReviewOutput) -> dict[str, Any]:
    return {
        "decision": output.decision.value,
        "reason": output.reason,
        "review_called": output.review_called,
        "resolution_status": output.resolution_status.value,
    }


def _deserialize_output(value: dict[str, Any]) -> PermissionReviewOutput:
    return PermissionReviewOutput(
        decision=PermissionReviewDecision(value["decision"]),
        reason=value["reason"],
        review_called=bool(value["review_called"]),
        resolution_status=PermissionResolutionStatus(value["resolution_status"]),
    )


async def cassetted_infer_permission_review(
    *,
    sample: PermissionReviewSample,
    api_client: SupportsStreamingMessages | None,
    model: str,
    cassette_mode: CassetteMode = "live",
    cassette_store: CassetteStore | None = None,
) -> PermissionReviewOutput:
    key = CassetteKey(case_id=sample.case_id, model=model, kind="infer")
    if cassette_mode == "replay":
        if cassette_store is None:
            raise ValueError("replay mode requires a cassette store")
        record = cassette_store.load(key)
        if record is None:
            raise CassetteMissingError(
                f"no permission-review cassette for {key.relative_path}; run record mode"
            )
        return _deserialize_output(record["response"])
    if api_client is None:
        raise ValueError(f"{cassette_mode} mode requires an api client")
    output = await infer_permission_review(sample=sample, api_client=api_client, model=model)
    if cassette_mode == "record":
        if cassette_store is None:
            raise ValueError("record mode requires a cassette store")
        cassette_store.save(
            key,
            f"{sample.case_id}: expected={sample.expected_decision.value}",
            _serialize_output(output),
        )
    return output


@dataclass(frozen=True)
class PermissionReviewCaseResult:
    sample: PermissionReviewSample
    output: PermissionReviewOutput
    scores: list[Score]


async def run_permission_review_eval(
    dataset_path: Path,
    scorers: list[Any],
    api_client: SupportsStreamingMessages | None,
    model: str,
    *,
    cassette_root: Path | None = None,
    cassette_mode: CassetteMode = "live",
    case_id: str | None = None,
) -> list[PermissionReviewCaseResult]:
    samples = select_cases(load_permission_review_dataset(dataset_path), case_id)
    store = CassetteStore(cassette_root) if cassette_root is not None else None
    results: list[PermissionReviewCaseResult] = []
    for sample in samples:
        output = await cassetted_infer_permission_review(
            sample=sample,
            api_client=api_client,
            model=model,
            cassette_mode=cassette_mode,
            cassette_store=store,
        )
        scores: list[Score] = [await scorer.score(sample, output) for scorer in scorers]
        results.append(PermissionReviewCaseResult(sample=sample, output=output, scores=scores))
    return results
