"""plugins/edit-guardrails/scripts/pretooluse.py のテスト。

PreToolUse 統合フック (mojibake / ps1 EOL / Bash mkdir auto-allow 等) のテスト。
独立スクリプトなので subprocess で起動し exit code・stderr・stdout を検証する。
"""

import json
import os
import pathlib
import subprocess
import sys

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "pretooluse.py"
_PLUGIN_MANIFEST = pathlib.Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
_MARKETPLACE_MANIFEST = pathlib.Path(__file__).resolve().parents[3] / ".claude-plugin" / "marketplace.json"


def _run(payload: object, env_overrides: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    text = payload if isinstance(payload, str) else json.dumps(payload)
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=text,
        capture_output=True,
        text=True,
        check=False,
        env=env,
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


class TestBashPlansMkdirAutoAllow:
    """Bash `mkdir -p ~/.claude/plans` の自動許可 check (allow)。

    許可時は stdout に `hookSpecificOutput.permissionDecision = "allow"` を
    含む JSON を出力する。許可対象外は stdout 空のまま exit 0 を返す
    (= フック介入なしで通常の許可判定に戻る)。

    テストでは HOME 環境変数を `tmp_path` に差し替えることでプラグイン配布先の
    ホーム環境を擬似再現し、plans ディレクトリの存在有無による挙動を検証する。
    """

    @pytest.fixture(name="fake_home")
    def _fake_home(self, tmp_path: pathlib.Path) -> dict[str, str]:
        """HOME を tmp_path に差し替えたフック実行 env を返す fixture。

        ~/.claude ディレクトリは作成するが、plans はサブテストで個別に作る。
        """
        (tmp_path / ".claude").mkdir()
        return {"HOME": str(tmp_path)}

    def _invoke(self, command: str, env_overrides: dict[str, str]) -> subprocess.CompletedProcess[str]:
        return _run({"tool_name": "Bash", "tool_input": {"command": command}}, env_overrides=env_overrides)

    def _has_allow_decision(self, result: subprocess.CompletedProcess[str]) -> bool:
        if result.returncode != 0 or not result.stdout.strip():
            return False
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        hook_output = data.get("hookSpecificOutput") or {}
        return hook_output.get("permissionDecision") == "allow"

    @pytest.mark.parametrize(
        "command",
        [
            "mkdir -p ~/.claude/plans",
            "mkdir --parents ~/.claude/plans",
            "/usr/bin/mkdir -p ~/.claude/plans",
        ],
    )
    def test_allowed_when_plans_dir_exists(self, fake_home: dict[str, str], tmp_path: pathlib.Path, command: str):
        """plans ディレクトリが存在する場合の冗長 mkdir は自動許可される。"""
        (tmp_path / ".claude" / "plans").mkdir()
        result = self._invoke(command, fake_home)
        assert result.returncode == 0, result.stderr
        assert self._has_allow_decision(result), f"allow 判定が出ていない: stdout={result.stdout!r}"

    def test_allowed_with_absolute_home_path(self, fake_home: dict[str, str], tmp_path: pathlib.Path):
        """絶対パス形式 (tilde 展開後と等価) でも許可される。"""
        (tmp_path / ".claude" / "plans").mkdir()
        absolute = str(tmp_path / ".claude" / "plans")
        result = self._invoke(f"mkdir -p {absolute}", fake_home)
        assert result.returncode == 0
        assert self._has_allow_decision(result)

    def test_not_allowed_when_plans_dir_missing(self, fake_home: dict[str, str]):
        """plans ディレクトリが存在しない場合は許可しない (配布先新規環境の安全性保証)。"""
        result = self._invoke("mkdir -p ~/.claude/plans", fake_home)
        assert result.returncode == 0
        assert not self._has_allow_decision(result), f"plans 未作成でも自動許可してしまった: stdout={result.stdout!r}"

    @pytest.mark.parametrize(
        "command",
        [
            # 別パス
            "mkdir -p /tmp/foo",
            "mkdir -p ~/.claude/plans/sub",
            "mkdir -p ~/.claude",
            # 合成・危険
            "mkdir -p ~/.claude/plans && rm -rf /",
            "mkdir -p $(echo ~/.claude/plans)",
            "mkdir -p ~/.claude/plans; ls",
            "mkdir -p ~/.claude/plans | cat",
            "mkdir -p `pwd`",
            # 形式違反
            "mkdir ~/.claude/plans",
            "mkdir -pv ~/.claude/plans",
            "mkdir -p ~/.claude/plans /tmp/foo",
            "mkdir -p",
        ],
    )
    def test_not_allowed_patterns(self, fake_home: dict[str, str], tmp_path: pathlib.Path, command: str):
        """パス違反・合成コマンド・形式違反は全て許可されない。"""
        (tmp_path / ".claude" / "plans").mkdir()
        result = self._invoke(command, fake_home)
        assert result.returncode == 0
        assert not self._has_allow_decision(result), (
            f"本来許可されないコマンドが通過した: command={command!r} stdout={result.stdout!r}"
        )

    def test_unrelated_bash_command_untouched(self, fake_home: dict[str, str]):
        """mkdir 以外の Bash コマンドはフック介入なしで通す (stdout 空)。"""
        result = self._invoke("echo hello", fake_home)
        assert result.returncode == 0
        assert result.stdout == ""


class TestManifestSsot:
    """plugin.json と marketplace.json の SSOT 整合性。

    version / description / name を 2 箇所で重複管理しているため、
    片方だけ更新して配布されない事故を防ぐためのハード チェック。
    """

    def test_plugin_manifest_matches_marketplace(self):
        plugin_manifest = json.loads(_PLUGIN_MANIFEST.read_text(encoding="utf-8"))
        marketplace = json.loads(_MARKETPLACE_MANIFEST.read_text(encoding="utf-8"))

        entries = [p for p in marketplace["plugins"] if p["name"] == plugin_manifest["name"]]
        assert len(entries) == 1, f"marketplace.json に {plugin_manifest['name']} のエントリが 1 件ではない"
        entry = entries[0]

        # SSOT の 3 フィールドが完全一致することを要求する。
        # 不一致が出たら .claude/rules/plugins.md を参照して両側を揃えること。
        assert entry["version"] == plugin_manifest["version"], (
            f"version 不一致: plugin.json={plugin_manifest['version']} marketplace.json={entry['version']}"
        )
        assert entry["description"] == plugin_manifest["description"], (
            "description 不一致: plugin.json と marketplace.json を揃えること"
        )
        assert entry["name"] == plugin_manifest["name"]
