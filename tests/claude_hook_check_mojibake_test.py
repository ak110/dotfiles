"""scripts/claude_hook_check_mojibake.py のテスト。

独立スクリプト化されたため subprocess で起動し、exit code と stderr を検証する。
"""

import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_check_mojibake.py"


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    """スクリプトを subprocess で起動し CompletedProcess を返す。"""
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )


class TestBlockedCases:
    """文字化けを検出して exit 2 でブロックするケース。"""

    def test_write_with_mojibake(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"},
            }
        )
        assert result.returncode == 2
        assert "U+FFFD" in result.stderr

    def test_edit_with_mojibake(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/tmp/a.txt",
                    "old_string": "foo",
                    "new_string": "bar\ufffd",
                },
            }
        )
        assert result.returncode == 2

    def test_multiedit_with_mojibake(self):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "/tmp/a.txt",
                    "edits": [
                        {"old_string": "a", "new_string": "b"},
                        {"old_string": "c", "new_string": "\ufffd"},
                    ],
                },
            }
        )
        assert result.returncode == 2


class TestAllowedCases:
    """exit 0 で通すべきケース。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # 既存文字化けを修復する Edit を妨げない (old_string は検査対象外)
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "/tmp/a.txt",
                    "old_string": "壊れた\ufffd文字",
                    "new_string": "壊れた文字",
                },
            },
            # MultiEdit でも old_string の文字化けは検査対象外
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "/tmp/a.txt",
                    "edits": [{"old_string": "\ufffd", "new_string": "正常"}],
                },
            },
            # 正常な日本語は通す
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "/tmp/a.txt", "content": "こんにちは世界"},
            },
            # Write/Edit/MultiEdit 以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo \ufffd"}},
            # tool_input が欠落していても通す
            {"tool_name": "Write"},
        ],
    )
    def test_allowed(self, payload: dict):
        result = _run(payload)
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正 JSON はフックを無効化 (安全側)。"""
        result = _run("this is not json")
        assert result.returncode == 0
