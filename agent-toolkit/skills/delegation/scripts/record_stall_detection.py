#!/usr/bin/env python3
"""停滞検知を完了したTaskStop対象をセッション状態へ記録する。"""

from __future__ import annotations

import argparse

from agent_toolkit._hooks.task_stop_state import record_completion


def _identifier(value: str) -> str:
    """空白と制御文字を含まない識別子だけを受理する。"""
    if not value or not value.isprintable() or any(character.isspace() for character in value):
        raise argparse.ArgumentTypeError("識別子は空白と制御文字を含まない非空文字列にする")
    return value


def main() -> int:
    """引数を検証し、対象タスクの停滞検知完了を記録する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True, type=_identifier)
    parser.add_argument("--task-id", required=True, type=_identifier)
    args = parser.parse_args()
    if not record_completion(args.session_id, args.task_id):
        parser.error("セッション状態へ記録できなかった")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
