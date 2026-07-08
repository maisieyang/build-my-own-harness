"""SWE-bench Lite adapter (benchmark track M1, decisions/40).

Drives the shipped ``oh`` CLI over SWE-bench Lite instances and emits the
standard ``predictions.jsonl`` for official sb-cli cloud evaluation. A
consumer of the harness, never an intrusion into it (D40 §六: 1 extension
+ rest unchanged).
"""

from openharness.swebench.errors import (
    DatasetNotFoundError,
    MalformedInstanceError,
    SWEBenchError,
)
from openharness.swebench.model import SWEBenchInstance, load_instances
from openharness.swebench.prompt import build_prompt

__all__ = [
    "DatasetNotFoundError",
    "MalformedInstanceError",
    "SWEBenchError",
    "SWEBenchInstance",
    "build_prompt",
    "load_instances",
]
