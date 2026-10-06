#!/usr/bin/env python3
"""停滞検知を完了したTaskStop対象をセッション状態へ記録する。"""

from __future__ import annotations

import argparse

from agent_toolkit._common import next_action as _next_action
from agent_toolkit._hooks.session_state import state_path
from agent_toolkit._hooks.task_stop_state import record_completion


def _identifier(value: str) -> str:
    """空白と制御文字を含まない識別子だけを受理する。"""
    if not value or not value.isprintable() or any(character.isspace() for character in value):
        raise argparse.ArgumentTypeError("識別子は空白と制御文字を含まない非空文字列にする")
    return value


def main() -> int:
    """引数を検証し、対象タスクの停滞検知完了を記録する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True, type=_identifier, help="停滞検知を記録するセッションのID")
    parser.add_argument(
        "--task-id", required=True, type=_identifier, help="停滞検知を完了したバックグラウンドタスクのID（TaskStopの対象）"
    )
    args = parser.parse_args()
    if not record_completion(args.session_id, args.task_id):
        _next_action.report(
            f"セッション状態へ記録できなかった: {state_path(args.session_id)}",
            next_action=(
                "記録先のディレクトリが存在し書き込めることを確かめ、同じ引数で再実行する。"
                "記録できないまま`TaskStop`を実行すると遮断されるため、停止は記録に成功してから行う"
            ),
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
