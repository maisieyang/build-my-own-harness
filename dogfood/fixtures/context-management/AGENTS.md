# Context Management Probe

- 这个目录只用于 OpenHarness context-management dogfood。
- 未经用户明确要求，不修改任何文件。
- 如果用户要求验证，精确命令是
  `uv run pytest test_context_probe.py -q --no-cov`。
- `large_context.txt` 是生成的观察材料，不是需要修复的生产文件。
