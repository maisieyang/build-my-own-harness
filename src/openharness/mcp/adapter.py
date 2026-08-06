"""``McpToolAdapter`` — bridge MCP tool definitions into ``BaseTool`` — P5-T3.

Per ``decisions/11`` D15.3 + D15.6 + D15.8:each MCP tool reported by
``client.list_tools()`` becomes an ``McpToolAdapter`` instance(a
``BaseTool[BaseModel]`` subclass). The adapter:

- exposes the tool to the LLM under name ``f"{server_name}.{tool_name}"``
  (PascalCase per D6.4 — server names are PascalCase by config convention,
  tool names from MCP servers are typically snake_case, so the combined
  form is ``Filesystem.read_file`` — same shape Claude Desktop uses);
- synthesizes a Pydantic ``input_model`` from the tool's ``inputSchema``
  (JSON Schema) so dispatch-time validation still works;
- gates ``is_read_only`` through the user's trust whitelist (D15.6) —
  servers not in ``trusted_mcp_servers`` get ``is_read_only=False``
  forced, ensuring AuthZ Tier 3 evaluates them as side-effectful;
- calls ``client.call_tool(...)`` in ``execute`` and translates the
  ``CallToolResult`` into ``ToolResult`` — logical errors (server returned
  ``isError=True``) become ``ToolResult(is_error=True)`` (errors-as-payload
  D15.4); transport failures (``McpCallError``) also become ``is_error``
  so the LLM sees a message and can adapt.

JSON Schema subset supported (D15.8):

- Primitives: ``string`` / ``integer`` / ``number`` / ``boolean``
- Containers: ``array`` / ``object`` (no nested schemas yet — both map
  to ``list[Any]`` / ``dict[str, Any]``)
- ``enum`` → ``Literal[...]``
- ``required`` list controls Pydantic field defaults

Unsupported types (``$ref`` / ``oneOf`` / ``anyOf`` / ``allOf`` / format
constraints) fall back to ``Any``. Tools using these still register, just
with weaker validation. Phase 6+ may widen.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, create_model

from openharness.mcp.client import McpCallError
from openharness.tools.base import (
    BaseTool,
    ExecutionDomain,
    ExternalEffectSurface,
    ToolResult,
)

if TYPE_CHECKING:
    from openharness.mcp.client import McpClient
    from openharness.tools.base import ToolExecutionContext


# Map JSON Schema primitive type names → Python types. Fallback for
# unknown / complex schemas is ``Any``.
_PRIMITIVE_TYPE_MAP: dict[str, type[Any]] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _resolve_field_type(prop_schema: dict[str, Any]) -> Any:
    """Map a single JSON Schema property to its Python type.

    Handles:
    - ``enum``:list of string/int values → ``Literal[v1, v2, ...]``
    - primitive ``type``:string/integer/number/boolean/array/object
    - unsupported ($ref / anyOf / oneOf / etc.) → ``Any``
    """
    if "enum" in prop_schema:
        values = prop_schema["enum"]
        # Literal needs static unpacking;cast to whatever Python type each
        # value has — JSON Schema enums are typed-homogenous in practice.
        return Literal[tuple(values)]
    json_type = prop_schema.get("type")
    if isinstance(json_type, str) and json_type in _PRIMITIVE_TYPE_MAP:
        return _PRIMITIVE_TYPE_MAP[json_type]
    # $ref / oneOf / anyOf / allOf / unknown — fall back to Any (Phase 5
    # weak-validation path per D15.8).
    return Any


def _synth_input_model(
    schema: dict[str, Any] | None,
    model_name: str = "McpToolInput",
) -> type[BaseModel]:
    """Build a Pydantic model class matching ``schema`` (JSON Schema).

    Per D15.8 supports the common subset. ``schema=None`` or
    ``schema={"type": "object", "properties": {}}`` → empty model
    (allow tools with zero arguments).

    The returned class is unique per call (``pydantic.create_model``
    builds a fresh class) — callers must not depend on identity across
    instances.
    """
    if not schema:
        return create_model(model_name)

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])

    fields: dict[str, Any] = {}
    for prop_name, prop_schema in properties.items():
        field_type: Any
        if not isinstance(prop_schema, dict):
            # Defensive: tools sometimes ship malformed schemas — make them
            # land as Any rather than blow up the whole adapter.
            field_type = Any
            description = None
        else:
            field_type = _resolve_field_type(prop_schema)
            description = prop_schema.get("description")
        # Pydantic field tuple: (type, FieldInfo)
        if prop_name in required:
            field_info = Field(..., description=description)
        else:
            field_info = Field(default=None, description=description)
            # Make the type Optional so None default is valid.
            field_type = field_type | None
        fields[prop_name] = (field_type, field_info)

    # ``create_model`` typing is permissive at runtime but its overloads
    # don't perfectly match ``**fields`` with our ``(type, FieldInfo)`` tuples.
    # The runtime contract is exactly what we want.
    model: type[BaseModel] = create_model(model_name, **fields)
    return model


class McpToolAdapter(BaseTool[BaseModel]):
    """Wraps one MCP server's tool definition as a :class:`BaseTool`.

    Instance attributes shadow class attrs so each adapter carries its
    own ``name`` / ``description`` / ``input_model`` / ``is_read_only``
    (Python instance > class attribute lookup;BaseTool only declares
    class-level annotations, no class-level assignments).

    Construction:

        adapter = McpToolAdapter(
            server_name="Filesystem",
            raw_tool_def=tool_def,   # SDK's Tool object
            client=mcp_client,       # already-entered McpClient
            trust=True,              # from Settings.trusted_mcp_servers
        )
    """

    execution_domain = ExecutionDomain.EXTERNAL_EFFECT
    external_effect_surface = ExternalEffectSurface.MCP

    def __init__(
        self,
        server_name: str,
        raw_tool_def: Any,
        client: McpClient,
        trust: bool,
    ) -> None:
        # SDK's Tool type has: name, description, inputSchema, annotations.
        tool_name = raw_tool_def.name
        self.name = f"{server_name}.{tool_name}"
        self.description = raw_tool_def.description or f"MCP tool {tool_name}"
        # Synthesize the input model from inputSchema.
        schema = getattr(raw_tool_def, "inputSchema", None)
        self.input_model = _synth_input_model(
            schema if isinstance(schema, dict) else dict(schema or {}),
            model_name=f"{server_name}_{tool_name}_Input",
        )
        # Trust gating (D15.6) — if untrusted, force is_read_only=False
        # regardless of what the server claims. Trust evaluation happens
        # ONCE at adapter construction;McpClientPool passes the right
        # value based on Settings.trusted_mcp_servers.
        self.is_read_only = self._resolve_read_only(raw_tool_def, trust)
        # Stored for execute().
        self._tool_name = tool_name
        self._client = client
        # T5 will read this when populating ``tool_dispatch`` log; default
        # value here so T5's change is just "compute trust_source from
        # adapter.trust_source" rather than re-deriving the logic.
        self.trust_source: str = "trusted-server" if trust else "strict-default"

    @staticmethod
    def _resolve_read_only(raw_tool_def: Any, trust: bool) -> bool:
        """Read ``annotations.readOnlyHint`` only when trusted.

        Untrusted servers' self-reports are ignored — they always
        force ``is_read_only=False`` (strict path through AuthZ Tier 3).
        """
        if not trust:
            return False
        annotations = getattr(raw_tool_def, "annotations", None)
        hint = getattr(annotations, "readOnlyHint", None) if annotations else None
        return bool(hint) if hint is not None else False

    async def execute(
        self,
        args: BaseModel,
        context: ToolExecutionContext,
    ) -> ToolResult:
        """Dispatch ``tools/call`` on the underlying MCP server.

        Translation rules:
        - args (Pydantic model) → ``model_dump()`` dict → MCP arguments
        - CallToolResult.content (list of TextContent etc.) → joined str
          for ToolResult.output
        - CallToolResult.isError=True → ToolResult(is_error=True, ...)
        - McpCallError (transport / pipe broken) → ToolResult(is_error=True,
          output=<server unavailable msg>) — errors-as-payload (D15.4)
        """
        del context  # ToolExecutionContext.cwd not relevant to remote MCP calls
        arguments = args.model_dump(exclude_none=True)
        try:
            result = await self._client.call_tool(self._tool_name, arguments=arguments)
        except McpCallError as exc:
            return ToolResult(
                is_error=True,
                output=f"MCP server {self._client.name!r} unavailable: {exc}",
            )

        # Extract textual content. The SDK's ``content`` is a list of
        # blocks (TextContent / ImageContent / EmbeddedResource);for
        # Phase 5 tools-only we only render TextContent's ``text`` field.
        text_parts: list[str] = []
        for block in result.content:
            text = getattr(block, "text", None)
            if isinstance(text, str):
                text_parts.append(text)
        output = "\n".join(text_parts) if text_parts else "(no content)"

        return ToolResult(
            output=output,
            is_error=bool(result.isError),
        )
