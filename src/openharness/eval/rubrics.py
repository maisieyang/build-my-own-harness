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


CAPABILITY_RUBRICS: dict[str, str] = {
    "T4": _T4_RUBRIC,
    "T5": _T5_RUBRIC,
    "T6": _T6_RUBRIC,
    "T7": _T7_RUBRIC,
}
"""Capability ID → rubric prompt.

Capabilities not in this dict (T1/T2/T3/T8 currently) cause
:class:`CapabilityLLMJudgeScorer` to return Score with ``value="NA"``
and reason "no rubric registered" — D32.1 selective coverage.

Extending coverage: add new entry to this dict. Scorer code, runner,
and Protocol stay 0 modification (D32 §四 future hook).
"""
