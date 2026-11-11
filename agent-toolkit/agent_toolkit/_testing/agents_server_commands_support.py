"""commandsの分割試験が共有する準備処理。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit._agents_server import shared_layout
from agent_toolkit._atk import environment


def _write_jsonl(path: pathlib.Path, records: list[dict]) -> None:
    """公開CLI用の保存済み記録を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")


def _write_root_sessions(state_root: pathlib.Path, root_session_id: str, sessions: list[dict]) -> None:
    directory = shared_layout.status_directory(root_session_id, state_root)
    directory.mkdir(parents=True)
    (directory / "root.json").write_text(json.dumps({"version": 1, "sessions": sessions}), encoding="utf-8")


_LONG_REPLY = "長" * 2100


def _write_mixed_claude_record(tmp_path: pathlib.Path) -> None:
    """発話、思考、ツール呼び出し、成功・失敗したツール結果、長い本文を含むClaude Codeの記録を作成する。"""
    _write_jsonl(
        tmp_path / "projects" / "sample" / "session-1.jsonl",
        [
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:00Z",
                "message": {"content": [{"type": "text", "text": "調査して"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-24T00:00:01Z",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "思考の本文"},
                        {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"command": "echo hi"}},
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:02Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_a", "content": "成功した出力"}]},
            },
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:03Z",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "toolu_b", "content": "失敗した出力", "is_error": True}]
                },
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-24T00:00:04Z",
                "message": {"content": [{"type": "text", "text": _LONG_REPLY}]},
            },
        ],
    )


def _human_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in environment.AGENT_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)
