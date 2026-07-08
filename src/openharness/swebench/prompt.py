"""Instance → headless goal prompt (D40 T2).

Contract (D40.3 红线): only ``repo`` and ``problem_statement`` from the
instance reach the prompt. The firewalled fields (gold patch, test patch,
FAIL_TO_PASS / PASS_TO_PASS, hints) must never appear — tested by
sentinel in ``tests/swebench/test_prompt.py``. No local filesystem paths
either: the run's cwd IS the workspace, so relative navigation suffices.

``executable`` states the run's actual capability surface (小批 finding,
D40 M1): without ``--sandbox`` the Bash tool is denied, so the model must
be told it cannot run anything — otherwise it writes repro/test scripts
it can never execute (3/5 of the first mini-batch patches were polluted
with them, and it has no tool to delete files afterwards either).
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
{execution_note}\
- Work only with files inside the current working directory, using relative \
paths.
- When you are confident the fix is complete, end with a one-paragraph \
summary of the change and why it resolves the issue.
"""

_STATIC_NOTE = (
    "- This environment cannot execute code or run tests: shell access is "
    "unavailable. Do not create scratch scripts, reproduction files, or any "
    "new files — reason about the code statically and edit the source "
    "directly.\n"
)

_SANDBOX_NOTE = (
    "- You may run shell commands to explore or reproduce the issue, but "
    "remove any scratch files you created before finishing — the final diff "
    "must only touch source files.\n"
)


def build_prompt(instance: SWEBenchInstance, *, executable: bool = False) -> str:
    """Render the headless goal prompt for one instance.

    ``executable=True`` when the run carries ``--sandbox`` (Bash allowed);
    default matches the no-sandbox posture where Bash is denied.
    """
    return _TEMPLATE.format(
        repo=instance.repo,
        problem_statement=instance.problem_statement.strip(),
        execution_note=_SANDBOX_NOTE if executable else _STATIC_NOTE,
    )
