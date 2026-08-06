from openharness.eval.permission_goal_lifecycle import status_line_contains


def test_status_parser_accepts_non_tty_prompt_prefix() -> None:
    assert status_line_contains(
        ">>> (approved exact request abc123; use /resume)\n",
        "(approved exact request",
    )
    assert status_line_contains(">>> (resumed: 4 messages)\n", "(resumed:")
    assert status_line_contains(">>> [Write] → wrote 19 bytes\n", "[Write] → wrote")


def test_status_parser_does_not_accept_unrelated_text() -> None:
    assert not status_line_contains("goal restored\n", "(approved exact request")
    assert not status_line_contains(
        "The transcript says (approved exact request but this is model text.\n",
        "(approved exact request",
    )
