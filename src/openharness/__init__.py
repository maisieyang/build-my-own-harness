"""OpenHarness — a production-grade Python harness for LLM agents.

Top-level re-exports keep import paths short for the most common
consumer surface (configuration). Sub-packages
(``openharness.protocols`` / ``openharness.api``) own their own
``__init__`` re-exports for the deeper types.
"""

from __future__ import annotations

from openharness.config import Settings

__version__ = "0.1.0"

__all__ = ["Settings", "__version__"]
