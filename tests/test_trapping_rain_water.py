"""Boundary tests for trapping rain water (LeetCode #42).

Covers: empty, single element, two elements, monotonic sequences,
plateaus, V-shape, W-shape, classic case, and large input.
All cases verified against brute-force O(n²) reference or manual calculation.
"""

from __future__ import annotations

from trapping_rain_water import trap


# -- Reference implementation (small inputs only) -----------------------------

def _brute_force(height: list[int]) -> int:
    """O(n²) reference for cross-validation."""
    n = len(height)
    water = 0
    for i in range(n):
        left_max = max(height[: i + 1], default=0)
        right_max = max(height[i:], default=0)
        water += min(left_max, right_max) - height[i]
    return water


# -- Boundary test cases ------------------------------------------------------

class TestTrapBoundary:
    """Each test targets a specific boundary condition."""

    def test_empty(self) -> None:
        assert trap([]) == 0

    def test_single_element(self) -> None:
        assert trap([5]) == 0

    def test_two_elements_asc(self) -> None:
        assert trap([1, 3]) == 0

    def test_two_elements_desc(self) -> None:
        assert trap([3, 1]) == 0

    def test_two_elements_equal(self) -> None:
        assert trap([3, 3]) == 0

    def test_three_elements_v_shape(self) -> None:
        """Minimal basin: [1, 0, 1] traps 1 unit."""
        assert trap([1, 0, 1]) == 1

    def test_three_elements_plateau(self) -> None:
        assert trap([3, 3, 3]) == 0

    def test_monotone_increasing(self) -> None:
        assert trap([1, 2, 3, 4, 5]) == 0

    def test_monotone_decreasing(self) -> None:
        assert trap([5, 4, 3, 2, 1]) == 0

    def test_all_zeros(self) -> None:
        assert trap([0, 0, 0, 0]) == 0

    def test_symmetric_dip(self) -> None:
        """[2, 0, 2] traps 2 units."""
        assert trap([2, 0, 2]) == 2

    def test_deep_v_valley(self) -> None:
        """[5, 0, 5] traps 5 units."""
        assert trap([5, 0, 5]) == 5

    def test_w_shape(self) -> None:
        """[5, 1, 5, 1, 5] traps 8 units (two basins of 4 each)."""
        assert trap([5, 1, 5, 1, 5]) == 8

    def test_classic_leetcode(self) -> None:
        assert trap([0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]) == 6

    def test_two_high_walls(self) -> None:
        assert trap([4, 2, 0, 3, 2, 5]) == 9

    def test_left_high_right_lower(self) -> None:
        """[0, 7, 1, 4, 6] — water limited by right wall at 6."""
        assert trap([0, 7, 1, 4, 6]) == 7

    def test_single_spike_no_trap(self) -> None:
        """A single peak with no surrounding walls."""
        assert trap([0, 0, 5, 0, 0]) == 0

    def test_staircase_up_then_down(self) -> None:
        """[1, 2, 3, 2, 1] — pyramid shape, no water trapped."""
        assert trap([1, 2, 3, 2, 1]) == 0

    def test_flat_bottom_basin(self) -> None:
        """[3, 0, 0, 0, 3] traps 9 units (3 wide x 3 deep)."""
        assert trap([3, 0, 0, 0, 3]) == 9

    def test_asymmetric_basin(self) -> None:
        """[5, 1, 2, 1, 3] — right wall lower than left."""
        assert trap([5, 1, 2, 1, 3]) == 5

    def test_large_input_performance(self) -> None:
        """100k alternating elements use a closed-form expected result."""
        height = [0, 1] * 50_000

        assert trap(height) == 49_999

    def test_cross_validate_random_shapes(self) -> None:
        """Cross-validate several non-trivial shapes against brute force."""
        cases = [
            [2, 0, 1, 0, 2],
            [4, 2, 3, 1, 4],
            [1, 3, 0, 2, 1, 3],
            [0, 3, 0, 3, 0, 3, 0],
        ]
        for h in cases:
            assert trap(h) == _brute_force(h), f"Mismatch for {h}"
