"""``WhitelistRegistry`` — P5d-T3a.

Per ``decisions/17`` D19.6:wrap an existing ``ToolRegistry`` to expose
only a bundle-whitelisted subset.``ToolRegistry`` itself stays
unchanged(cross-layer invariant);engine/dispatch is agnostic because
the wrapper satisfies the same shape(``get`` / ``list_tools`` /
``to_api_schema``).

**Why subclass rather than compose-via-Protocol?**
``QueryContext.tool_registry`` is concrete-typed as ``ToolRegistry``
(D7.5). A Protocol-based wrap would require widening that field — which
violates the "zero diff to engine/" invariant. Subclassing keeps the
type compatibility without touching the engine layer.

**Skip-not-fail discipline**: if ``whitelist`` references tool names
that aren't actually registered in ``base``, the unknown names are
logged as a warning (consistent with parse_bundle / parse_command /
parse_skill — never raise during bootstrap).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from openharness.observability.logging import get_logger
from openharness.tools.base import ToolRegistry

if TYPE_CHECKING:
    from openharness.protocols.tools import ToolSpec
    from openharness.tools.base import BaseTool

_logger = get_logger("bundles")


class WhitelistRegistry(ToolRegistry):
    """``ToolRegistry`` exposing only the whitelisted subset of a base.

    The wrapper subclasses ``ToolRegistry`` so it satisfies
    ``QueryContext.tool_registry: ToolRegistry`` without engine-layer
    changes. All accessor methods are overridden to delegate to the
    wrapped base + filter by name.

    Construction is the bundle-application step's job (see
    :func:`openharness.bundles.apply.apply_bundle_to_context`);callers
    don't typically instantiate this directly.
    """

    def __init__(self, base: ToolRegistry, whitelist: set[str]) -> None:
        """Wrap ``base``; expose only names also in ``whitelist``.

        Unknown names in ``whitelist`` (not present in ``base``) are
        logged as a warning and silently dropped from the effective set.
        """
        super().__init__()
        self._base = base
        base_names = {t.name for t in base.list_tools()}
        unknown = whitelist - base_names
        if unknown:
            _logger.warning(
                "bundle_whitelist_unknown_tools",
                names=sorted(unknown),
            )
        self._effective: set[str] = whitelist & base_names

    def register(self, tool: BaseTool[Any]) -> None:  # noqa: ARG002
        """Forbidden — WhitelistRegistry is immutable after construction."""
        raise RuntimeError(
            "WhitelistRegistry is immutable; register tools on the base ToolRegistry"
        )

    def get(self, name: str) -> BaseTool[Any]:
        """Return the tool if whitelisted;raise ``KeyError`` otherwise.

        Whitelist check happens *before* the base lookup — a tool that
        exists in base but isn't whitelisted is invisible from the LLM's
        perspective.
        """
        if name not in self._effective:
            raise KeyError(name)
        return self._base.get(name)

    def list_tools(self) -> list[BaseTool[Any]]:
        """Filtered subset of the base's ``list_tools()`` (insertion
        order preserved from base)."""
        return [t for t in self._base.list_tools() if t.name in self._effective]

    def to_api_schema(self) -> list[ToolSpec]:
        """Filtered subset of the base's ``to_api_schema()``— what the
        LLM sees in its tool catalog (insertion order preserved)."""
        return [s for s in self._base.to_api_schema() if s.name in self._effective]
