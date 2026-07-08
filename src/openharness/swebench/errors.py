"""Differentiated errors for the SWE-bench adapter (D40).

Same shape as the other subsystem error modules: one subsystem root
(``SWEBenchError``) under :class:`~openharness.errors.OpenHarnessError`,
leaf types per failure mode so callers can catch precisely and the CLI
can render remediation without a raw traceback.
"""

from __future__ import annotations

from openharness.errors import OpenHarnessError


class SWEBenchError(OpenHarnessError):
    """Root for every SWE-bench adapter failure."""


class DatasetNotFoundError(SWEBenchError):
    """The local dataset JSONL does not exist — remediation is a fetch."""

    def __init__(self, path: str) -> None:
        super().__init__(
            f"SWE-bench dataset not found: {path} — run `oh bench swebench fetch` first"
        )
        self.path = path


class FetchError(SWEBenchError):
    """The datasets-server fetch failed (transport or response shape)."""

    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"datasets-server fetch failed for {url}: {reason}")
        self.url = url
        self.reason = reason


class MalformedInstanceError(SWEBenchError):
    """A dataset line is not valid JSON or misses required instance fields."""

    def __init__(self, line_number: int, reason: str) -> None:
        super().__init__(f"malformed SWE-bench instance at line {line_number}: {reason}")
        self.line_number = line_number
        self.reason = reason
