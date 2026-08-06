"""Tool abstractions — :class:`BaseTool`, :class:`ToolResult`,
:class:`ToolExecutionContext` (P2-T2 sub-units 2a + 2b).

Per the P2-T2 Three-Axis discussion (D8.1 - D8.5, D8.7):

- **D8.1**: ``BaseTool`` is an ABC, not a Protocol. Every tool source (base
  tools, future MCP adapter, plugins) explicitly inherits.
- **D8.2**: ``input_model`` is a Pydantic class (``type[InputT]``). MCP
  adapters synthesize one via ``pydantic.create_model()`` from JSON Schema.
- **D8.3**: ``BaseTool`` is generic in ``InputT`` (a ``BaseModel`` subclass)
  so subclasses can write ``execute(args: ReadInput, ...)`` without an LSP
  violation.
- **D8.4**: ``ToolResult.output`` is a flat ``str``. ``metadata`` is the
  escape hatch for structured info.
- **D8.5**: ``is_error=True`` represents a *recoverable* failure the LLM can
  react to. Programming errors ``raise`` instead.
- **D8.7**: ``ToolExecutionContext`` carries only ``cwd`` for now.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from openharness.execution.boundary import (
    BoundaryViolation,
    ExecutionFailed,
    ExecutionResult,
    OperationCompleted,
    ProcessCompleted,
    TimedOut,
)
from openharness.protocols import ToolSpec

if TYPE_CHECKING:
    from pathlib import Path

    from openharness.engine.context import QueryContext
    from openharness.execution import ExecutionEnvironment, SandboxSession


@dataclass(frozen=True)
class ToolResult:
    """A tool's outcome, fed back to the LLM as a ``ToolResultBlock``.

    Construction patterns:

    - Success: ``ToolResult(output="contents of file X")``
    - Recoverable failure: ``ToolResult(output="permission denied", is_error=True)``
    - With structured metadata: ``ToolResult(output="...", metadata={"bytes": 42})``
    """

    output: str
    is_error: bool = False
    # Per-instance dict — ``field(default_factory=dict)`` avoids the classic
    # mutable-default-argument trap (every instance shares one dict).
    metadata: dict[str, Any] = field(default_factory=dict)


def tool_result_from_operation(
    result: ExecutionResult,
) -> ToolResult:
    """Translate structured local-data-plane outcomes for file tools."""
    if isinstance(result, OperationCompleted):
        return ToolResult(
            output=result.output,
            is_error=result.is_error,
            metadata=dict(result.metadata),
        )
    if isinstance(result, BoundaryViolation):
        return ToolResult(
            output=(
                f"sandbox boundary violation ({result.dimension}): "
                f"{result.requested}; {result.evidence}"
            ),
            is_error=True,
            metadata={
                "boundary_violation": {
                    "dimension": result.dimension,
                    "requested": result.requested,
                    "evidence": result.evidence,
                }
            },
        )
    if isinstance(result, TimedOut):
        return ToolResult(output="sandbox operation timed out", is_error=True)
    if isinstance(result, ExecutionFailed):
        return ToolResult(output=f"sandbox operation failed: {result.reason}", is_error=True)
    if isinstance(result, ProcessCompleted):
        return ToolResult(
            output="sandbox returned an invalid result for a filesystem operation",
            is_error=True,
        )


@dataclass(frozen=True)
class ToolExecutionContext:
    """Runtime context handed to ``BaseTool.execute``.

    P2-T2 carried only ``cwd``. P6-T2 (D16.8) adds the optional
    ``parent_query`` field — additive default ``None`` so every existing
    tool (Read / Write / Edit / Bash / Grep / MCP adapters / LoadSkill)
    continues to ignore it. Only :class:`SpawnAgent` reads it, to build
    the sub-agent's ``QueryContext`` via ``dataclasses.replace``.

    The TYPE_CHECKING-only import of :class:`QueryContext` avoids the
    runtime import cycle (``engine/context.py`` already imports
    ``tools.ToolRegistry`` lazily). At runtime the field is just an
    opaque object the tool either passes through or unwraps via
    ``dataclasses.replace``.

    Per the cross-cutting invariant, this is the **only structural
    dispatch-machinery change Phase 6 introduces** — and it's strictly
    additive.
    """

    cwd: Path
    parent_query: QueryContext | None = None
    # P7-T2 (D17.3 / D17.4): substrate the tool should delegate shell
    # commands to. Engine populates from ``QueryContext.execution_env``.
    # Default ``None`` so every existing tool (Read / Write / Edit /
    # Grep / MCP / SpawnAgent / LoadSkill) that doesn't consume the
    # field passes through unchanged. Only ``BashTool`` reads it (with
    # the ``_HOST_EXECUTION`` fallback if ``None``, preserving direct
    # construction of ``ToolExecutionContext(cwd=p)`` in tests).
    execution_env: ExecutionEnvironment | None = None
    # S4: when present, all LOCAL_DATA tools route through this verified
    # session. Legacy execution_env/host paths remain only for postures that
    # have not selected a unified sandbox runtime.
    sandbox_session: SandboxSession | None = None


# Bound to BaseModel so every tool's input is parseable from the LLM's
# JSON dict via ``model.model_validate(...)``.
InputT = TypeVar("InputT", bound=BaseModel)


class ExecutionDomain(str, Enum):
    """Where a model-callable tool's effects are enforced."""

    UNDECLARED = "undeclared"
    LOCAL_DATA = "local_data"
    EXTERNAL_EFFECT = "external_effect"
    DELEGATED_RUNTIME = "delegated_runtime"
    TRUSTED_CONTROL = "trusted_control"


class ExternalEffectSurface(str, Enum):
    MCP = "mcp"
    WEB = "web"
    BROWSER = "browser"
    COMPUTER_USE = "computer_use"


class BaseTool(ABC, Generic[InputT]):
    """Abstract base class for every tool the agent can invoke.

    Subclasses define four things:

    1. ``name`` — unique identifier; PascalCase per ``decisions/06`` D6.4.
    2. ``description`` — one-line LLM-visible description.
    3. ``input_model`` — Pydantic class describing the tool's arguments.
    4. ``execute`` — the async body that does the actual work.

    ABC enforcement: ``execute`` is ``@abstractmethod``, so any subclass
    that forgets it cannot be instantiated. ``name`` / ``description`` /
    ``input_model`` are class-level annotations — mypy strict catches
    subclasses that omit them at static-check time; at runtime, accessing
    a missing attribute raises ``AttributeError``.
    """

    # Class attributes subclasses must assign. No defaults here — that would
    # make incomplete subclasses silently inherit base values.
    name: str
    description: str
    input_model: type[InputT]

    # P3-T1.1a / D13.3: read-only classification with a safe default. Tools
    # that mutate state (Write / Edit / Bash) inherit ``False`` and go through
    # the AuthZ Tier 3 strict path (P3-T3); Read-only tools (Read / Grep) opt
    # IN by setting ``is_read_only = True``. Bash defaults False even though
    # ``cat foo`` is read-only — static classification can't tell ``cat`` from
    # ``rm``, so we err conservative.
    is_read_only: bool = False

    # P5-T5 (D15.6 / boundary 11): provenance of the ``is_read_only`` value
    # — observability shows where the trust decision came from.
    #   "local"           — built-in tools (Read / Write / Bash / ...)
    #   "trusted-server"  — MCP adapter whose server is in
    #                       Settings.trusted_mcp_servers (server's
    #                       readOnlyHint was honored)
    #   "strict-default"  — MCP adapter whose server is NOT trusted
    #                       (is_read_only forced to False regardless of
    #                       what the server claimed)
    # ``engine/query.py`` reads this on each dispatch into the
    # ``tool_dispatch`` log event's ``trust_source`` field — the only
    # engine change Phase 5 needs (boundary cross-cutting invariant).
    trust_source: str = "local"

    # S1 boundary coverage contract. Registration rejects the sentinel so a
    # new model-callable surface cannot silently inherit host authority.
    execution_domain: ExecutionDomain = ExecutionDomain.UNDECLARED
    external_effect_surface: ExternalEffectSurface | None = None

    @abstractmethod
    async def execute(
        self,
        args: InputT,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Run the tool with already-validated ``args`` and return a result.

        Recoverable failures (file not found, command non-zero exit, network
        timeout) -> ``ToolResult(is_error=True, output="<error message>")`` so
        the LLM can read the error and adapt. Programming errors (assertion
        failures, type mismatches that escaped validation) -> ``raise`` and
        let the loop surface them.
        """


class ToolRegistry:
    """In-memory map from tool name to :class:`BaseTool` instance.

    Per D8.6 (Three-Axis): plain method ``register`` (no decorator sugar),
    duplicate names raise ``ValueError``. Stored as ``BaseTool[Any]`` —
    the registry doesn't care about the input type parameter; only the loop
    (P2-T4) cares once a specific tool's ``execute`` is being called.
    """

    def __init__(self) -> None:
        # Insertion-ordered; ``list_tools()`` and ``to_api_schema()`` rely
        # on dict's ordering guarantee (Python 3.7+) for stable output.
        self._tools: dict[str, BaseTool[Any]] = {}

    def register(self, tool: BaseTool[Any]) -> None:
        """Register a tool under its ``name``. Raises on duplicate."""
        if tool.execution_domain is ExecutionDomain.UNDECLARED:
            raise ValueError(
                f"tool {tool.name!r} has no execution domain; "
                "declare where its effects are enforced"
            )
        if (
            tool.execution_domain is ExecutionDomain.EXTERNAL_EFFECT
            and tool.external_effect_surface is None
        ):
            raise ValueError(
                f"tool {tool.name!r} has no external effect surface; "
                "declare which independent policy applies"
            )
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool[Any]:
        """Retrieve a registered tool by name. Raises ``KeyError`` if absent."""
        if name not in self._tools:
            raise KeyError(name)
        return self._tools[name]

    def list_tools(self) -> list[BaseTool[Any]]:
        """Return the registered tools in insertion order. Caller-owned list
        copy -- mutating it does not affect the registry."""
        return list(self._tools.values())

    def execution_domain_report(self) -> dict[ExecutionDomain, tuple[str, ...]]:
        """Return deterministic configured tool coverage by execution domain."""
        grouped: dict[ExecutionDomain, list[str]] = {}
        for tool in self._tools.values():
            grouped.setdefault(tool.execution_domain, []).append(tool.name)
        return {domain: tuple(names) for domain, names in grouped.items()}

    def external_effect_report(self) -> dict[ExternalEffectSurface, tuple[str, ...]]:
        """Name each external surface not covered by a local boundary."""
        grouped: dict[ExternalEffectSurface, list[str]] = {}
        for tool in self._tools.values():
            if tool.execution_domain is not ExecutionDomain.EXTERNAL_EFFECT:
                continue
            surface = tool.external_effect_surface
            if surface is None:
                continue
            grouped.setdefault(surface, []).append(tool.name)
        return {surface: tuple(names) for surface, names in grouped.items()}

    def to_api_schema(self) -> list[ToolSpec]:
        """Project the registry to ``list[ToolSpec]`` for the API request.

        Per D8.2: each tool's ``input_model.model_json_schema()`` becomes the
        ``input_schema`` field. Per D8.8: returns the ``ToolSpec`` Pydantic
        type from ``protocols``, not a raw ``list[dict]`` -- callers can pass
        the result straight into ``ApiMessageRequest.tools`` without
        translation.

        Insertion order preserved (mirrors :meth:`list_tools`).
        """
        return [
            ToolSpec(
                name=tool.name,
                description=tool.description,
                input_schema=tool.input_model.model_json_schema(),
            )
            for tool in self._tools.values()
        ]
