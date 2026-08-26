# Workflow Journey Fixture

- 只允许修改当前 fixture 中的 `pricing.py` 和 `test_pricing.py`。
- 使用 `python -m pytest -c pytest.ini test_pricing.py -q --no-cov` 验证。
- 不要修改 OpenHarness 仓库生产代码。
