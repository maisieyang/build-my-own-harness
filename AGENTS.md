# AGENTS.md

## 信息来源

日常开发只使用当前事实：

- 生产代码和测试定义精确行为；
- `README.md` 描述当前产品、架构和使用方式；
- `evals/*/dataset_card.md` 定义对应能力的评测契约。

`decisions/`、`tasks/`、`learnings/` 和 `REFERENCE.md` 是历史资料。只有在追问
“为什么这样设计”时才读取，不把它们当作当前行为或新工作的默认依据。

## 开发

- 行为变更和缺陷修复采用 TDD：先新增或更新测试，确认测试因目标缺口而 RED，
  再修改生产代码到 GREEN。不得仅为通过测试而弱化断言。
- 修改 prompt、工具描述、上下文组装、judge 或其他依赖模型输出的行为时，按对应
  `evals/<capability>/dataset_card.md` 的 reference policy、pass bar 和复跑方式
  验证。Replay 只验证已录制行为与 scorer，不代替模型行为变化后的 live
  re-ratification。

## 验证

```bash
uv run pytest -m "not integration and not eval" -q
uv run mypy --strict src/
uv run ruff check
uv run ruff format --check
```
