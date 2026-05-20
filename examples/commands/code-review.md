---
name: code-review
description: Read-only code review using the `read-only` bundle (Phase 5d demo)
mode: read-only
---
Read-only code review of the following files / change:

{args}

Constraints (enforced by the `read-only` bundle, not by prompt
discipline alone):

- You can ONLY use Read / Grep — no Write / Edit / Bash.
- `secrets/**` and `*.env` are blocked at the permission layer.
- Every tool dispatch is logged with `event=audit_tool_complete`
  (run with `OPENHARNESS_LOG_FORMAT=json` to see).

Produce findings in this shape:

```
[file:line] severity — observation
  why it matters: ...
  suggested fix: ...
```

Don't editorialize.
