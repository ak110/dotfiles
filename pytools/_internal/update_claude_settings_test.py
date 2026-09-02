"""update_claude_settings モジュールのテスト。"""

import json
import logging
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pytools._internal import update_claude_settings as mod
from pytools._internal._test_helpers import run_update_claude_settings
from pytools._internal.update_claude_settings import update_claude_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.json"
_PROD_MANAGED_CONFIG = _REPO_ROOT / "share" / "claude_json_managed.json"
_PROD_MANAGED_SETTINGS_FILES = (
    _PROD_MANAGED_SETTINGS,
    _PROD_MANAGED_SETTINGS.with_suffix(".posix.json"),
    _PROD_MANAGED_SETTINGS.with_suffix(".win32.json"),
)
_PROD_PRETOOLUSE_SCRIPT = _REPO_ROOT / "scripts" / "claude-hook-pretooluse.ps1"
_LEGACY_WINDOWS_PRETOOLUSE_COMMAND = (
    'powershell -NoProfile -ExecutionPolicy Bypass -Command "& { uv run --no-project --script '
    "'__HOME__\\dotfiles\\scripts\\claude_hook.py' pretooluse; "
    'if ($LASTEXITCODE -eq 2) { exit 2 } else { exit 0 } }"'
)
_LEGACY_WINDOWS_AUTONOMOUS_EXIT_COMMAND = (
    'powershell -NoProfile -ExecutionPolicy Bypass -Command "& { uv run --no-project --script '
    "'__HOME__\\dotfiles\\scripts\\claude_hook.py' autonomous_exit; exit 0 }\""
)

MANAGED_ALLOW = [
    "Bash",
    "Edit",
    "Glob",
    "Grep",
    "MultiEdit",
    "NotebookEdit",
    "Read",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
    "Write",
    "mcp__serena",
    "mcp__plugin_serena_serena",
]
MANAGED_DENY = ["Read(*.key)", "Read(*.crt)", "Read(//**/.credentials.json)"]
MANAGED = {
    "language": "japanese",
    "permissions": {
        "allow": MANAGED_ALLOW,
        "deny": MANAGED_DENY,
        "defaultMode": "plan",
    },
}


def _run(tmp_path: Path, managed: dict, existing: dict | None = None) -> dict:
    """update_claude_settings でマージしてターゲット結果を返す。"""
    return run_update_claude_settings(tmp_path, managed, existing)


class TestUpdateClaudeSettings:
    """~/.claude/settings.json 向けマージテスト。"""

    def test_new_file(self, tmp_path: Path):
        """settings.json が存在しない場合、managed 設定がそのまま出力される。"""
        result = _run(tmp_path, MANAGED)
        assert result["language"] == "japanese"
        assert result["permissions"]["defaultMode"] == "plan"
        assert result["permissions"]["allow"] == MANAGED_ALLOW
        assert result["permissions"]["deny"] == MANAGED_DENY

    def test_managed_plugin_disable_is_applied(self, tmp_path: Path):
        """配布原本のプラグイン無効化は既存設定へマージされる。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"enabledPlugins": {"hookify@claude-plugins-official": True}})
        assert result["enabledPlugins"]["hookify@claude-plugins-official"] is False

    def test_merge_preserves_existing_keys(self, tmp_path: Path):
        """既存キーが保持され、permissions が正しく union マージされる。"""
        existing = {
            "enabledPlugins": {"foo@bar": True},
            "remote": {"enabled": True},
            "permissions": {
                "allow": ["CustomTool", "Bash"],
                "deny": ["Read(./.secret)"],
            },
        }
        result = _run(tmp_path, MANAGED, existing)

        # 既存キーが保持されている
        assert result["enabledPlugins"] == {"foo@bar": True}
        assert result["remote"] == {"enabled": True}

        # allow: 既存順維持 + managed の新規追加、重複排除
        allow: list[str] = result["permissions"]["allow"]
        assert allow[0] == "CustomTool"
        assert allow[1] == "Bash"
        assert allow.count("Bash") == 1
        for item in MANAGED_ALLOW:
            assert item in allow

        # deny: union マージ
        deny: list[str] = result["permissions"]["deny"]
        assert "Read(./.secret)" in deny
        for item in MANAGED_DENY:
            assert item in deny

        # defaultMode が追加される
        assert result["permissions"]["defaultMode"] == "plan"

    def test_env_merge_preserves_existing(self, tmp_path: Path):
        """env は dict として再帰マージされ、既存キーを壊さない。"""
        managed = {"env": {"CLAUDE_CODE_NO_FLICKER": "1"}}
        existing = {"env": {"FOO": "bar"}}
        result = _run(tmp_path, managed, existing)
        assert result["env"] == {"FOO": "bar", "CLAUDE_CODE_NO_FLICKER": "1"}


class TestHomePlaceholder:
    """`__HOME__`プレースホルダーがホームディレクトリ絶対パスへ置換されることを検証する。"""

    def test_placeholder_replaced_in_string(self, tmp_path: Path):
        managed = {"statusLine": {"command": "__HOME__/.local/bin/claude-statusline statusline"}}
        result = _run(tmp_path, managed)
        assert result["statusLine"]["command"] == f"{Path.home()}/.local/bin/claude-statusline statusline"

    def test_placeholder_replaced_recursively_in_nested_structures(self, tmp_path: Path):
        managed = {"hooks": {"Stop": [{"hooks": [{"command": "__HOME__/x"}]}]}}
        result = _run(tmp_path, managed)
        assert result["hooks"]["Stop"][0]["hooks"][0]["command"] == f"{Path.home()}/x"

    def test_string_without_placeholder_kept_as_is(self, tmp_path: Path):
        managed = {"language": "japanese"}
        result = _run(tmp_path, managed)
        assert result["language"] == "japanese"

    def test_windows_path_backslash_normalized_to_forward_slash(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("C:\\Users\\test")))
        managed = {"statusLine": {"command": "__HOME__/.local/bin/claude-statusline.exe statusline"}}
        result = _run(tmp_path, managed)
        assert "\\" not in result["statusLine"]["command"]

    def test_windows_path_result_uses_forward_slash_format(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("C:\\Users\\test")))
        managed = {"statusLine": {"command": "__HOME__/.local/bin/claude-statusline.exe statusline"}}
        result = _run(tmp_path, managed)
        assert result["statusLine"]["command"] == "C:/Users/test/.local/bin/claude-statusline.exe statusline"


class TestJsoncCommentPreservation:
    """JSONCコメント維持経路のテスト。

    既存パスの値置換のみで済む更新は`pytilpack.jsonc.edit`経由で書き戻され、
    利用者が加えた行コメント・空行・独自インデントを維持する。
    構造変化（キー追加・list変更）を含む更新は現行の`json.dumps`経路にフォールバックする。
    """

    def test_scalar_only_change_preserves_comments(self, tmp_path: Path):
        """既存キーのスカラー値置換のみならコメントが維持される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            '{\n  // ユーザーコメント\n  "language": "japanese"\n}\n',
            encoding="utf-8",
        )

        update_claude_settings(
            managed_path,
            target_path,
            removed_hook_substrings=(),
            removed_env_keys=(),
            removed_list_item_substrings=(),
        )

        text = target_path.read_text(encoding="utf-8")
        assert "// ユーザーコメント" in text
        assert '"language": "english"' in text

    def test_key_addition_falls_back_to_full_rewrite(self, tmp_path: Path):
        """新規キー追加を含む更新はコメント維持できず全書き換えへフォールバックする。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"language": "english", "newKey": "newValue"}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            '{\n  // ユーザーコメント\n  "language": "japanese"\n}\n',
            encoding="utf-8",
        )

        update_claude_settings(
            managed_path,
            target_path,
            removed_hook_substrings=(),
            removed_env_keys=(),
            removed_list_item_substrings=(),
        )

        text = target_path.read_text(encoding="utf-8")
        # フォールバック経路はJSONCコメントを保持しない
        assert "// ユーザーコメント" not in text
        # 変更後の値は反映される
        result = json.loads(text)
        assert result["language"] == "english"
        assert result["newKey"] == "newValue"


class TestProductionManagedSettings:
    """配布原本のプラットフォーム別記述規約とWindows向けhookの起動契約を検証する。"""

    @pytest.mark.parametrize("suffix", ["posix", "win32"])
    def test_personal_stop_hooks_match_platform_contract(self, suffix: str):
        """OS別設定の個人Stop hookが各プラットフォームの契約と一致する。"""
        path = _PROD_MANAGED_SETTINGS.with_suffix(f".{suffix}.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        stop_groups = data["hooks"].get("Stop", [])
        commands = [hook["command"] for group in stop_groups for hook in group["hooks"]]
        assert sum("claude_hook.py" in command and "autonomous_exit" in command for command in commands) == 0
        assert sum("claude_hook.py" in command and "stop_bell" in command for command in commands) == (
            1 if suffix == "posix" else 0
        )
        assert all("claude_hook.py stop;" not in command for command in commands)
        assert ("Stop" in data["hooks"]) is (suffix == "posix")

    def test_windows_hook_commands_use_home_placeholder(self):
        """Windows個人hookの実行パスがプレースホルダー形式である。"""
        path = _PROD_MANAGED_SETTINGS.with_suffix(".win32.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for groups in data["hooks"].values()
            for group in groups
            for hook in group["hooks"]
            if hook.get("type") == "command"
        ]
        python_hook_commands = [command for command in commands if "claude_hook.py" in command]
        assert len(python_hook_commands) == 1
        assert all("__HOME__\\dotfiles\\scripts\\claude_hook.py" in command for command in python_hook_commands)
        assert all("$env:USERPROFILE\\dotfiles\\scripts\\claude_hook.py" not in command for command in commands)

    def test_windows_pretooluse_script_preserves_exit_contract(self):
        """PowerShellスクリプトはフックの終了コード2だけを呼び出し元へ伝える。"""
        raw = _PROD_PRETOOLUSE_SCRIPT.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")
        text = raw.decode("utf-8-sig")
        assert "$hookScript = Join-Path $env:USERPROFILE 'dotfiles\\scripts\\claude_hook.py'" in text
        assert "Test-Path -LiteralPath $hookScript -PathType Leaf" in text
        assert "uv run --no-project --script $hookScript pretooluse" in text
        assert "if ($LASTEXITCODE -eq 2)" in text
        assert text.rstrip().endswith("exit 0")

    def test_windows_pretooluse_script_allows_missing_hook_via_pwsh(self, tmp_path: Path) -> None:
        """フック本体が不在の場合はPowerShell実行時も終了コード0で通過する。"""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwshを利用できないためPowerShell実行時テストを省略する")

        env = os.environ.copy()
        env["USERPROFILE"] = str(tmp_path / "missing-home")

        result = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(_PROD_PRETOOLUSE_SCRIPT)],
            check=False,
            env=env,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0

    @pytest.mark.parametrize("uv_exit_code", [0, 2])
    @pytest.mark.parametrize("invocation", ["direct", "bash"])
    def test_windows_pretooluse_script_runs_via_pwsh(
        self,
        tmp_path: Path,
        uv_exit_code: int,
        invocation: str,
    ) -> None:
        """PowerShell実行時も引数境界と終了コード契約を維持する。"""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwshを利用できないためPowerShell実行時テストを省略する")

        home = tmp_path / "Aki User"
        hook_script = home / "dotfiles" / "scripts" / "claude_hook.py"
        hook_script.parent.mkdir(parents=True)
        hook_script.touch()
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_path = tmp_path / f"uv-args-{invocation}-{uv_exit_code}.json"
        stub_path = tmp_path / "uv_stub.py"
        stub_path.write_text(
            """import json
import os
import sys
from pathlib import Path

Path(os.environ["UV_ARGS_PATH"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
sys.exit(int(os.environ["UV_EXIT_CODE"]))
""",
            encoding="utf-8",
        )
        uv_path = bin_dir / "uv"
        uv_path.write_text(
            f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(stub_path)!r}, run_name='__main__')\n",
            encoding="utf-8",
        )
        uv_path.chmod(0o755)
        (bin_dir / "uv.cmd").write_text(
            f'@"{sys.executable}" "{stub_path}" %*\r\n',
            encoding="utf-8",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": os.pathsep.join((str(bin_dir), env["PATH"])),
                "USERPROFILE": str(home),
                "UV_ARGS_PATH": str(args_path),
                "UV_EXIT_CODE": str(uv_exit_code),
            }
        )
        direct_command = [pwsh, "-NoProfile", "-File", str(_PROD_PRETOOLUSE_SCRIPT)]
        if invocation == "direct":
            command = direct_command
        else:
            bash = shutil.which("bash")
            if bash is None:
                pytest.skip("bashを利用できないためシェル経由のPowerShell実行時テストを省略する")
            command = [bash, "-c", shlex.join(direct_command)]

        result = subprocess.run(command, check=False, env=env, capture_output=True, text=True)

        assert result.returncode == uv_exit_code
        assert json.loads(args_path.read_text(encoding="utf-8")) == [
            "run",
            "--no-project",
            "--script",
            str(hook_script),
            "pretooluse",
        ]

    def test_windows_hook_generation_quotes_home_path_with_spaces(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Windows個人hookの生成結果は空白を含むホームパスを単一引数として扱う。"""
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path("C:\\Users\\Aki User")))
        target_path = tmp_path / "settings.json"
        update_claude_settings(
            _PROD_MANAGED_SETTINGS,
            target_path,
            overrides=[_PROD_MANAGED_SETTINGS.with_suffix(".win32.json")],
            removed_hook_substrings=(),
            removed_env_keys=(),
            removed_list_item_substrings=(),
        )

        data = json.loads(target_path.read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for groups in data["hooks"].values()
            for group in groups
            for hook in group["hooks"]
            if hook.get("type") == "command"
            and ("claude_hook.py" in hook.get("command", "") or "claude-hook-pretooluse.ps1" in hook.get("command", ""))
        ]
        assert len(commands) == 2
        assert any(
            '-File "C:/Users/Aki User\\dotfiles\\scripts\\claude-hook-pretooluse.ps1"' in command for command in commands
        )
        python_commands = [command for command in commands if "claude_hook.py" in command]
        assert all("'C:/Users/Aki User\\dotfiles\\scripts\\claude_hook.py'" in command for command in python_commands)


class TestUpdateClaudeConfig:
    """~/.claude.json 向けの単純上書きマージテスト。"""

    def test_new_file(self, tmp_path: Path):
        """claude.json が存在しない場合、managed 設定がそのまま出力される。"""
        result = _run(tmp_path, {"verbose": True})
        assert result == {"verbose": True}

    def test_merge_preserves_existing_keys(self, tmp_path: Path):
        """既存キーが保持され、managed キーで上書きされる。"""
        existing = {"numStartups": 100, "verbose": False, "theme": "light"}
        result = _run(tmp_path, {"verbose": True}, existing)
        assert result["numStartups"] == 100
        assert result["theme"] == "light"
        assert result["verbose"] is True


class TestPlatformOverride:
    """OS 別オーバーライド JSON が重ねマージされることを検証する。"""

    def test_override_adds_hooks(self, tmp_path: Path):
        """ベースに無い hooks セクションをオーバーライド経由で追加できる。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(
            json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "x"}]}]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["language"] == "japanese"
        assert result["hooks"]["PreToolUse"][0]["matcher"] == "Write"

    def test_override_adds_required_minimum_version(self, tmp_path: Path):
        """オーバーライド経由でトップレベルの`requiredMinimumVersion`がユーザー設定へ合成される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(
            json.dumps({"requiredMinimumVersion": "2.1.163"}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["requiredMinimumVersion"] == "2.1.163"
        assert result["language"] == "japanese"

    def test_override_replaces_scalar(self, tmp_path: Path):
        """オーバーライドはベースのスカラー値を上書きする。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "english"}, ensure_ascii=False), encoding="utf-8")
        override_path = tmp_path / "override.json"
        override_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        update_claude_settings(managed_path, target_path, overrides=[override_path])

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["language"] == "japanese"

    def test_missing_override_is_ignored(self, tmp_path: Path):
        """存在しないオーバーライドはスキップされる (他 OS を壊さない)。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"

        # overrides に空リストを渡してオーバーライドなしの動作を検証する
        update_claude_settings(managed_path, target_path, overrides=[])
        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}


def _setup_run_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_settings: dict,
    managed_config: dict | None = None,
) -> Path:
    """`run()` 経由テスト向けに 4 つのモジュール定数パスを差し替える。

    `_MANAGED_SETTINGS_PATH` には `managed_settings` を書き込む。
    `_MANAGED_CONFIG_PATH` には `managed_config` を書き込む。省略時は空 dict とする。
    `_SETTINGS_PATH`・`_CONFIG_PATH` は未作成のまま返す。
    """
    managed_settings_path = tmp_path / "managed_settings.json"
    managed_settings_path.write_text(json.dumps(managed_settings, ensure_ascii=False), encoding="utf-8")
    managed_config_path = tmp_path / "managed_config.json"
    managed_config_path.write_text(json.dumps(managed_config or {}), encoding="utf-8")
    settings_path = tmp_path / "settings.json"
    config_path = tmp_path / "claude.json"
    monkeypatch.setattr(mod, "_MANAGED_SETTINGS_PATH", managed_settings_path)
    monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(mod, "_MANAGED_CONFIG_PATH", managed_config_path)
    monkeypatch.setattr(mod, "_CONFIG_PATH", config_path)
    return settings_path


class TestCodexMcpTimeout:
    """`run()`経由で旧Codex MCP timeoutの一回限りの除去を検証する。"""

    def test_production_config_does_not_manage_codex_mcp(self) -> None:
        """配布設定は`mcpServers`自体を管理しない。"""
        managed = json.loads(_PROD_MANAGED_CONFIG.read_text(encoding="utf-8"))
        assert "mcpServers" not in managed

    def test_legacy_timeout_is_removed_from_generated_definition(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """旧installerが生成する形の定義から管理値のtimeoutだけを除去する。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing_codex = {
            "type": "stdio",
            "command": "codex",
            "args": ["mcp-server"],
            "env": {},
            "timeout": 7_200_000,
        }
        config_path.write_text(json.dumps({"mcpServers": {"codex": existing_codex}}), encoding="utf-8")

        mod.run()

        result = json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["codex"]
        assert result == {key: value for key, value in existing_codex.items() if key != "timeout"}

    def test_definition_with_extra_fields_is_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """利用者が加えたフィールドを持つ定義は変更しない。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing = {
            "mcpServers": {
                "codex": {
                    "type": "stdio",
                    "command": "codex",
                    "args": ["mcp-server"],
                    "env": {"CUSTOM": "value"},
                    "customField": True,
                    "timeout": 7_200_000,
                }
            }
        }
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8")) == existing

    def test_settings_json_does_not_strip_legacy_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """User scope設定ではないsettings.jsonの同名キーを変更しない。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {})
        existing_codex = {"command": "codex", "args": ["mcp-server"], "timeout": 7_200_000}
        settings_path.write_text(json.dumps({"mcpServers": {"codex": existing_codex}}), encoding="utf-8")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))["mcpServers"]["codex"]
        assert result == existing_codex

    @pytest.mark.parametrize(
        "existing_codex",
        [
            {"command": "other", "args": ["mcp-server"], "timeout": 7_200_000},
            {"type": "http", "command": "codex", "args": ["mcp-server"], "timeout": 7_200_000},
            {"command": "codex", "args": ["other"], "timeout": 7_200_000},
        ],
    )
    def test_non_legacy_definition_preserves_timeout(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        existing_codex: dict,
    ) -> None:
        """旧定義と一致しない同名serverのtimeoutを保持する。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps({"mcpServers": {"codex": existing_codex}}), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["codex"] == existing_codex

    def test_custom_timeout_is_preserved(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """従来管理値と異なるtimeoutは利用者設定として保持する。"""
        _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing = {"mcpServers": {"codex": {"command": "codex", "args": ["mcp-server"], "timeout": 1_000}}}
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8")) == existing

    @pytest.mark.parametrize(
        "existing",
        [
            {},
            {"mcpServers": {"codex": {"type": "stdio", "args": ["mcp-server"]}}},
            {"mcpServers": {"codex": {"type": "stdio", "command": ""}}},
            {"mcpServers": {"codex": {"type": "http", "command": "codex"}}},
        ],
    )
    def test_missing_or_incomplete_definition_does_not_create_timeout_only_entry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        existing: dict,
    ) -> None:
        """Codex定義が不在または不完全な場合はtimeoutを追加しない。"""
        _setup_run_paths(
            tmp_path,
            monkeypatch,
            {},
            {},
        )
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps(existing), encoding="utf-8")

        mod.run()

        assert json.loads(config_path.read_text(encoding="utf-8")) == existing


class TestPlatformOverrideSelection:
    """`run()` 経由でのプラットフォーム別オーバーライド適用を検証する統合テスト。

    `sys.platform` をパッチして `_platform_overrides()` の選択ロジックを通す。
    オーバーライドファイル選択・読み込み・マージ結果反映までを一連で検証する。
    """

    def test_linux_applies_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Linux (posix) 環境では `managed_settings.posix.json` が適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "english"})
        (tmp_path / "managed_settings.posix.json").write_text(json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed_settings.win32.json").write_text(json.dumps({"os": "win32"}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(mod.sys, "platform", "linux")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["os"] == "posix"
        assert result["language"] == "english"

    def test_darwin_applies_posix_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """macOS (darwin) 環境でも `managed_settings.posix.json` が適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "english"})
        (tmp_path / "managed_settings.posix.json").write_text(json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(mod.sys, "platform", "darwin")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["os"] == "posix"

    def test_win32_applies_win32_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Windows (win32) 環境では `managed_settings.win32.json` が適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "english"})
        (tmp_path / "managed_settings.posix.json").write_text(json.dumps({"os": "posix"}, ensure_ascii=False), encoding="utf-8")
        (tmp_path / "managed_settings.win32.json").write_text(json.dumps({"os": "win32"}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(mod.sys, "platform", "win32")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["os"] == "win32"

    def test_missing_override_applies_base_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """対応オーバーライドファイルが存在しない場合はベース設定のみが適用される。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {"language": "japanese"})
        monkeypatch.setattr(mod.sys, "platform", "linux")

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}


class TestMergeRecursive:
    """_merge の再帰マージロジックテスト。"""

    def test_nested_dict_merge(self, tmp_path: Path):
        """ネストした dict が再帰的にマージされる。"""
        existing = {"outer": {"keep": 1, "override": "old"}}
        result = _run(tmp_path, {"outer": {"override": "new", "add": 2}}, existing)
        assert result["outer"] == {"keep": 1, "override": "new", "add": 2}

    def test_list_union_dedup(self, tmp_path: Path):
        """list が union マージされ、重複排除・順序維持される。"""
        existing = {"items": ["a", "b", "c"]}
        result = _run(tmp_path, {"items": ["b", "d"]}, existing)
        assert result["items"] == ["a", "b", "c", "d"]

    def test_schema_key_is_ignored(self, tmp_path: Path):
        """`$schema` はマージ対象外で、ユーザー設定に伝播しない。"""
        existing = {"language": "english"}
        managed = {"$schema": "https://example.com/schema.json", "language": "japanese"}
        result = _run(tmp_path, managed, existing)
        assert "$schema" not in result
        assert result["language"] == "japanese"

    def test_schema_key_does_not_overwrite_existing(self, tmp_path: Path):
        """既存の `$schema` があっても managed 側の `$schema` で上書きされない。"""
        existing = {"$schema": "user-defined"}
        managed = {"$schema": "managed-defined", "language": "japanese"}
        result = _run(tmp_path, managed, existing)
        assert result["$schema"] == "user-defined"

    def test_dict_list_union_dedup(self, tmp_path: Path):
        """dict を要素に持つ list もマージ可能で、同一内容の重複は排除される。

        hooks 配列のように非 hashable な要素を含む list をマージする際の回帰テスト。
        """
        hook_entry = {
            "matcher": "Write|Edit|MultiEdit",
            "hooks": [{"type": "command", "command": "claude-hook-check-mojibake"}],
        }
        existing = {"hooks": {"PreToolUse": [hook_entry]}}
        managed = {
            "hooks": {
                "PreToolUse": [
                    dict(hook_entry),  # 同一内容 → 重複排除される
                    {"matcher": "Bash", "hooks": []},
                ]
            }
        }
        result = _run(tmp_path, managed, existing)
        pretooluse = result["hooks"]["PreToolUse"]
        assert len(pretooluse) == 2
        assert pretooluse[0] == hook_entry
        assert pretooluse[1]["matcher"] == "Bash"


class TestDiffLogging:
    """update_claude_settings の差分ログ出力を公開経路経由で検証する。

    値の要約・リスト差分・再帰差分は内部実装のため、設定更新時に logger へ
    出力される差分行を通じてまとめて検証する。
    """

    @staticmethod
    def _update_and_capture(
        tmp_path: Path,
        managed: dict,
        existing: dict,
        caplog: pytest.LogCaptureFixture,
        *,
        removed_env_keys: tuple[str, ...] = (),
    ) -> list[str]:
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps(managed, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        with caplog.at_level(logging.INFO, logger="pytools._internal.update_claude_settings"):
            update_claude_settings(managed_path, target_path, removed_env_keys=removed_env_keys)
        return caplog.messages

    def test_scalar_changes_are_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """文字列・数値・真偽値のスカラー変更が old → new 形式で6スペース付き出力される。"""
        existing = {"s": "english", "n": 1, "b": False}
        managed = {"s": "japanese", "n": 42, "b": True}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        text = "\n".join(messages)
        assert 's: "english" → "japanese"' in text
        assert "n: 1 → 42" in text
        assert "b: false → true" in text
        assert any(line.startswith("      s:") for line in messages)

    def test_new_key_value_summaries(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """新規キーの値が dict/list サマリーと長文切り詰めで出力される。"""
        existing: dict = {}
        managed = {"d": {"a": 1, "b": 2}, "l": [1, 2, 3], "long": "x" * 100}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        text = "\n".join(messages)
        assert "d: (新規) {...} (2 keys)" in text
        assert "l: (新規) [...] (3 件)" in text
        long_line = next(line for line in messages if line.startswith("      ") and "long: (新規)" in line)
        assert long_line.endswith("...")  # 60文字上限で切り詰められる

    def test_nested_dict_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """ネストした dict の変更が親.子のパス付きで出力される。"""
        existing = {"permissions": {"allow": ["Bash"], "defaultMode": "plan"}}
        managed = {"permissions": {"defaultMode": "auto"}}
        text = "\n".join(self._update_and_capture(tmp_path, managed, existing, caplog))
        assert 'permissions.defaultMode: "plan" → "auto"' in text

    def test_string_list_union_shows_inline_diff(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """文字列リストの union 追加が件数と追加要素付きで出力される。"""
        existing = {"items": ["a", "b"]}
        managed = {"items": ["a", "b", "c"]}
        text = "\n".join(self._update_and_capture(tmp_path, managed, existing, caplog))
        assert "items: 2 → 3 件" in text
        assert '+"c"' in text

    def test_many_list_additions_show_count_only(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """差分件数が上限を超えるリストは件数のみ出力される（追加要素のインライン表示なし）。"""
        existing = {"items": ["a"]}
        managed = {"items": ["a", "b", "c", "d", "e"]}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        items_line = next(line for line in messages if line.startswith("      ") and "items:" in line)
        assert "1 → 5 件" in items_line
        assert "+" not in items_line

    def test_dict_element_list_shows_count_only(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """dict を要素に持つリスト（hooks 等）の差分は件数のみ出力される（インライン表示なし）。

        inner hooks には除去対象外の command を持たせる（空だと _strip_removed_hooks が
        matcher ごと除去してしまうため）。
        """
        existing = {"hooks": {"PreToolUse": [{"matcher": "A", "hooks": [{"type": "command", "command": "a.py"}]}]}}
        managed = {"hooks": {"PreToolUse": [{"matcher": "B", "hooks": [{"type": "command", "command": "b.py"}]}]}}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        hooks_line = next(line for line in messages if line.startswith("      ") and "PreToolUse:" in line)
        assert "1 → 2 件" in hooks_line
        assert "+" not in hooks_line

    def test_removed_env_key_shows_deletion(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """env キー除去で env が削除され、削除差分が出力される。"""
        existing = {"env": {"OLD": "1"}}
        managed: dict = {}
        text = "\n".join(self._update_and_capture(tmp_path, managed, existing, caplog, removed_env_keys=("OLD",)))
        assert "env:" in text
        assert "→ (削除)" in text

    def test_no_change_logs_no_diff_rows(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """差分が無い場合は変更なしのみで差分行（6スペース始まり）が出力されない。"""
        existing = {"language": "japanese"}
        managed = {"language": "japanese"}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        assert any("変更なし" in m for m in messages)
        assert not [m for m in messages if m.startswith("      ")]

    def test_diff_rows_sorted_by_key(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """差分行はキーのアルファベット順で出力される。"""
        existing: dict = {}
        managed = {"z": 1, "a": 2, "m": 3}
        messages = self._update_and_capture(tmp_path, managed, existing, caplog)
        diff_rows = [m for m in messages if m.startswith("      ")]
        keys = [m.split(":")[0].strip() for m in diff_rows]
        assert keys == ["a", "m", "z"]


class TestStripRemovedHooks:
    """配布元から削除された hook エントリの自動除去テスト。"""

    def test_removed_hook_is_dropped_from_existing(self, tmp_path: Path):
        """既存の hooks に旧 command 部分文字列が残っている場合、マージ前に除去される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Write", "hooks": [{"type": "command", "command": "new-cmd"}]}]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PostToolUse": [
                            {
                                "matcher": "Write|Edit|MultiEdit",
                                "hooks": [
                                    {"type": "command", "command": "sh -c 'uv run --script ~/legacy/old_hook.py'"},
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_hook_substrings=("old_hook.py",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        # 旧エントリは PostToolUse ごと消える (空になったため)
        assert "PostToolUse" not in result["hooks"]
        # 新エントリはマージされて残る
        assert result["hooks"]["PreToolUse"][0]["matcher"] == "Write"

    def test_legacy_windows_common_entry_is_dropped_from_existing(self, tmp_path: Path):
        """旧Windows共通入口が既存設定に残っていても既定の更新で除去する。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        legacy = "uv run --no-project --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook.py"
        target_path.write_text(
            json.dumps(
                {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": legacy}]}]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert "hooks" not in result or "PreToolUse" not in result["hooks"]

    def test_substituted_windows_autonomous_entry_is_dropped_from_existing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """実ホーム絶対パスへ置換された旧Windows自律終了入口を既定の更新で除去する。"""
        home = Path("C:/Users/Aki User")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))

        legacy_managed_path = tmp_path / "legacy-managed.json"
        legacy_managed_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "Stop": [
                            {
                                "hooks": [
                                    {"type": "command", "command": _LEGACY_WINDOWS_AUTONOMOUS_EXIT_COMMAND},
                                    {"type": "command", "command": "keep-stop-hook"},
                                ]
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "settings.json"
        update_claude_settings(
            legacy_managed_path,
            target_path,
            overrides=[],
            removed_hook_substrings=(),
            removed_env_keys=(),
            removed_list_item_substrings=(),
        )
        generated = json.loads(target_path.read_text(encoding="utf-8"))
        generated_command = generated["hooks"]["Stop"][0]["hooks"][0]["command"]
        assert "C:/Users/Aki User\\dotfiles\\scripts\\claude_hook.py" in generated_command

        update_claude_settings(
            _PROD_MANAGED_SETTINGS,
            target_path,
            overrides=[_PROD_MANAGED_SETTINGS.with_suffix(".win32.json")],
        )

        result = json.loads(target_path.read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for group in result.get("hooks", {}).get("Stop", [])
            for hook in group["hooks"]
            if hook.get("type") == "command"
        ]
        assert commands == ["keep-stop-hook"]

    def test_distributed_legacy_windows_pretooluse_is_replaced(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """配布済みの旧Windows入口を現行の`-File`入口1件へ移行する。"""
        home = Path("C:/Users/Aki User")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        target_path = tmp_path / "settings.json"
        legacy_command = _LEGACY_WINDOWS_PRETOOLUSE_COMMAND.replace("__HOME__", str(home))
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write|Edit|MultiEdit",
                                "hooks": [{"type": "command", "command": legacy_command}],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(
            _PROD_MANAGED_SETTINGS,
            target_path,
            overrides=[_PROD_MANAGED_SETTINGS.with_suffix(".win32.json")],
        )

        result = json.loads(target_path.read_text(encoding="utf-8"))
        commands = [
            hook["command"]
            for group in result["hooks"]["PreToolUse"]
            for hook in group["hooks"]
            if hook.get("type") == "command"
        ]
        assert commands == [
            "powershell -NoProfile -ExecutionPolicy Bypass -File "
            '"C:/Users/Aki User\\dotfiles\\scripts\\claude-hook-pretooluse.ps1"'
        ]

    def test_removed_hook_keeps_sibling_in_same_matcher(self, tmp_path: Path):
        """同じ matcher 内の他の hook は残す。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {"type": "command", "command": "old_hook.py"},
                                    {"type": "command", "command": "keep_me.py"},
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_hook_substrings=("old_hook.py",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        inner = result["hooks"]["PreToolUse"][0]["hooks"]
        assert len(inner) == 1
        assert inner[0]["command"] == "keep_me.py"

    @pytest.mark.parametrize(
        ("command", "should_be_removed"),
        [
            # 旧形式 (`uv run --script`): 除去対象
            (
                "sh -c 'uv run --script ~/dotfiles/scripts/claude_hook_pretooluse.py; exit 0'",
                True,
            ),
            (
                "sh -c 'uv run --script ~/dotfiles/scripts/claude_hook_stop.py; exit 0'",
                True,
            ),
            (
                'pwsh -c "uv run --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook_pretooluse.py"',
                True,
            ),
            (
                'pwsh -c "uv run --script $env:USERPROFILE\\dotfiles\\scripts\\claude_hook_stop.py"',
                True,
            ),
            # 廃止済みの直接起動形式 (`uv run --no-project --script <旧スクリプト>`): 除去対象
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook_pretooluse.py; exit 0'",
                True,
            ),
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook_posttooluse.py; exit 0'",
                True,
            ),
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook_stop.py; exit 0'",
                True,
            ),
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook_autonomous_exit.py; exit 0'",
                True,
            ),
            # 共通エントリポイント形式でも、スクリプト実在検査を持たない旧pretooluseコマンドは除去する
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook.py pretooluse; exit 0'",
                True,
            ),
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook.py stop; exit 0'",
                True,
            ),
            (
                'powershell -Command "& { uv run --no-project --script '
                '$env:USERPROFILE\\dotfiles\\scripts\\claude_hook.py stop; exit 0 }"',
                True,
            ),
            (
                "powershell -NoProfile -ExecutionPolicy Bypass -Command " + "'& {" + " uv run --no-project --script "
                '"C:/Users/Aki User\\dotfiles\\scripts\\claude_hook.py" pretooluse; '
                'if ($LASTEXITCODE -eq 2) { exit 2 } else { exit 0 } }"',
                True,
            ),
            (
                "powershell -NoProfile -ExecutionPolicy Bypass -File "
                '"C:/Users/Aki User\\dotfiles\\scripts\\claude-hook-pretooluse.ps1"',
                False,
            ),
            (
                "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook.py autonomous_exit; exit 0'",
                True,
            ),
            (
                'powershell -Command "& { uv run --no-project --script '
                '$env:USERPROFILE\\dotfiles\\scripts\\claude_hook.py autonomous_exit; exit 0 }"',
                True,
            ),
        ],
    )
    def test_no_project_substrings_default(self, tmp_path: Path, command: str, should_be_removed: bool):
        """既定の除去パターンが廃止形式を除去し、現行の共通エントリポイントを保持する。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [{"type": "command", "command": command}],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        if should_be_removed:
            assert "PreToolUse" not in result.get("hooks", {})
        else:
            assert result["hooks"]["PreToolUse"][0]["hooks"][0]["command"] == command


class TestStripRemovedEnvKeys:
    """配布元から削除された env キーの自動除去テスト。"""

    @pytest.mark.parametrize(
        ("existing_env", "removed_keys", "expected_env"),
        [
            # 削除対象キーあり → 除去される
            (
                {"OLD_KEY": "1", "KEEP_KEY": "keep"},
                ("OLD_KEY",),
                {"KEEP_KEY": "keep"},
            ),
            # 削除対象キーなし（別の env キーのみ） → 無変更
            (
                {"OTHER_KEY": "value"},
                ("OLD_KEY",),
                {"OTHER_KEY": "value"},
            ),
            # 削除後 env が空 dict → env キーごと除去される
            (
                {"OLD_KEY": "1"},
                ("OLD_KEY",),
                None,  # env キーごと消える
            ),
        ],
    )
    def test_env_key_removal(
        self,
        tmp_path: Path,
        existing_env: dict,
        removed_keys: tuple[str, ...],
        expected_env: dict | None,
    ):
        """env キーの除去・無変更・空後のキー消去を検証する。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"env": existing_env}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=removed_keys)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        if expected_env is None:
            assert "env" not in result
        else:
            assert result["env"] == expected_env

    def test_no_env_key_is_noop(self, tmp_path: Path):
        """env キー自体なし → 無変更。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"language": "japanese"}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=("OLD_KEY",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert "env" not in result
        assert result["language"] == "japanese"

    def test_non_dict_env_is_untouched(self, tmp_path: Path):
        """env が dict でない場合、削除対象キーを渡しても env は無変更のまま保持される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"env": "invalid"}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=("OLD_KEY",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["env"] == "invalid"

    def test_empty_removed_env_keys_is_noop(self, tmp_path: Path):
        """removed_env_keys が空タプルの場合、既存の env は無変更のまま保持される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"env": {"FOO": "bar", "BAZ": "qux"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=())

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["env"] == {"FOO": "bar", "BAZ": "qux"}

    def test_specified_env_key_removed_and_managed_env_preserved(self, tmp_path: Path):
        """removed_env_keys で指定したキーが除去され、managed の env は保持される。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"env": {"CLAUDE_CODE_NO_FLICKER": "1"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"env": {"DEPRECATED_ENV_KEY": "1", "CLAUDE_CODE_NO_FLICKER": "1"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_env_keys=("DEPRECATED_ENV_KEY",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert "DEPRECATED_ENV_KEY" not in result["env"]
        assert result["env"]["CLAUDE_CODE_NO_FLICKER"] == "1"

    def test_default_removed_env_key_is_removed(self, tmp_path: Path):
        """既定の廃止キー一覧に含まれる環境変数を既存設定から除去する。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"env": {"CLAUDE_CODE_NO_FLICKER": "1"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"env": {"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1", "KEEP_KEY": "keep"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS" not in result["env"]
        assert result["env"]["KEEP_KEY"] == "keep"
        assert result["env"]["CLAUDE_CODE_NO_FLICKER"] == "1"


class TestStripRemovedKeys:
    """配布元から廃止した設定キーの自動除去テスト。"""

    @pytest.mark.parametrize(
        ("existing", "expected"),
        [
            (
                {"autoMode": {"allowMode": "extend", "allow": ["利用者独自ルール"]}},
                {"autoMode": {"allow": ["利用者独自ルール"]}},
            ),
            (
                {"autoMode": {"allow": ["利用者独自ルール"]}},
                {"autoMode": {"allow": ["利用者独自ルール"]}},
            ),
        ],
    )
    def test_removed_key_is_deleted_or_absent_without_other_changes(
        self,
        tmp_path: Path,
        existing: dict,
        expected: dict,
    ) -> None:
        """登録キーを除去し、キーが存在しない場合は他の設定を変更しない。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, removed_keys=("autoMode.allowMode",))

        assert json.loads(target_path.read_text(encoding="utf-8")) == expected

    def test_run_safely_removes_key_from_settings_and_config(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`はsettings.jsonと.claude.jsonの廃止キーを安全に除去する。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {})
        config_path = tmp_path / "claude.json"
        existing = {"autoMode": {"allowMode": "extend", "allow": ["利用者独自ルール"]}}
        settings_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")
        config_path.write_text(json.dumps(existing, ensure_ascii=False), encoding="utf-8")

        mod.run()

        expected = {"autoMode": {"allow": ["利用者独自ルール"]}}
        assert json.loads(settings_path.read_text(encoding="utf-8")) == expected
        assert json.loads(config_path.read_text(encoding="utf-8")) == expected


class TestStripRemovedListItems:
    """配布元から削除された配列項目の自動削除テスト。"""

    def test_strip_removed_list_items_removes_matching(self, tmp_path: Path):
        """登録した部分文字列を含む配列要素が削除される。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["OLD_RULE_MARKER で始まる旧ルール文面", "新ルール文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["新ルール文面"]

    def test_strip_removed_list_items_preserves_others(self, tmp_path: Path):
        """部分文字列を含まない要素（利用者の独自追加項目）は保持される。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["利用者独自ルール1", "利用者独自ルール2"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["利用者独自ルール1", "利用者独自ルール2"]

    def test_strip_removed_list_items_missing_path(self, tmp_path: Path):
        """対象パスが存在しない場合は例外を送出せず処理が継続する。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}

    def test_strip_removed_list_items_non_list_target(self, tmp_path: Path):
        """パス先が list でない場合は何もしない。"""
        mappings = (("autoMode.allow", "OLD_RULE_MARKER"),)
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"autoMode": {"allow": "not a list"}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, removed_list_item_substrings=mappings)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == "not a list"

    def test_run_applies_list_items_to_settings_only(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """`run()`経由で settings.json 側にのみ削除マッピングが適用される。

        .claude.json 側の同名構造（仮にあった場合）は保持されることを検証する回帰テスト。
        """
        managed_settings_path = tmp_path / "managed_settings.json"
        managed_settings_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        managed_config_path = tmp_path / "managed_config.json"
        managed_config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        old_rule_marker = next(
            marker
            for path, marker in mod._REMOVED_LIST_ITEM_SUBSTRINGS  # pylint: disable=protected-access
            if path == "autoMode.allow"
        )
        settings_path.write_text(
            json.dumps(
                {"autoMode": {"allow": [f"{old_rule_marker} 旧ルール文面", "新ルール文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path = tmp_path / "claude.json"
        config_path.write_text(
            json.dumps(
                {"autoMode": {"allow": [f"{old_rule_marker} を含む config 側項目"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(mod, "_MANAGED_SETTINGS_PATH", managed_settings_path)
        monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(mod, "_MANAGED_CONFIG_PATH", managed_config_path)
        monkeypatch.setattr(mod, "_CONFIG_PATH", config_path)

        mod.run()

        # settings 側は旧ルール文面が削除される
        settings_result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert settings_result["autoMode"]["allow"] == ["新ルール文面"]
        # config 側は削除されない（仮に同名構造があっても保持される）
        config_result = json.loads(config_path.read_text(encoding="utf-8"))
        assert config_result["autoMode"]["allow"] == [f"{old_rule_marker} を含む config 側項目"]

    def test_run_removes_legacy_env_deny_and_preserves_user_deny(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は旧管理denyだけを除去し、利用者独自denyと現行管理denyを保持する。"""
        settings_path = _setup_run_paths(
            tmp_path,
            monkeypatch,
            {"permissions": {"deny": MANAGED_DENY}},
        )
        settings_path.write_text(
            json.dumps(
                {"permissions": {"deny": ["Read(./.env)", "Read(./.secret)", "Read(*.key)"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["permissions"]["deny"] == [
            "Read(./.secret)",
            "Read(*.key)",
            "Read(*.crt)",
            "Read(//**/.credentials.json)",
        ]

    def test_run_removes_legacy_personal_repo_push_rule(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は移管前の個人リポジトリpushルールだけを除去する。"""
        settings_path = _setup_run_paths(tmp_path, monkeypatch, {})
        settings_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ak110の個人リポジトリ（dotfiles, pytilpack等）ではデフォルトブランチへの直接pushを許可。",
                            "利用者独自ルール",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["利用者独自ルール"]

    def test_run_removes_legacy_codex_mcp_permission(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run()`は旧Codex MCPの許可だけを除去し、現行MCPと利用者独自許可を保持する。"""
        settings_path = _setup_run_paths(
            tmp_path,
            monkeypatch,
            {"permissions": {"allow": ["mcp__plugin_agent-toolkit_codex_app_server__codex_start"]}},
        )
        settings_path.write_text(
            json.dumps(
                {
                    "permissions": {
                        "allow": [
                            "mcp__codex__codex",
                            "mcp__codex__codex_reply",
                            "Bash(ls:*)",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["permissions"]["allow"] == [
            "Bash(ls:*)",
            "mcp__plugin_agent-toolkit_codex_app_server__codex_start",
        ]


class TestStripStaleLabeledListItems:
    """ラベル単位の旧文面自動削除テスト（配布原本のラベル改訂時の重複蓄積を防ぐ）。"""

    def test_stale_label_variant_is_removed_on_managed_text_revision(self, tmp_path: Path):
        """配布原本側でラベル本文を改訂すると、配布先の旧文面がラベル一致で除去される（再発防止テスト）。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["Feedback-Originated Gate Revision: atk mq process-loop を使う新文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ak110の個人リポジトリでの利用者独自エントリ",
                            "Feedback-Originated Gate Revision: atk fb process-loop を使う旧文面",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == [
            "ak110の個人リポジトリでの利用者独自エントリ",
            "Feedback-Originated Gate Revision: atk mq process-loop を使う新文面",
        ]

    def test_unlabeled_user_entry_is_preserved(self, tmp_path: Path):
        """ラベルを持たない利用者独自エントリは除去対象にならない（再発防止テスト）。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"autoMode": {"allow": ["Some Label: 現行文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"autoMode": {"allow": ["ラベルを持たない利用者独自ルール"]}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == [
            "ラベルを持たない利用者独自ルール",
            "Some Label: 現行文面",
        ]

    def test_empty_paths_is_noop(self, tmp_path: Path):
        """`stale_labeled_list_paths`が空タプルの場合は何もしない（既定値の安全性、再発防止テスト）。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"autoMode": {"allow": ["Label: 現行文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"autoMode": {"allow": ["Label: 旧文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(managed_path, target_path)

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == ["Label: 旧文面", "Label: 現行文面"]

    def test_missing_managed_path_is_noop(self, tmp_path: Path):
        """配布原本（managed）側に対象パスが存在しない場合は例外を送出せず処理が継続する（再発防止テスト）。

        `_strip_stale_labeled_list_items`はmanaged側からラベル集合を抽出できず`labels`が
        空集合になるため、早期returnでtarget側の経路解決には到達しない分岐を検証する。
        """
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese"}

    def test_missing_target_path_is_noop(self, tmp_path: Path):
        """配布先（target）側に対象パスが存在しない場合は例外を送出せず通常マージが進む（再発防止テスト）。

        managed側にはラベル付き項目が存在するため`labels`は非空になり、target側の
        経路解決（`_resolve_dict_container`）がdictでない/存在しないキーで`None`を返す
        分岐を検証する。初回導入など`settings.json`に`autoMode`キー自体が無いケースに相当する。
        """
        managed_path = tmp_path / "managed.json"
        managed_path.write_text(
            json.dumps({"autoMode": {"allow": ["Label: 現行文面"]}}, ensure_ascii=False),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(json.dumps({"language": "japanese"}, ensure_ascii=False), encoding="utf-8")

        update_claude_settings(managed_path, target_path, stale_labeled_list_paths=("autoMode.allow",))

        result = json.loads(target_path.read_text(encoding="utf-8"))
        assert result == {"language": "japanese", "autoMode": {"allow": ["Label: 現行文面"]}}

    def test_run_reproduces_and_fixes_prod_duplicate(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """`run()`経由で実際の重複事象（旧文面atk fbと新文面atk mqの共存）が解消される（再現テスト）。"""
        managed_settings_path = tmp_path / "managed_settings.json"
        managed_settings_path.write_text(
            json.dumps(
                {"autoMode": {"allow": ["Feedback-Originated Gate Revision: atk mq process-loop を使う新文面"]}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        managed_config_path = tmp_path / "managed_config.json"
        managed_config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ak110の個人リポジトリでの利用者独自エントリ",
                            "Feedback-Originated Gate Revision: atk fb process-loop を使う旧文面",
                            "Feedback-Originated Gate Revision: atk mq process-loop を使う新文面",
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        config_path = tmp_path / "claude.json"
        config_path.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

        monkeypatch.setattr(mod, "_MANAGED_SETTINGS_PATH", managed_settings_path)
        monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
        monkeypatch.setattr(mod, "_MANAGED_CONFIG_PATH", managed_config_path)
        monkeypatch.setattr(mod, "_CONFIG_PATH", config_path)

        mod.run()

        result = json.loads(settings_path.read_text(encoding="utf-8"))
        assert result["autoMode"]["allow"] == [
            "ak110の個人リポジトリでの利用者独自エントリ",
            "Feedback-Originated Gate Revision: atk mq process-loop を使う新文面",
        ]


class TestManagedAutoModeSchema:
    """配布原本の`autoMode`サブキーを公式スキーマの許容集合と照合する。"""

    def test_subkeys_match_official_schema_snapshot(self) -> None:
        """配布原本3ファイルの`autoMode`サブキーは公式スキーマの許容集合に含まれる。"""
        # https://json.schemastore.org/claude-code-settings.json の autoMode.properties、2026-08-16時点
        allowed = {"allow", "soft_deny", "environment", "hard_deny", "classifyAllShell"}
        for path in _PROD_MANAGED_SETTINGS_FILES:
            data = json.loads(path.read_text(encoding="utf-8"))
            if "autoMode" in data:
                assert set(data["autoMode"]) <= allowed, path


class TestManagedAutoModeAllowLabelFormat:
    """配布原本`autoMode.allow`の自作要素がラベル形式であることを検査する自動チェック。

    `_strip_stale_labeled_list_items`によるラベル単位の旧文面除去は、配布原本側の要素が
    ラベル形式（`^([A-Za-z][A-Za-z0-9 -]*): `）に一致することを前提とする。
    公式仕様の`"$defaults"`以外にラベルを持たない要素を追加すると保護対象にならないため、機械的に検出する。
    """

    def test_all_entries_match_labeled_format(self) -> None:
        """配布原本`autoMode.allow`は`"$defaults"`以外の全要素がラベル形式。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        allow = managed["autoMode"]["allow"]
        unlabeled = [
            item
            for item in allow
            if item != "$defaults" and not mod._LABELED_LIST_ITEM_PATTERN.match(item)  # pylint: disable=protected-access
        ]
        assert unlabeled == []

    def test_labels_are_unique(self) -> None:
        """配布原本`autoMode.allow`のラベルは重複しない。

        `_strip_stale_labeled_list_items`はラベル単位で配布先の旧文面を除去するため、
        配布原本側に同一ラベルの項目が複数あると正規化の基準が曖昧になる。
        コピー&ペーストの誤りによる重複を機械的に検出する。
        """
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        allow = managed["autoMode"]["allow"]
        labels = [
            match.group(1)
            for item in allow
            if (match := mod._LABELED_LIST_ITEM_PATTERN.match(item))  # pylint: disable=protected-access
        ]
        assert len(labels) == len(set(labels))
