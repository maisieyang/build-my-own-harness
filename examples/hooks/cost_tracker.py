"""Filesystem plugin hook — log per-call cost via PostApiCall usage.

Drop this file at ``~/.openharness/hooks/cost_tracker.py`` (global)
or ``<cwd>/.openharness/hooks/cost_tracker.py`` (project) and run
with ``--enable-plugin-hooks``:

    cp examples/hooks/cost_tracker.py ~/.openharness/hooks/
    oh ask --enable-plugin-hooks "list 5 git commands"

Verify discovery picked it up:

    oh hooks list --enable-plugin-hooks
    oh hooks describe track_cost --enable-plugin-hooks

The hook fires after each LLM API call (``PostApiCall``) and emits a
structured ``api_call_cost`` log line with input/output tokens and
USD cost computed from a per-million-token price table. Pure
observability — it never modifies the call.

The framework intentionally ships no built-in cost tracker because:

1. Price tables expire (providers re-price / launch new models).
2. Billing strategies differ (batch API discount, enterprise contract,
   prompt-cache discount).
3. Reporting destinations differ (stderr / DB / Prometheus / SIEM).

Building blocks (``UsageSnapshot`` on every ``PostApiCallContext``)
are framework-provided;the policy table below is yours to maintain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from openharness.bundles import hook_spec
from openharness.observability.logging import get_logger

if TYPE_CHECKING:
    from openharness.hooks.context import HookContext
    from openharness.hooks.result import HookResult

# Per-million-token USD prices — illustrative only;maintain your own.
# Format: model_id -> (input_per_mtok, output_per_mtok)
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-7": (15.00, 75.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "gpt-4o": (2.50, 10.00),
    "qwen-plus": (0.40, 1.20),
    "qwen-max": (2.40, 9.60),
}

_logger = get_logger("plugin.cost_tracker")
_cumulative_cost = 0.0  # process-local;real deployments persist to file/DB


@hook_spec("PostApiCall")
async def track_cost(context: HookContext) -> HookResult | None:
    """Log per-call cost. Passthrough — never modifies the result."""
    usage = getattr(context, "usage", None)
    request = getattr(context, "request", None)
    if usage is None or request is None:
        return None  # not a PostApiCall context — passthrough

    model = request.model
    in_price, out_price = PRICING.get(model, (0.0, 0.0))
    cost = usage.input_tokens * in_price / 1_000_000 + usage.output_tokens * out_price / 1_000_000

    global _cumulative_cost
    _cumulative_cost += cost

    _logger.info(
        "api_call_cost",
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cost_usd=round(cost, 6),
        cumulative_usd=round(_cumulative_cost, 4),
        price_known=model in PRICING,
    )
    return None  # passthrough — observe only
