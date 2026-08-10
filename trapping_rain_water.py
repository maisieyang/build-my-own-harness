def trap(height: list[int]) -> int:
    """双指针法计算接雨水总量。

    从两端向中间收缩，始终处理较矮一侧：较矮侧的积水仅取决于
    该侧历史最大值，因为对面必有不低于当前较高柱的屏障。

    Args:
        height: 非负整数列表，表示每个宽度为 1 的柱子高度。

    Returns:
        能接住的雨水总量。

    Complexity:
        Time O(n), Space O(1).
    """
    if len(height) < 3:
        return 0

    left, right = 0, len(height) - 1
    left_max = right_max = 0
    water = 0

    while left < right:
        if height[left] < height[right]:
            if height[left] > left_max:
                left_max = height[left]
            else:
                water += left_max - height[left]
            left += 1
        else:
            if height[right] > right_max:
                right_max = height[right]
            else:
                water += right_max - height[right]
            right -= 1

    return water
