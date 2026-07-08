"""Instance → headless goal prompt (D40 T2).

Contract (D40.3 红线): only ``repo`` and ``problem_statement`` from the
instance reach the prompt. The firewalled fields (gold patch, test patch,
FAIL_TO_PASS / PASS_TO_PASS, hints) must never appear — tested by
sentinel in ``tests/swebench/test_prompt.py``. No local filesystem paths
either: the run's cwd IS the workspace, so relative navigation suffices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openharness.swebench.model import SWEBenchInstance

_TEMPLATE = """\
You are working inside a checkout of the open-source repository {repo}. \
The following GitHub issue was reported against this codebase:

<issue>
{problem_statement}
</issue>

Fix the issue described above.

Requirements:
- First locate the root cause in the source code, then apply the minimal \
change that resolves the issue. Do not rewrite or refactor beyond what the \
fix needs.
- Do not modify any test files or add new tests — the fix will be judged \
against the project's own test suite separately.
- Work only with files inside the current working directory, using relative \
paths.
- When you are confident the fix is complete, end with a one-paragraph \
summary of the change and why it resolves the issue.
"""


def build_prompt(instance: SWEBenchInstance) -> str:
    """Render the headless goal prompt for one instance."""
    return _TEMPLATE.format(
        repo=instance.repo,
        problem_statement=instance.problem_statement.strip(),
    )
