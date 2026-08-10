def compare(a: float, b: float) -> str:
    """比较两个数的大小, 返回描述性字符串。"""
    if a > b:
        return f"{a} > {b}"
    elif a < b:
        return f"{a} < {b}"
    else:
        return f"{a} == {b}"


if __name__ == "__main__":
    # 调试用例
    test_cases = [
        (3, 5),
        (7, 2),
        (4, 4),
        (-1, -3),
        (0, 0.0),
        (3.14, 3.14159),
    ]

    for a, b in test_cases:
        result = compare(a, b)
        print(f"compare({a}, {b}) => {result}")
