"""update_claude_settingsの基本動作の試験。"""

# pylint: disable=protected-access

import json
import shutil
import sys
from pathlib import Path

import pytest

from pytools._internal import common
from pytools._internal import update_claude_settings as mod
from pytools._internal.update_claude_settings import update_claude_settings
from pytools._internal.update_claude_settings_test_helpers import (
    _PROD_MANAGED_CONFIG,
    _PROD_MANAGED_SETTINGS,
    MANAGED,
    MANAGED_ALLOW,
    MANAGED_DENY,
    _run,
    _setup_run_paths,
)


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

    def test_managed_dialog_expiry_overwrites_existing(self, tmp_path: Path):
        """配布原本の dialogExpiry は既存設定の値を上書きする。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"dialogExpiry": "5m"})
        assert result["dialogExpiry"] == "never"

    @pytest.mark.parametrize("platform", ["linux", "win32"])
    @pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
    def test_run_disables_host_instructions_and_preserves_unmanaged_settings(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        existing: bool,
    ) -> None:
        """通常の更新で補助生成を止めて手動スキルを残し、既存値の保持と再適用を両OSで確認する。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        managed_config = json.loads(_PROD_MANAGED_CONFIG.read_text(encoding="utf-8"))
        settings_path = _setup_run_paths(tmp_path, monkeypatch, managed, managed_config)
        for suffix in ("posix", "win32"):
            shutil.copyfile(
                _PROD_MANAGED_SETTINGS.with_suffix(f".{suffix}.json"),
                tmp_path / "dotfiles-root" / "share" / f"claude_settings_json_managed.{suffix}.json",
            )
        monkeypatch.setattr(mod.sys, "platform", platform)
        unmanaged = {"model": "custom-model", "mcpServers": {"custom": {"command": "custom-mcp"}}}
        manual_skills = (
            "batch",
            "claude-api",
            "claude-in-chrome",
            "code-review",
            "dataviz",
            "debug",
            "doctor",
            "fewer-permission-prompts",
            "init",
            "keybindings-help",
            "loop",
            "run",
            "run-skill-generator",
            "schedule",
            "security-review",
            "simplify",
            "update-config",
            "verify",
        )
        config_path = tmp_path / "claude.json"
        if existing:
            settings_path.write_text(
                json.dumps(
                    {
                        "outputStyle": "Explanatory",
                        "includeGitInstructions": True,
                        "enableArtifact": True,
                        "disableClaudeAiConnectors": False,
                        "enableWorkflows": True,
                        "awaySummaryEnabled": True,
                        "env": {"CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION": "1", "CUSTOM_ENV": "preserved"},
                        "skillOverrides": {"batch": "disabled", "custom-skill": "user-invocable-only"},
                        **unmanaged,
                    }
                ),
                encoding="utf-8",
            )
            config_path.write_text(json.dumps({"customAuthentication": "preserved"}), encoding="utf-8")

        first = mod.run()
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
        config = json.loads(config_path.read_text(encoding="utf-8"))
        assert first.failure is None
        assert settings["outputStyle"] == "default"
        assert settings["includeGitInstructions"] is False
        assert settings["enableArtifact"] is False
        assert settings["disableClaudeAiConnectors"] is True
        assert settings["enableWorkflows"] is False
        assert settings["awaySummaryEnabled"] is False
        assert settings["env"]["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] == "0"
        assert {name: settings["skillOverrides"][name] for name in manual_skills} == dict.fromkeys(
            manual_skills, "user-invocable-only"
        )
        assert "disableArtifact" not in settings
        assert "disableBundledSkills" not in settings
        if existing:
            assert all(settings[key] == value for key, value in unmanaged.items())
            assert settings["env"]["CUSTOM_ENV"] == "preserved"
            assert settings["skillOverrides"]["custom-skill"] == "user-invocable-only"
            assert config["customAuthentication"] == "preserved"

        second = mod.run()
        assert second.failure is None
        assert not second.changed
        assert json.loads(settings_path.read_text(encoding="utf-8")) == settings
        assert json.loads(config_path.read_text(encoding="utf-8")) == config

    def test_managed_commit_attribution_uses_host_identity_without_enabling_other_attribution(self, tmp_path: Path):
        """commit帰属だけを配布し、session URLとPRの既定動作は変更しない。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"attribution": {"sessionUrl": True}})

        assert result["attribution"]["commit"] == ("Co-Authored-By: {model} / {effort} <noreply@anthropic.com>")
        assert result["attribution"]["sessionUrl"] is True
        assert "sessionUrl" not in managed["attribution"]
        assert "pr" not in managed["attribution"]

    def test_managed_env_keeps_bash_working_dir_at_project(self, tmp_path: Path):
        """配布原本はBash呼び出し間の作業ディレクトリ持ち越しを止める環境変数を既存のenvへ加える。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"env": {"FOO": "bar"}})
        assert result["env"]["CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR"] == "1"
        assert result["env"]["FOO"] == "bar"

    def test_managed_env_enables_function_hooks(self, tmp_path: Path):
        """配布原本のFunction hooks有効化が既存設定へ反映される。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"env": {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "0", "FOO": "bar"}})
        assert result["env"]["CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"] == "1"
        assert result["env"]["FOO"] == "bar"

    def test_managed_env_directs_playwright_mcp_output_under_home(self, tmp_path: Path):
        """配布原本はPlaywright MCPの自動命名の出力先を作業ツリーの外（ホーム配下のキャッシュ）へ向ける。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"env": {"FOO": "bar"}})
        assert result["env"]["PLAYWRIGHT_MCP_OUTPUT_DIR"] == f"{Path.home().as_posix()}/.cache/playwright-mcp"
        assert result["env"]["FOO"] == "bar"

    def test_managed_env_extends_mcp_connect_timeout(self, tmp_path: Path):
        """配布原本はMCPサーバーの接続上限を、再起動直後の遅い起動も待てる120秒へ延ばす。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        result = _run(tmp_path, managed, {"env": {"FOO": "bar"}})
        assert result["env"]["MCP_TIMEOUT"] == "120000"
        assert result["env"]["FOO"] == "bar"

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

    def test_managed_project_files_environment_merges_with_existing_rules(self, tmp_path: Path) -> None:
        """作業ツリーの環境宣言が、Claude Code組み込みの規則を示す`$defaults`とユーザーの独自設定を保持して届く。"""
        managed = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        declaration = next(item for item in managed["autoMode"]["environment"] if item.startswith("Project files: "))
        existing = {
            "autoMode": {
                "environment": ["$defaults", "ユーザー固有の信頼境界"],
                "allow": ["ユーザー固有の許可"],
            },
        }
        result = _run(tmp_path, managed, existing)["autoMode"]
        assert declaration in result["environment"]
        assert "ユーザー固有の信頼境界" in result["environment"]
        assert result["environment"].count("$defaults") == 1
        assert set(managed["autoMode"]["environment"]) <= set(result["environment"])
        assert "ユーザー固有の許可" in result["allow"]
        assert "$defaults" in result["allow"]


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
    ユーザーが加えた行コメント・空行・独自インデントを維持する。
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


def test_run_fails_without_dotfiles_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """作業ツリーを解決できない場合は配置先へ書き込まず、工程を失敗と数える。"""
    settings_path = tmp_path / "settings.json"
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: None)
    monkeypatch.setattr(mod, "_SETTINGS_PATH", settings_path)
    monkeypatch.setattr(mod, "_CONFIG_PATH", tmp_path / "claude.json")

    outcome = mod.run()

    assert outcome.failure == "dotfilesの作業ツリーを解決できない"
    assert not settings_path.exists()
