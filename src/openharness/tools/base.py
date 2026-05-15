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
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from pydantic import BaseModel

from openharness.protocols import ToolSpec

if TYPE_CHECKING:
    from pathlib import Path


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


@dataclass(frozen=True)
class ToolExecutionContext:
    """Runtime context handed to ``BaseTool.execute``.

    P2-T2 carries only ``cwd``. P2-T3 file tools resolve relative paths
    against this; ``Bash`` uses it as the subprocess cwd; ``Grep`` walks
    from it. Future fields (settings excerpts, hook data) land here as
    additive frozen-dataclass fields when concrete capabilities surface them.
    """

    cwd: Path


# Bound to BaseModel so every tool's input is parseable from the LLM's
# JSON dict via ``model.model_validate(...)``.
InputT = TypeVar("InputT", bound=BaseModel)


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
