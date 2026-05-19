"""Shared constants — name regex + frontmatter fence.

All three current consumers (commands / skills / bundles) use the
exact same values. Exported here so changes happen in one place.
"""

from __future__ import annotations

import re

# Safe-identifier regex shared across all user-supplied names that
# flow into argument positions or filesystem paths. Same value as
# ``McpServerConfig._NAME_PATTERN`` (Phase 5a) and the three
# dataclasses being unified.
NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]*$")

# YAML frontmatter delimiter. Triple-dash on its own line opens AND
# closes the block, same as Jekyll / Hugo / pytest's docstring marker.
FRONTMATTER_FENCE = "---"
