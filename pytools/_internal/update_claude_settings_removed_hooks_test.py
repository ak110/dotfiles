"""update_claude_settingsのremoved_hooksの試験。"""

# pylint: disable=protected-access

import json
import sys
from pathlib import Path

import pytest

from pytools._internal.update_claude_settings import update_claude_settings
from pytools._internal.update_claude_settings_test_helpers import (
    _LEGACY_WINDOWS_AUTONOMOUS_EXIT_COMMAND,
    _LEGACY_WINDOWS_PRETOOLUSE_COMMAND,
    _PROD_MANAGED_SETTINGS,
)


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
            # 実体をlibexec/へ移す前のscripts/を指す`-File`の起動設定は除去し、現行の起動設定は保持する
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
        """`removed_env_keys`を省略した場合の廃止キー一覧に含まれる環境変数を既存設定から除去する。"""
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
