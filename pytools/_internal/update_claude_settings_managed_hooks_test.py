"""update_claude_settings: 管理対象フック検証テスト。

TestRemovedHookCommands、TestWarnOrphanDotfilesHookCommands、TestNormalizeManagedHooks を含む。
テストは update_claude_settings_test.py から分割。
"""

import json
import logging
import typing
from pathlib import Path

import pytest

from pytools._internal._test_helpers import run_update_claude_settings
from pytools._internal.update_claude_settings import _substitute_home_placeholder, update_claude_settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROD_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.json"


def _run(tmp_path: Path, managed: dict, existing: dict | None = None) -> dict:
    """update_claude_settings でマージしてターゲット結果を返す。"""
    return run_update_claude_settings(tmp_path, managed, existing)


class TestRemovedHookCommands:
    """配布原本から削除したフックコマンドの除去を検証する。"""

    _POSIX_MANAGED_SETTINGS = _REPO_ROOT / "share" / "claude_settings_json_managed.posix.json"
    _LEGACY_PRETOOLUSE_COMMAND = (
        "sh -c 'uv run --no-project --script ~/dotfiles/scripts/claude_hook.py pretooluse; "
        "code=$?; [ $code -eq 2 ] && exit 2 || exit 0'"
    )

    @staticmethod
    def _pretooluse_commands(data: dict) -> list[str]:
        """`PreToolUse`のフックコマンドを列挙する。"""
        entries = data.get("hooks", {}).get("PreToolUse", [])
        return [hook["command"] for entry in entries for hook in entry.get("hooks", [])]

    def test_removes_legacy_pretooluse_command_and_keeps_the_current_one(self, tmp_path: Path) -> None:
        """スクリプト実在検査を持たない旧コマンドだけを除き、配布原本の現行コマンドは残す。"""
        override = typing.cast(
            "dict",
            _substitute_home_placeholder(json.loads(self._POSIX_MANAGED_SETTINGS.read_text(encoding="utf-8"))),
        )
        current_commands = self._pretooluse_commands(override)
        assert current_commands
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "",
                                "hooks": [
                                    {"type": "command", "command": command}
                                    for command in (self._LEGACY_PRETOOLUSE_COMMAND, *current_commands)
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        update_claude_settings(_PROD_MANAGED_SETTINGS, target_path, overrides=[self._POSIX_MANAGED_SETTINGS])

        merged = self._pretooluse_commands(json.loads(target_path.read_text(encoding="utf-8")))
        assert self._LEGACY_PRETOOLUSE_COMMAND not in merged
        assert all(command in merged for command in current_commands)


class TestWarnOrphanDotfilesHookCommands:
    """`_warn_orphan_dotfiles_hook_commands`の警告機能テスト。"""

    def test_warns_orphan_dotfiles_hook_command(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """配布原本に存在しない`dotfiles/scripts/`参照のフックコマンドを確定文面で警告する。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        orphan_command = "sh -c 'uv run --script ~/dotfiles/scripts/orphan_hook.py'"
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": orphan_command,
                                    }
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with caplog.at_level(logging.INFO, logger="pytools._internal.update_claude_settings"):
            update_claude_settings(managed_path, target_path)

        assert f"配布原本に存在しないフックコマンド（settings.json）: {orphan_command}" in caplog.messages

    def test_does_not_warn_for_commands_present_in_either_platform_source(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """posix・win32いずれかの原本に存在するコマンドは警告しない。"""
        managed_path = tmp_path / "managed.json"
        managed_path.write_text("{}", encoding="utf-8")
        posix_path = tmp_path / "managed.posix.json"
        posix_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "sh -c 'uv run --script ~/dotfiles/scripts/exist_hook.py'",
                                    }
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps(
                {
                    "hooks": {
                        "PreToolUse": [
                            {
                                "matcher": "Write",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "sh -c 'uv run --script ~/dotfiles/scripts/exist_hook.py'",
                                    }
                                ],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with caplog.at_level(logging.INFO, logger="pytools._internal.update_claude_settings"):
            update_claude_settings(managed_path, target_path, overrides=[posix_path])

        assert not any("exist_hook.py" in message for message in caplog.messages)

    def test_does_not_warn_for_non_dotfiles_hook_command(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """`dotfiles/scripts/`を参照しないコマンド（プラグイン由来・利用者追加）は警告しない。"""
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
                                "hooks": [{"type": "command", "command": "sh -c 'custom-hook-from-plugin'"}],
                            }
                        ]
                    }
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        with caplog.at_level(logging.INFO, logger="pytools._internal.update_claude_settings"):
            update_claude_settings(managed_path, target_path)

        assert not any("custom-hook-from-plugin" in message for message in caplog.messages)


class TestNormalizeManagedHooks:
    """管理対象フックを現在の構造へ正規化する回帰テスト。"""

    @pytest.mark.parametrize(
        "override_path",
        [
            _REPO_ROOT / "share" / "claude_settings_json_managed.posix.json",
            _REPO_ROOT / "share" / "claude_settings_json_managed.win32.json",
        ],
        ids=("posix", "windows"),
    )
    def test_production_old_split_and_current_combined_entries_are_normalized(
        self,
        tmp_path: Path,
        override_path: Path,
    ):
        """実在するOS別配布元で旧個別要素を正規化し、WindowsのStop不在も検証する。"""
        override = json.loads(override_path.read_text(encoding="utf-8"))
        if "Stop" not in override["hooks"]:
            assert override_path.name == "claude_settings_json_managed.win32.json"
            return
        managed_entries = override["hooks"]["Stop"]
        expected_entries = typing.cast(
            list[dict[str, list[dict[str, str]]]],
            _substitute_home_placeholder(managed_entries),
        )
        managed_hooks = [hook for entry in expected_entries for hook in entry["hooks"]]
        existing_entries = [{"hooks": [hook]} for hook in managed_hooks] + expected_entries
        target_path = tmp_path / "target.json"
        target_path.write_text(
            json.dumps({"hooks": {"Stop": existing_entries}}, ensure_ascii=False),
            encoding="utf-8",
        )

        update_claude_settings(
            _PROD_MANAGED_SETTINGS,
            target_path,
            overrides=[override_path],
            removed_hook_substrings=(),
            removed_env_keys=(),
            removed_list_item_substrings=(),
        )
        first = json.loads(target_path.read_text(encoding="utf-8"))
        changed_again = update_claude_settings(
            _PROD_MANAGED_SETTINGS,
            target_path,
            overrides=[override_path],
            removed_hook_substrings=(),
            removed_env_keys=(),
            removed_list_item_substrings=(),
        )
        second = json.loads(target_path.read_text(encoding="utf-8"))

        assert first["hooks"]["Stop"] == expected_entries
        assert second == first
        assert not changed_again

    def test_custom_command_in_same_entry_is_preserved(self, tmp_path: Path):
        """管理対象コマンドと同居する利用者独自コマンドを保持する。"""
        managed_command = "managed-stop"
        custom_hook = {"type": "command", "command": "custom-stop"}
        managed_entry = {"hooks": [{"type": "command", "command": managed_command}]}
        existing_entry = {"hooks": [managed_entry["hooks"][0], custom_hook]}

        result = _run(
            tmp_path,
            {"hooks": {"Stop": [managed_entry]}},
            {"hooks": {"Stop": [existing_entry]}},
        )

        assert result["hooks"]["Stop"] == [{"hooks": [custom_hook]}, managed_entry]

    def test_unmanaged_event_is_preserved(self, tmp_path: Path):
        """同じコマンド文字列でも管理対象外イベントのフックは保持する。"""
        managed_hook = {"type": "command", "command": "managed-command"}
        unmanaged_entry = {"hooks": [managed_hook]}

        result = _run(
            tmp_path,
            {"hooks": {"Stop": [{"hooks": [managed_hook]}]}},
            {"hooks": {"PostToolUse": [unmanaged_entry]}},
        )

        assert result["hooks"]["PostToolUse"] == [unmanaged_entry]
        assert result["hooks"]["Stop"] == [{"hooks": [managed_hook]}]
