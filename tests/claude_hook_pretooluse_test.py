"""scripts/claude_hook_pretooluse.py のテスト。

dotfiles 個人環境専用の PreToolUse フック (警告のみ・非ブロック) のテスト。
mojibake / PS1 EOL は plugin 側 (plugins/edit-guardrails) に移管済み。
独立スクリプトなので subprocess で起動し exit code と stderr を検証する。
"""

import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "claude_hook_pretooluse.py"

# 文字列リテラルで直接書くと本ファイル自身が警告を出す原因になるため、
# テスト対象の「言及される名前」はプログラム的に組み立てる。
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


class TestLocalMdReferenceWarning:
    """ローカル専用ファイル言及検出 (警告のみ・非ブロック)。"""

    def test_content_reference_warns_but_passes(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "docs/guide.md", "content": f"See {_LOCAL_MD} for details."},
            }
        )
        assert result.returncode == 0
        assert _LOCAL_MD in result.stderr
        assert "warn" in result.stderr.lower()

    def test_edit_reference_warns_but_passes(self):
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
        assert result.returncode == 0
        assert _LOCAL_MD in result.stderr

    def test_multiedit_reference_warns_but_passes(self):
        result = _run(
            {
                "tool_name": "MultiEdit",
                "tool_input": {
                    "file_path": "README.md",
                    "edits": [
                        {"old_string": "a", "new_string": "b"},
                        {"old_string": "c", "new_string": f"See {_LOCAL_MD}"},
                    ],
                },
            }
        )
        assert result.returncode == 0
        assert _LOCAL_MD in result.stderr

    def test_editing_target_file_itself_is_allowed_silently(self):
        """対象ファイル自体の編集は正当な操作として警告も出さない。"""
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": f"/home/user/proj/{_LOCAL_MD}", "content": f"# {_LOCAL_MD}\nmemo"},
            }
        )
        assert result.returncode == 0
        assert _LOCAL_MD not in result.stderr

    def test_editing_target_file_with_windows_path_allowed_silently(self):
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
        assert _LOCAL_MD not in result.stderr

    def test_old_string_reference_is_allowed_silently(self):
        """言及を削除する Edit は old_string に書いてあっても警告しない。"""
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
        assert _LOCAL_MD not in result.stderr


class TestGeneralBehavior:
    """共通の振る舞い。"""

    @pytest.mark.parametrize(
        "payload",
        [
            # Write/Edit/MultiEdit 以外は全て通す
            {"tool_name": "Bash", "tool_input": {"command": "echo test"}},
            # tool_input が欠落していても通す
            {"tool_name": "Write"},
            # 通常の日本語は通す
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

    def test_mojibake_no_longer_blocks(self):
        """mojibake チェックは plugin 側に移管されたため dotfiles 側では通す。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "a.txt", "content": "hello \ufffd world"}})
        assert result.returncode == 0

    def test_ps1_lf_no_longer_blocks(self):
        """PS1 EOL チェックは plugin 側に移管されたため dotfiles 側では通す。"""
        result = _run(
            {"tool_name": "Write", "tool_input": {"file_path": "a.ps1", "content": "Set-StrictMode\nWrite-Host 'x'\n"}}
        )
        assert result.returncode == 0
