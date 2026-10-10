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
    return [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": call_id,
                        "input": {"command": "atk run-script session-review-evidence"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": call_id, "is_error": True, "content": diagnostic}],
            },
        },
    ]
