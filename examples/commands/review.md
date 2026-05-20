---
name: review
description: Code-review prompt template — focus on correctness, readability, security
---
Please review the following code change for correctness, readability,
and security implications:

{args}

Focus on:

- Edge cases not covered by the change
- Naming + readability
- Security concerns (input validation, secret leakage, injection)
- Whether the change matches its stated intent

Be concrete: cite specific lines, suggest concrete diffs where useful.
Skip pleasantries; this is a working review, not a writeup.
