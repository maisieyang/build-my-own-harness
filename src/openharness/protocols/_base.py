"""Shared Pydantic base for all protocol models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Protocol model base — strict validation by default.

    - ``extra="forbid"``: unknown fields raise ``ValidationError`` (catches typos).
    - ``validate_assignment=True``: re-validate on field mutation (catches type drift after
      construction, important since we chose mutable models in D2.3).
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)
