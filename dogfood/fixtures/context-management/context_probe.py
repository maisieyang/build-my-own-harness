"""Deterministic executable surface for context-management dogfood."""

from __future__ import annotations

import argparse
from pathlib import Path


def read_anchors(path: Path) -> tuple[str, str, str]:
    text = path.read_text(encoding="utf-8")
    anchors = [line for line in text.splitlines() if "_ANCHOR=" in line]
    if len(anchors) != 3:
        raise ValueError(f"expected 3 anchors, found {len(anchors)}")
    return anchors[0], anchors[1], anchors[2]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("anchors", "large-output", "fail"))
    args = parser.parse_args()
    large_file = Path(__file__).with_name("large_context.txt")
    if args.action == "anchors":
        print("\n".join(read_anchors(large_file)))
        return 0
    if args.action == "large-output":
        print(large_file.read_text(encoding="utf-8"), end="")
        return 0
    print("PROBE_ERROR=expected-context-failure")
    return 7


if __name__ == "__main__":
    raise SystemExit(main())
