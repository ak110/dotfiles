"""scripts/claude_hook_pretooluse.py のテスト。

統合 PreToolUse フック (mojibake / ps1 EOL / 特定ファイル名言及) のテスト。
独立スクリプトなので subprocess で起動し exit code と stderr を検証する。
"""

import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_pretooluse.py"

# 文字列リテラルで直接書くと本ファイルがフックにブロックされるため、
# テスト対象の「禁止される言及」はプログラム的に組み立てる。
_LOCAL_MD = "CLAUDE" + ".local.md"


def _run(payload: object) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
    )


class TestMojibakeCheck:
    """文字化け (U+FFFD) 検出。"""

    def test_write_with_mojibake(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "/tmp/a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 2
        assert "U+FFFD" in result.stderr

    def test_edit_with_mojibake(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/a.txt", "old_string": "foo", "new_string": "bar\ufffd"},
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

    def test_old_string_mojibake_is_allowed(self):
        """old_string 内の文字化けは既存修復を妨げないため通す。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "/tmp/a.txt", "old_string": "壊れた\ufffd文字", "new_string": "壊れた文字"},
            }
        )
        assert result.returncode == 0


class TestPs1EolCheck:
    """PowerShell ファイルへの LF-only 書き込み検出。"""

    def test_ps1_with_lf_only_blocks(self):
        content = "Set-StrictMode\nWrite-Host 'x'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "C:/x/a.ps1", "content": content}})
        assert result.returncode == 2
        assert "LF 改行" in result.stderr

    def test_ps1_tmpl_with_lf_only_blocks(self):
        content = "Set-StrictMode\n{{ .chezmoi.homeDir }}\n"
        result = _run({"tool_name": "Edit", "tool_input": {"file_path": "./a.ps1.tmpl", "new_string": content}})
        assert result.returncode == 2

    def test_ps1_with_crlf_allowed(self):
        content = "Set-StrictMode\r\nWrite-Host 'x'\r\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": content}})
        assert result.returncode == 0

    def test_non_ps1_with_lf_only_allowed(self):
        """対象拡張子でなければ LF-only は関知しない。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "hello\nworld\n"}})
        assert result.returncode == 0

    def test_ps1_single_line_edit_allowed(self):
        """改行を含まない 1 行の Edit は誤検出を避けて通す。"""
        result = _run({"tool_name": "Edit", "tool_input": {"file_path": "a.ps1", "old_string": "Old", "new_string": "New"}})
        assert result.returncode == 0


class TestLocalMdReferenceCheck:
    """ローカル専用ファイル言及検出 (ファイル名はリテラルで書かない)。"""

    def test_content_reference_is_blocked(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "docs/guide.md", "content": f"See {_LOCAL_MD} for details."},
            }
        )
        assert result.returncode == 2
        assert _LOCAL_MD in result.stderr

    def test_edit_reference_is_blocked(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "README.md",
                    "old_string": "foo",
                    "new_string": f"Refer to {_LOCAL_MD}",
                },
            }
        )
        assert result.returncode == 2

    def test_editing_target_file_itself_is_allowed(self):
        """対象ファイル自体の編集は正当な操作として通す。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": f"/home/user/proj/{_LOCAL_MD}", "content": f"# {_LOCAL_MD}\nmemo"},
            }
        )
        assert result.returncode == 0

    def test_editing_target_file_with_windows_path_allowed(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {
                    "file_path": f"C:\\proj\\{_LOCAL_MD}",
                    "content": f"memo {_LOCAL_MD}",
                },
            }
        )
        assert result.returncode == 0

    def test_old_string_reference_is_allowed(self):
        """言及を削除する Edit は old_string に書いてあっても通す。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {
                    "file_path": "README.md",
                    "old_string": f"See {_LOCAL_MD}",
                    "new_string": "See docs",
                },
            }
        )
        assert result.returncode == 0


class TestGeneralBehavior:
    """統合スクリプト共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit 以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo \ufffd"}},
            # tool_input が欠落していても通す
            {"tool_name": "Write"},
            # 正常な日本語は通す
            {"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "こんにちは世界"}},
        ],
    )
    def test_allowed(self, payload: dict):
        result = _run(payload)
        assert result.returncode == 0

    def test_invalid_json(self):
        """不正 JSON はフックを無効化 (安全側)。"""
        result = _run("this is not json")
        assert result.returncode == 0
