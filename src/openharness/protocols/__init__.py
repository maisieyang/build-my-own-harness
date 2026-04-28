"""Wire-level protocols and data models -- public API.

The foundation that engine, tools, UI, compaction, and persistence all depend
on. Decisions: see ``decisions/02-protocols.md``. Architecture context:
``docs/learning/capability-ladder.md`` §7-§8.

Public API is re-exported from this module so callers do not need to know
about internal submodules:

    from openharness.protocols import ContentBlock, ApiMessageRequest, ToolSpec
"""

from __future__ import annotations

from openharness.protocols.content import (
    ContentBlock,
    ImageBlock,
    ImageSource,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from openharness.protocols.messages import ConversationMessage
from openharness.protocols.requests import ApiMessageRequest
from openharness.protocols.stream_events import (
    ApiMessageCompleteEvent,
    ApiRetryEvent,
    ApiStreamEvent,
    ApiTextDeltaEvent,
)
from openharness.protocols.tools import ToolSpec
from openharness.protocols.usage import UsageSnapshot

__all__ = [
    "ApiMessageCompleteEvent",
    "ApiMessageRequest",
    "ApiRetryEvent",
    "ApiStreamEvent",
    "ApiTextDeltaEvent",
    "ContentBlock",
    "ConversationMessage",
    "ImageBlock",
    "ImageSource",
    "TextBlock",
    "ToolResultBlock",
    "ToolSpec",
    "ToolUseBlock",
    "UsageSnapshot",
]
