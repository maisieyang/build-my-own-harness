"""LLM-judge rubrics — Stage 3 substrate.

Per-capability rubric registry for :class:`CapabilityLLMJudgeScorer`.
Selective coverage (D32.1): only T4 / T5 / T6 / T7 — the 4 brittleness
dimensions Stage 2 N=3 surfaced. Other capabilities (T1/T2/T3/T8) don't
register a rubric; the judge returns ``"NA"`` for them.

Each rubric follows playbook §四 4.3 + D32.7 5 principles:

1. Binary (1/0) not 5-point
2. Chain-of-thought: "reason first, then score"
3. PASS / FAIL examples in 2 languages
4. Length < 500 words (judge prompt > 800 字 → +30% score noise)
5. Strict JSON output: ``{"reason": "...", "score": 0|1}``

Bias defenses inside each rubric:
- Verbosity: "Length does NOT factor"
- Format: "Output ONLY JSON, no markdown fence"
- Cross-language: "any language" example pairs

Self-preference bias is NOT defended (single-model env per D32.5);
calibration drift tracking is Stage 4+ (per D32 §四).
"""

from __future__ import annotations

_T4_RUBRIC = """You are an evaluation judge.

Given:
- A conversation where the assistant called a tool that FAILED.
- A candidate "next_step" inference for what to do next.

Decide: does next_step propose a meaningful RECOVERY action?

PASS examples (any language):
- "suggest checking the correct filename"
- "list nearby files with ls / grep"
- "verify the file path with user"
- "建议确认正确的文件名"
- "suggest checking for a typo or alternative filename"
- "verify the file path"

FAIL examples:
- "wait for user instruction"
- "retry the same tool on the same path"
- "等待用户的下一步指示"
- "no further action"

Length does NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence justifying", "score": 0 or 1}"""


_T5_RUBRIC = """You are an evaluation judge.

Given:
- A multi-step conversation (Read → Grep, etc.) where the user wants a high-level task done.
- A candidate "goal" inference for what the assistant is trying to do.

Decide: does goal capture the HIGH-LEVEL user task (PASS), or get distracted by a LOW-LEVEL action like symbol discovery (FAIL)?

Critical distinction: it is OK to MENTION a symbol as part of the broader task. It is NOT OK to MAKE that symbol discovery the goal itself.

PASS examples:
- "fix the failing test_X by repairing the markdown logic in _parse_focus_state_response"
  (mentions symbol, but focus is "fix the test")
- "verify the test_email assertion passes"

FAIL examples:
- "identify the implementation of _parse_focus_state_response"
  (symbol IS the goal, not a means)
- "find the definition of class User to understand the test"
  (discovery as the goal)

Length and language do NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence on what the goal focuses on", "score": 0 or 1}"""


_T6_RUBRIC = """You are an evaluation judge.

Given:
- A conversation where the assistant used only tool_use blocks (no text).
- A candidate "goal" inference for the user's task.

Decide: does goal use user-side language (PASS), or leak a tool name as a verb (FAIL)?

Critical: words like "read" / "grep" can be either an English verb ("read the docstring") OR a tool name ("call the Read tool"). Judge which.

PASS examples:
- "examine EXTRACTION_SYSTEM_PROMPT content" (user-side)
- "read the content of X" ("read" as English verb describing intent)
- "view the docstring of foo"
- "查看 X 的定义"

FAIL examples:
- "grep for X in Y" (tool name as verb)
- "use Read tool on file X" (explicit tool reference)
- "execute the Bash command Y"

Length and language do NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence on how the verb is used", "score": 0 or 1}"""


_T7_RUBRIC = """You are an evaluation judge.

Given:
- A conversation where the user said "done" / "ok" / "完成" after an Edit succeeded.
- A candidate "next_step" inference.

Decide: does next_step propose a verification or follow-up action (PASS), OR blindly trust the user's "done" (FAIL)?

PASS examples (any language):
- "run tests/test_X.py to confirm no regression"
- "verify by reading file content"
- "pytest tests/services/test_focus_state.py"
- "测试 inference_started 是否能被触发"
- "检查 logger 命名一致性"
- "确认无语法错误"

FAIL examples:
- "wait for new user task"
- "confirm with user the task is done"
- "等用户新的指示"
- "no further action needed"

Length and language do NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence on what verification is proposed", "score": 0 or 1}"""


# ---------------------------------------------------------------------------
# Memory-decision rubrics — Phase 16 T3 (D35.5 P0)
#
# For the memory_decision eval (decision surface #4 inline decision).
# Each rubric asks the judge: "did the LLM pick a defensible
# frontmatter ``type`` for this user message under the Claude-Code
# memory taxonomy?". The rubrics accept multiple valid readings — e.g.,
# "I prefer terse responses" is defensible as ``user`` (preference about
# communication) OR ``feedback`` (guidance on how to approach work).
# This matches Claude Code's own taxonomy where the two type
# definitions deliberately overlap in the preference region.
# ---------------------------------------------------------------------------


_M_JUDGE_PREFERENCE_RUBRIC = """You are an evaluation judge.

Given:
- A user message expressing a stable preference about how they want to be
  worked with (response style, code conventions, communication tone, etc.).
- The frontmatter ``type`` field the model wrote.

Decide: is the type defensible under the Claude-Code memory taxonomy?

Type definitions (verbatim from the system prompt):
- user: information about the user's role, preferences, knowledge.
- feedback: guidance the user has given you about how to approach work
  (corrections, confirmed approaches).
- project: ongoing work, goals, initiatives within the project.
- reference: pointers to external systems.

For preference-style messages, BOTH ``user`` and ``feedback`` are
DEFENSIBLE — Claude Code's own taxonomy overlaps in this region.
``project`` or ``reference`` are NOT defensible (they're about external
state, not the user themselves).

PASS examples (any of these types for a preference user message):
- type=user (preference is user-facing info)
- type=feedback (preference is guidance about behavior)

FAIL examples:
- type=project (preference is not project state)
- type=reference (preference is not an external pointer)

Length does NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence justifying", "score": 0 or 1}"""


_M_JUDGE_CORRECTION_RUBRIC = """You are an evaluation judge.

Given:
- A user message correcting an approach or stating a non-obvious fact
  with reasoning (e.g., "actually we use X, not Y, because Z").
- The frontmatter ``type`` field the model wrote.

Decide: is the type defensible?

Corrections / clarifications are most naturally ``feedback`` (guidance
about how to approach work), but ``project`` is ALSO defensible when
the correction conveys project state ("we use X tooling" = a project
fact). ``user`` is defensible if the correction is about a personal
working choice. ``reference`` is NOT defensible unless the correction
is specifically pointing at an external system.

PASS examples:
- type=feedback (correction about approach)
- type=project (correction conveys project state)
- type=user (correction about personal style)

FAIL examples:
- type=reference (correction is not about external system unless
  explicitly mentioning Linear/Slack/etc.)

Length does NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence justifying", "score": 0 or 1}"""


_M_JUDGE_PROJECT_RUBRIC = """You are an evaluation judge.

Given:
- A user message conveying project state, decisions, deadlines, or
  ongoing work context (e.g., "we're freezing merges Thursday for the
  release branch").
- The frontmatter ``type`` field the model wrote.

Decide: is the type defensible?

Project-state messages MUST be ``project``. Other types are wrong:
- type=user would be a category error (not about the user)
- type=feedback would conflate project state with behavioral guidance
- type=reference would be wrong unless the message is purely a
  pointer to an external resource

PASS examples:
- type=project

FAIL examples:
- type=user / type=feedback / type=reference for clear project state

Length does NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence justifying", "score": 0 or 1}"""


_M_JUDGE_REFERENCE_RUBRIC = """You are an evaluation judge.

Given:
- A user message pointing at an external system as the source of
  truth (Linear project, Slack channel, dashboard, etc.).
- The frontmatter ``type`` field the model wrote.

Decide: is the type defensible?

Reference messages MUST be ``reference``. ``project`` is
borderline-defensible if the external system IS the project's
canonical tracker — but the cleaner choice is ``reference``. Other
types are wrong.

PASS examples:
- type=reference (canonical)
- type=project (borderline-defensible when the external system is
  THE project tracker)

FAIL examples:
- type=user / type=feedback

Length does NOT factor.

Output EXACTLY one JSON on a single line, no markdown fence:
{"reason": "one sentence justifying", "score": 0 or 1}"""


CAPABILITY_RUBRICS: dict[str, str] = {
    "T4": _T4_RUBRIC,
    "T5": _T5_RUBRIC,
    "T6": _T6_RUBRIC,
    "T7": _T7_RUBRIC,
    # Memory-decision capability families — Phase 16 T3 (D35.5 P0).
    "M-judge-preference": _M_JUDGE_PREFERENCE_RUBRIC,
    "M-judge-correction": _M_JUDGE_CORRECTION_RUBRIC,
    "M-judge-project": _M_JUDGE_PROJECT_RUBRIC,
    "M-judge-reference": _M_JUDGE_REFERENCE_RUBRIC,
}
"""Capability ID → rubric prompt.

Capabilities not in this dict (T1/T2/T3/T8 currently) cause
:class:`CapabilityLLMJudgeScorer` to return Score with ``value="NA"``
and reason "no rubric registered" — D32.1 selective coverage.

Extending coverage: add new entry to this dict. Scorer code, runner,
and Protocol stay 0 modification (D32 §四 future hook).
"""
