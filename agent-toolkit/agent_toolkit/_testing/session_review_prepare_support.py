"""セッション振り返りの試験（session_review_prepare_support）。"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._common import state_paths

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)


_LONG_INTERVENTION = "そうじゃなくて、対象は全部です。" + "理由の説明。" * 400 + "最後まで読んで。"


_BACKGROUND_OUTPUT_NOTICE = (
    '<atk-auto source="response-language" kind="block">'
    "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取った。完了通知を受けてから読むこと。</agent-toolkit-auto-inserted>"
)


@pytest.fixture(autouse=True)
def _isolate_failure_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """失敗署名の発生記録の保存先をテストごとの状態ディレクトリへ限定する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")  # pylint: disable=protected-access


def _work_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    return work_dir


def _bundle_rows(record: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    """準備結果が案内する保存証拠を読む。"""
    path = pathlib.Path(record["work_dir"]) / "bundle" / filename
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_claude_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """初期要求、英語の状況説明と`pretooluse`の警告通知、失敗したコマンド、2000文字を超えるユーザー介入を持つtranscriptを書き込む。"""
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will run the linter now."},
                    {"type": "tool_use", "name": "Bash", "id": "toolu_fail", "input": {"command": "make lint"}},
                ],
            },
        },
        {
            "type": "attachment",
            "timestamp": "2026-09-06T12:00:11Z",
            "attachment": {
                "type": "hook_additional_context",
                "hookName": "PreToolUse:Bash",
                "toolUseID": "toolu_fail",
                "content": [_BACKGROUND_OUTPUT_NOTICE],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:20Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_fail", "is_error": True, "content": "Error: 検査に失敗した"}
                ],
            },
        },
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": _LONG_INTERVENTION}},
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return transcript
