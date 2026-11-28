"""update_claude_settingsのremoved_hooksの試験。"""

# pylint: disable=protected-access

import json
import sys
from pathlib import Path

import pytest

from pytools._internal import update_claude_settings as mod
from pytools._internal.update_claude_settings import update_claude_settings
from pytools._internal.update_claude_settings_test_helpers import (
    _LEGACY_WINDOWS_AUTONOMOUS_EXIT_COMMAND,
    _LEGACY_WINDOWS_PRETOOLUSE_COMMAND,
    _PROD_MANAGED_SETTINGS,
)


class TestStripRemovedHooks:
    @pytest.mark.parametrize("platform", ["linux", "darwin"])
    @pytest.mark.parametrize("user_setting", [{}, {"AGENT_TOOLKIT_CONFIG_CLAUDE_STOP_BELL": "false"}])
    def test_bell_migration_preserves_other_registrations_and_matches_preview(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, user_setting: dict
    ) -> None:
        monkeypatch.setattr(sys, "platform", platform)
        bell = {"type": "command", "command": r"""printf %s '{"terminalSequence":"\u0007"}'"""}
        notify_matcher = "permission_prompt|elicitation_dialog|elicitation_url_dialog|agent_needs_input"
        managed_notification = {"matcher": notify_matcher, "hooks": [bell]}
        user_question = {"type": "command", "command": "keep-question"}
        user_question_group = {"matcher": "AskUserQuestion", "hooks": [user_question]}
        user_pretooluse = {"matcher": "Bash", "hooks": [bell]}
        user_notification = {"matcher": "idle_prompt", "hooks": [{**bell, "timeout": 3}]}
        user_stop = {"hooks": [{"type": "command", "command": "keep-stop"}]}
        old = {
            "env": {"USER_KEY": "keep", **user_setting},
            "hooks": {
                "PreToolUse": [{**user_question_group, "hooks": [bell, user_question]}, user_pretooluse],
                "Notification": [managed_notification, user_notification, managed_notification],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": (
                                    "sh -c 'command -v dotfiles-claude-hook >/dev/null 2>&1 || exit 0; "
                                    "dotfiles-claude-hook stop_bell; exit 0'"
                                ),
                            }
                        ]
                    },
                    user_stop,
                ],
            },
        }
        target = tmp_path / "settings.json"
        target.write_text(json.dumps(old), encoding="utf-8")
        overrides = mod._platform_overrides(_PROD_MANAGED_SETTINGS)
        managed = mod.effective_managed_settings(
            json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8")),
            [json.loads(path.read_text(encoding="utf-8")) for path in overrides],
        )

        assert update_claude_settings(_PROD_MANAGED_SETTINGS, target, overrides=overrides) is True
        updated = json.loads(target.read_text(encoding="utf-8"))

        assert updated == mod.preview_managed_settings_update(old, managed)
        hooks = updated["hooks"]
        assert [group for group in hooks["PreToolUse"] if group.get("matcher") == "AskUserQuestion"] == [user_question_group]
        assert user_pretooluse in hooks["PreToolUse"]
        assert hooks["Notification"] == [user_notification, managed_notification]
        assert hooks["Stop"] == [user_stop]
        expected_default = {"AGENT_TOOLKIT_CONFIG_CLAUDE_STOP_BELL": "true"} if platform == "linux" else {}
        assert updated["env"] == {**managed.get("env", {}), **expected_default, "USER_KEY": "keep", **user_setting}
        assert ("AGENT_TOOLKIT_CONFIG_CLAUDE_STOP_BELL" in managed.get("env", {})) is (platform == "linux")
        assert update_claude_settings(_PROD_MANAGED_SETTINGS, target, overrides=overrides) is False

    def test_removed_hook_is_dropped_from_existing(self, tmp_path: Path):
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

        assert "PostToolUse" not in result["hooks"]

        assert result["hooks"]["PreToolUse"][0]["matcher"] == "Write"

    def test_legacy_windows_common_entry_is_dropped_from_existing(self, tmp_path: Path):
        """旧Windows共通hookの起動設定が既存設定に残っていても、除去対象の引数を省略した更新で除去する。"""
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
        """実ホーム絶対パスへ置換された旧Windows自律終了hookの起動設定を、除去対象の引数を省略した更新で除去する。"""
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

    @pytest.mark.parametrize(
        "old_command",
        [
            _LEGACY_WINDOWS_PRETOOLUSE_COMMAND.replace("__HOME__", "C:/Users/Aki User"),
            "powershell -NoProfile -ExecutionPolicy Bypass -File "
            '"C:/Users/Aki User\\dotfiles\\scripts\\claude-hook-pretooluse.ps1"',
        ],
        ids=["distributed-legacy", "moved-to-libexec"],
    )
    def test_old_windows_pretooluse_converges_to_single_registration(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        old_command: str,
    ) -> None:
        """配布済みの旧Windows hookの起動設定とscripts/を指す`-File`の起動設定を、libexec/を指す現行の起動設定1件へ移行する。"""
        home = Path("C:/Users/Aki User")
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
        target_path = tmp_path / "settings.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": old_command}]}
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
            '"C:/Users/Aki User\\dotfiles\\libexec\\claude-hook-pretooluse.ps1"'
        ]

    def test_removed_hook_keeps_sibling_in_same_matcher(self, tmp_path: Path):
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
                True,
            ),
            (
                "powershell -NoProfile -ExecutionPolicy Bypass -File "
                '"C:/Users/Aki User\\dotfiles\\libexec\\claude-hook-pretooluse.ps1"',
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
        """`removed_hook_substrings`を省略した場合の除去パターンが廃止形式を除去し、現行の共通エントリポイントを保持する。"""
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
    @pytest.mark.parametrize(
        ("existing_env", "removed_keys", "expected_env"),
        [
            (
                {"OLD_KEY": "1", "KEEP_KEY": "keep"},
                ("OLD_KEY",),
                {"KEEP_KEY": "keep"},
            ),
            (
                {"OTHER_KEY": "value"},
                ("OLD_KEY",),
                {"OTHER_KEY": "value"},
            ),
            (
                {"OLD_KEY": "1"},
                ("OLD_KEY",),
                None,
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
