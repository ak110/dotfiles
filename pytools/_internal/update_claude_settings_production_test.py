"""update_claude_settingsのproductionの試験。"""

# pylint: disable=protected-access

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pytools._internal import removal_registry
from pytools._internal import update_claude_settings as mod
from pytools._internal.update_claude_settings import update_claude_settings
from pytools._internal.update_claude_settings_test_helpers import (
    _PROD_MANAGED_SETTINGS,
    _PROD_MANAGED_SETTINGS_FILES,
    _PROD_POSTTOOLUSE_SCRIPT,
    _PROD_PRETOOLUSE_SCRIPT,
)


class TestProductionManagedSettings:
    """配布原本のプラットフォーム別記述規約とWindows向けhookの起動契約を検証する。"""

    @pytest.mark.parametrize("suffix", ["posix", "win32"])
    def test_personal_stop_hooks_match_platform_contract(self, suffix: str):
        """OS別設定の個人Stop hookが各プラットフォームの契約と一致する。"""
        path = _PROD_MANAGED_SETTINGS.with_suffix(f".{suffix}.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        stop_groups = data["hooks"].get("Stop", [])
        commands = [hook["command"] for group in stop_groups for hook in group["hooks"]]
        assert sum("dotfiles-claude-hook autonomous_exit" in command for command in commands) == 0
        assert sum("dotfiles-claude-hook stop_bell" in command for command in commands) == (1 if suffix == "posix" else 0)
        assert "dotfiles-claude-hook stop" not in commands
        assert ("Stop" in data["hooks"]) is (suffix == "posix")

    def test_managed_settings_declares_no_deny_rules(self):
        """配布設定は秘匿ファイルの読取禁止を持たず、プロジェクト側設定へ委ねる。"""
        data = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))
        assert "deny" not in data["permissions"]

    @pytest.mark.parametrize(
        ("event", "hook_exit_code", "expected_exit_code"),
        [
            ("PreToolUse", 0, 0),
            ("PreToolUse", 1, 0),
            ("PreToolUse", 2, 0),
            ("Stop", 1, 0),
            ("Stop", 2, 0),
        ],
    )
    def test_posix_hook_commands_preserve_exit_contract(
        self,
        tmp_path: Path,
        event: str,
        hook_exit_code: int,
        expected_exit_code: int,
    ) -> None:
        """POSIX個人hookは警告だけを返すため、どの終了コードも呼び出し元へ伝えず0で終える。"""
        path = _PROD_MANAGED_SETTINGS.with_suffix(".posix.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        command = next(
            hook["command"]
            for group in data["hooks"][event]
            for hook in group["hooks"]
            if "dotfiles-claude-hook" in hook["command"]
        )
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_path = tmp_path / "hook-args.json"
        hook_path = bin_dir / "dotfiles-claude-hook"
        hook_path.write_text(
            '#!/bin/sh\nprintf \'%s\' "$1" > "$HOOK_ARGS_PATH"\nexit "$HOOK_EXIT_CODE"\n',
            encoding="utf-8",
        )
        hook_path.chmod(0o755)
        env = os.environ.copy()
        env.update(
            {
                "PATH": os.pathsep.join((str(bin_dir), "/usr/bin", "/bin")),
                "HOOK_ARGS_PATH": str(args_path),
                "HOOK_EXIT_CODE": str(hook_exit_code),
            }
        )

        result = subprocess.run(["/bin/sh", "-c", command], check=False, env=env)

        assert result.returncode == expected_exit_code
        expected_subcommand = {
            "PreToolUse": "pretooluse",
            "Stop": "stop_bell",
        }[event]
        assert args_path.read_text(encoding="utf-8") == expected_subcommand

    @pytest.mark.parametrize("event", ["PreToolUse", "Stop"])
    def test_posix_hook_commands_allow_missing_console_script(self, tmp_path: Path, event: str) -> None:
        """POSIX個人hookはconsole scriptが存在しない場合も通過させる。"""
        path = _PROD_MANAGED_SETTINGS.with_suffix(".posix.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        command = next(
            hook["command"]
            for group in data["hooks"][event]
            for hook in group["hooks"]
            if "dotfiles-claude-hook" in hook["command"]
        )
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(empty_bin), "/usr/bin", "/bin"))

        result = subprocess.run(["/bin/sh", "-c", command], check=False, env=env, capture_output=True)

        assert result.returncode == 0
        assert result.stdout == b""
        assert result.stderr == b""

    def test_windows_pretooluse_script_uses_bom_and_crlf(self):
        """PowerShellスクリプトはBOM付きUTF-8とCRLF改行で書く。"""
        raw = _PROD_PRETOOLUSE_SCRIPT.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")

    def test_windows_posttooluse_script_uses_bom_and_crlf(self):
        """PostToolUseの互換起動用のPowerShellスクリプトもBOM付きUTF-8とCRLF改行で書く。"""
        raw = _PROD_POSTTOOLUSE_SCRIPT.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        assert b"\r\n" in raw
        assert b"\n" not in raw.replace(b"\r\n", b"")

    @pytest.mark.parametrize(("hook_exit_code", "expected_exit_code"), [(0, 0), (1, 0), (2, 0)])
    @pytest.mark.parametrize("invocation", ["direct", "bash"])
    def test_windows_pretooluse_script_runs_via_pwsh(
        self,
        tmp_path: Path,
        hook_exit_code: int,
        expected_exit_code: int,
        invocation: str,
    ) -> None:
        """PowerShell実行時も引数境界を維持し、警告だけを返すhookとしてどの終了コードも0で終える。"""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwshを利用できないためPowerShell実行時テストを省略する")

        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        args_path = tmp_path / f"hook-args-{invocation}-{hook_exit_code}.json"
        stub_path = tmp_path / "hook_stub.py"
        stub_path.write_text(
            """import json
import os
import sys
from pathlib import Path

Path(os.environ["HOOK_ARGS_PATH"]).write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
sys.exit(int(os.environ["HOOK_EXIT_CODE"]))
""",
            encoding="utf-8",
        )
        hook_path = bin_dir / "dotfiles-claude-hook"
        hook_path.write_text(
            f"#!{sys.executable}\nimport runpy\nrunpy.run_path({str(stub_path)!r}, run_name='__main__')\n",
            encoding="utf-8",
        )
        hook_path.chmod(0o755)
        (bin_dir / "dotfiles-claude-hook.cmd").write_text(
            f'@"{sys.executable}" "{stub_path}" %*\r\n',
            encoding="utf-8",
        )

        env = os.environ.copy()
        env.update(
            {
                "PATH": os.pathsep.join((str(bin_dir), env["PATH"])),
                "HOOK_ARGS_PATH": str(args_path),
                "HOOK_EXIT_CODE": str(hook_exit_code),
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

        assert result.returncode == expected_exit_code
        assert json.loads(args_path.read_text(encoding="utf-8")) == ["pretooluse"]

    def test_windows_pretooluse_script_allows_missing_console_script(self, tmp_path: Path) -> None:
        """PowerShellスクリプトはconsole scriptが存在しない場合も通過させる。"""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwshを利用できないためPowerShell実行時テストを省略する")
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        env = os.environ.copy()
        env["PATH"] = str(empty_bin)

        result = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(_PROD_PRETOOLUSE_SCRIPT)],
            check=False,
            env=env,
        )

        assert result.returncode == 0

    @pytest.mark.parametrize("hook_exit_code", [0, 1, 2])
    def test_windows_posttooluse_command_always_succeeds(self, tmp_path: Path, hook_exit_code: int) -> None:
        """撤去前の設定が起動するPostToolUseの互換起動スクリプトは、console scriptの有無によらず正常終了する。"""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwshを利用できないためPowerShell実行時テストを省略する")
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        hook_path = bin_dir / "dotfiles-claude-hook"
        hook_path.write_text(f"#!/bin/sh\nexit {hook_exit_code}\n", encoding="utf-8")
        hook_path.chmod(0o755)
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(bin_dir), "/usr/bin", "/bin"))

        result = subprocess.run([pwsh, "-NoProfile", "-File", str(_PROD_POSTTOOLUSE_SCRIPT)], check=False, env=env)

        assert result.returncode == 0

    def test_windows_posttooluse_command_allows_missing_console_script(self, tmp_path: Path) -> None:
        """PostToolUseの互換起動スクリプトは、console scriptが存在しない場合も出力なしで正常終了する。"""
        pwsh = shutil.which("pwsh")
        if pwsh is None:
            pytest.skip("pwshを利用できないためPowerShell実行時テストを省略する")
        empty_bin = tmp_path / "bin"
        empty_bin.mkdir()
        env = os.environ.copy()
        env["PATH"] = str(empty_bin)

        result = subprocess.run(
            [pwsh, "-NoProfile", "-File", str(_PROD_POSTTOOLUSE_SCRIPT)],
            check=False,
            env=env,
            capture_output=True,
        )

        assert result.returncode == 0
        assert result.stdout == b""
        assert result.stderr == b""

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
        ]
        assert len(commands) == 1
        assert '-File "C:/Users/Aki User\\dotfiles\\libexec\\claude-hook-pretooluse.ps1"' in commands[0]

    def test_posix_personal_hooks_converge_to_single_current_registration(self, tmp_path: Path) -> None:
        """旧形式と現行形式が併存する設定へ適用すると、個人hookは現行形式1件ずつになりPostToolUseは残らない。"""
        current = json.loads(_PROD_MANAGED_SETTINGS.with_suffix(".posix.json").read_text(encoding="utf-8"))["hooks"]
        current_commands = {
            event: next(
                hook["command"]
                for group in current[event]
                for hook in group["hooks"]
                if "dotfiles-claude-hook" in hook["command"]
            )
            for event in ("PreToolUse", "Stop")
        }
        existing_hooks = {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit|MultiEdit",
                    "hooks": [
                        {
                            "type": "command",
                            "command": "sh -c 'dotfiles-claude-hook pretooluse; code=$?; [ $code -eq 2 ] && exit 2 || exit 0'",
                        }
                    ],
                },
                {"matcher": "Write|Edit|MultiEdit", "hooks": [{"type": "command", "command": current_commands["PreToolUse"]}]},
            ],
            "PostToolUse": [
                {
                    "matcher": "Skill|Read",
                    "hooks": [{"type": "command", "command": "sh -c 'dotfiles-claude-hook posttooluse; exit 0'"}],
                },
                {
                    "matcher": "Skill|Read",
                    "hooks": [
                        {
                            "type": "command",
                            "command": (
                                "sh -c 'command -v dotfiles-claude-hook >/dev/null 2>&1 || exit 0; "
                                "dotfiles-claude-hook posttooluse; exit 0'"
                            ),
                        }
                    ],
                },
            ],
            "Stop": [
                {"hooks": [{"type": "command", "command": "sh -c 'dotfiles-claude-hook stop_bell; exit 0'"}]},
                {"hooks": [{"type": "command", "command": current_commands["Stop"]}]},
            ],
        }
        target_path = tmp_path / "settings.json"
        target_path.write_text(json.dumps({"hooks": existing_hooks}), encoding="utf-8")

        update_claude_settings(
            _PROD_MANAGED_SETTINGS, target_path, overrides=[_PROD_MANAGED_SETTINGS.with_suffix(".posix.json")]
        )

        hooks = json.loads(target_path.read_text(encoding="utf-8"))["hooks"]
        personal = {
            event: [hook["command"] for group in groups for hook in group["hooks"] if "dotfiles-claude-hook" in hook["command"]]
            for event, groups in hooks.items()
        }
        assert personal["PreToolUse"] == [current_commands["PreToolUse"]]
        assert personal["Stop"] == [current_commands["Stop"]]
        assert not personal.get("PostToolUse")


class TestManagedAutoModeSchema:
    """配布原本の`autoMode`サブキーが公式スキーマの許容集合に含まれるか確かめる。"""

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


class TestRetiredAutoModeAllowLabels:
    """再編で廃止した`autoMode.allow`ラベルが配布先から除去されることの検査。

    `_strip_stale_labeled_list_items`は配布原本に現存するラベルの旧文面だけを除去するため、
    配布原本から消えたラベルは`_REMOVED_LIST_ITEM_SUBSTRINGS`へ登録しないと配布先に残り続ける。
    """

    _RETIRED_LABELS = (
        "Reconsidered Retry Approval",
        "Session-Owned Amend",
        "Exit-Session Termination",
        "AWI-Originated Gate Revision",
        "Feedback-Originated Gate Revision",
        "Background Operator Auto-Approval",
        "External Marketplace Registration",
        "Agent Config Read",
        "Delegation Continuation Message",
        "Release Workflow Dispatch",
        "Personal Repo Default-Branch Push",
        "Merge Approval",
        "Plan File Write",
        "WI Queue State Transition",
        "Feedback Queue State Transition",
    )

    def test_retired_labels_are_stripped_from_user_settings(self, tmp_path: Path) -> None:
        """配布反映で、配布先に残る旧ラベルが除去され現行ルールとユーザー独自エントリが残る。"""
        target_path = tmp_path / "settings.json"
        target_path.write_text(
            json.dumps(
                {
                    "autoMode": {
                        "allow": [
                            "ラベルを持たないユーザー独自ルール",
                            *[f"{label}: 旧文面" for label in self._RETIRED_LABELS],
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
            removed_list_item_substrings=removal_registry.values(mod._REMOVED_LIST_ITEM_SUBSTRINGS),  # pylint: disable=protected-access
            stale_labeled_list_paths=("autoMode.allow",),
        )

        allow = json.loads(target_path.read_text(encoding="utf-8"))["autoMode"]["allow"]
        assert [item for item in allow if any(item.startswith(f"{label}: ") for label in self._RETIRED_LABELS)] == []
        assert "ラベルを持たないユーザー独自ルール" in allow
        managed_allow = json.loads(_PROD_MANAGED_SETTINGS.read_text(encoding="utf-8"))["autoMode"]["allow"]
        assert [item for item in managed_allow if item not in allow] == []
