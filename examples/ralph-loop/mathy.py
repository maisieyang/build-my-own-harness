"""A tiny module with deliberate bugs for the Ralph-loop demo.

Ships buggy on purpose: run `bash ralph.sh` and watch the loop drive it green.
`git checkout examples/ralph-loop/mathy.py` resets it to buggy to re-run.
"""


def add(a: int, b: int) -> int:
    return a + b


def is_even(n: int) -> bool:
    return n % 2 == 0
