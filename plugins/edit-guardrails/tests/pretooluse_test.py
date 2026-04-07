"""plugins/edit-guardrails/scripts/pretooluse.py のテスト。

PreToolUse 統合フック (mojibake / ps1 EOL) のテスト。
独立スクリプトなので subprocess で起動し exit code と stderr を検証する。
"""

import json
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pretooluse.py"


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


class TestLockfilesCheck:
    """lockfile / 生成物ディレクトリの直接編集ブロック。"""

    @pytest.mark.parametrize(
        "file_path",
        [
            "uv.lock",
            "/home/user/proj/uv.lock",
            "pnpm-lock.yaml",
            "sub/pnpm-lock.yaml",
            "package-lock.json",
            "yarn.lock",
            "Cargo.lock",
            "crates/sub/Cargo.lock",
            "mise.lock",
            ".venv/lib/python3.12/site-packages/x.py",
            "node_modules/pkg/index.js",
        ],
    )
    def test_write_blocked(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 2
        assert "直接編集は禁止" in result.stderr

    def test_edit_cargo_lock_blocked(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "Cargo.lock", "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 2
        assert "cargo add" in result.stderr

    def test_normal_file_allowed(self):
        """lockfile 名を部分的に含むだけのパスは通す (例: uv.lock.bak)。"""
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "uv.lock.bak", "content": "x"}})
        assert result.returncode == 0


class TestSecretsCheck:
    """シークレット/鍵ファイルの直接編集ブロック。"""

    @pytest.mark.parametrize(
        "file_path",
        [
            ".env",
            ".env.local",
            "app/.env.production",
            ".encrypt_key",
            ".secret_key",
            "github_action",
            "keys/github_action.pub",
            "certs/server.pem",
            "private.key",
        ],
    )
    def test_blocked(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 2
        assert "シークレット" in result.stderr

    @pytest.mark.parametrize(
        "file_path",
        [
            ".env.example",
            ".env.sample",
            "config.env-example",
            "private-sample",
        ],
    )
    def test_example_allowed(self, file_path: str):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}})
        assert result.returncode == 0


class TestManifestCheck:
    """manifest 手編集の警告 (warn のみ、exit code は 0)。"""

    def test_pyproject_toml_warns(self):
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "pyproject.toml", "old_string": "a", "new_string": "b"},
            }
        )
        assert result.returncode == 0
        assert "pyproject.toml" in result.stderr
        assert "uv add" in result.stderr

    def test_package_json_warns(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "app/package.json", "content": "{}"},
            }
        )
        assert result.returncode == 0
        assert "package.json" in result.stderr
        assert "pnpm add" in result.stderr

    def test_normal_file_no_warn(self):
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "foo.txt", "content": "x"}})
        assert result.returncode == 0
        assert result.stderr == ""


class TestHomePathCheck:
    """ホームディレクトリ絶対パス混入の警告 (warn のみ)。"""

    _HOME = str(pathlib.Path.home())

    def test_home_path_in_content_warns(self):
        content = f"config_path = '{self._HOME}/myproj/config.yaml'\n"
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "src/app.py", "content": content}})
        assert result.returncode == 0
        assert "ホームディレクトリ" in result.stderr

    def test_home_path_in_local_md_skipped(self):
        content = f"See {self._HOME}/proj for details."
        result = _run({"tool_name": "Write", "tool_input": {"file_path": "CLAUDE.local.md", "content": content}})
        assert result.returncode == 0
        assert result.stderr == ""

    def test_home_path_in_settings_local_json_skipped(self):
        content = f'{{"path": "{self._HOME}/x"}}'
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": ".claude/settings.local.json", "content": content},
            }
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_no_home_path_no_warn(self):
        result = _run(
            {
                "tool_name": "Write",
                "tool_input": {"file_path": "src/app.py", "content": "x = '/other/path'\n"},
            }
        )
        assert result.returncode == 0
        assert result.stderr == ""

    def test_home_path_does_not_block(self):
        """warn なので exit code は 0 のまま (block にならない)。"""
        result = _run(
            {
                "tool_name": "Edit",
                "tool_input": {"file_path": "README.md", "old_string": "a", "new_string": f"{self._HOME}/x"},
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
