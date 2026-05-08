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

    @abstractmethod
    async def execute(
        self,
        args: InputT,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Run the tool with already-validated ``args`` and return a result.

        Recoverable failures (file not found, command non-zero exit, network
        timeout) → ``ToolResult(is_error=True, output="<error message>")`` so
        the LLM can read the error and adapt. Programming errors (assertion
        failures, type mismatches that escaped validation) → ``raise`` and
        let the loop surface them.
        """
