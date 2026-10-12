"""失敗署名と準備の試験が共有する、実行系ごとの失敗記録を作成する。"""

from typing import Any


def failure_entries(kind: str, diagnostic: str, call_id: str) -> list[dict[str, Any]]:
    """コマンド失敗またはClaudeのツール失敗を同じコマンド・終了コードで返す。"""
    if kind == "command-failure":
        return [
            {
                "type": "event_msg",
                "payload": {
                    "type": "item_completed",
                    "item": {
                        "type": "CommandExecution",
                        "status": "failed",
                        "command": ["atk", "run-script", "session-review-evidence"],
                        "exit_code": 2,
                        "stderr": diagnostic,
                    },
                },
            }
        ]
    return bash_failure_entries("atk run-script session-review-evidence", diagnostic, call_id)


def bash_failure_entries(command: str, output: str, call_id: str) -> list[dict[str, Any]]:
    """ClaudeのBashが`command`を実行して失敗し、`output`をツール結果として返した記録を返す。

    Claude Code 2.1.288の記録では、非0終了の結果本文は`Exit code <N>`の行と出力を改行でつないだ形である。
    """
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": output}],
            },
        },
    ]
