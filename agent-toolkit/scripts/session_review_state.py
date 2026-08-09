#!/usr/bin/env -S uv run --no-project --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""セッション振り返りの起動済み状態を記録する。"""

from __future__ import annotations

import argparse
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import _session_review_evidence  # noqa: E402  # pylint: disable=wrong-import-position,import-error
from _session_state import update_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_SESSION_REVIEW_SKILL = "agent-toolkit:session-review"


def _mark_invoked(state: dict) -> dict:
    invoked = state.get("session_review_invoked")
    if not isinstance(invoked, dict):
        invoked = {}
    invoked[_SESSION_REVIEW_SKILL] = True
    state["session_review_invoked"] = invoked
    return state


def main(argv: list[str] | None = None) -> int:
    """指定セッションへ振り返り起動済み状態を記録する。"""
    parser = argparse.ArgumentParser(description="セッション振り返りの起動済み状態を記録する。")
    parser.add_argument("session_id", help="フックpayloadから受け取った空でないセッション識別子。")
    args = parser.parse_args(argv)
    if not args.session_id.strip():
        parser.error("session_idには空でない値が必要")
    if not update_state(args.session_id, _mark_invoked):
        return 1
    print(_session_review_evidence.SESSION_REVIEW_STARTED_MARKER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
