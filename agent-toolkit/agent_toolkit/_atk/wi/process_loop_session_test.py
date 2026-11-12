# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""統括セッションのプロンプト、環境、終了コードとコンソール制御を検証する。"""

import argparse
import io
import os
import pathlib
import shutil
import subprocess

import pytest

from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._testing.process_loop_session_support import _isolate_process_loop_commands as _isolate_process_loop_commands


class TestProcessLoopPromptAndEnv:
    """process-loopサブコマンド: claudeへ渡す最初のプロンプトと環境変数、正常終了時の反復継続を検証する。"""

    def test_empty_path_entries_are_preserved(self) -> None:
        """`PATH`の空要素を除去対象に含めないこと。

        POSIXの`PATH`では空要素がカレントディレクトリを表すため、除去すると解決順序が宣言外に変わる。
        """
        venv_root = "/tmp/venv"
        env = {
            "VIRTUAL_ENV": venv_root,
            "PATH": os.pathsep.join((f"{venv_root}/bin", "", "/usr/bin", "")),
        }
        _inherited_venv.strip_inherited_venv(env)
        assert env["PATH"] == os.pathsep.join(("", "/usr/bin", ""))

    @pytest.mark.parametrize("orchestrator", ["claude", "codex", "agy"])
    @pytest.mark.parametrize("resume", [None, "saved-session"])
    def test_manifest_goal_keeps_host_specific_delivery(
        self, tmp_path: pathlib.Path, orchestrator: str, resume: str | None
    ) -> None:
        """manifest付き目標は新規・再開ともClaudeだけに/goalを付けて配送する。"""
        prompt = _pl_session.build_process_loop_prompt()
        argv, _ = _pl_session._build_session_argv(  # pylint: disable=protected-access  # noqa: SLF001
            argparse.Namespace(resume=resume),
            prompt,
            dict(os.environ),
            orchestrator=orchestrator,
            model="test-model",
            effort="medium",
            resume_pending=resume is not None,
            cwd=tmp_path,
        )
        expected = (
            '<atk-auto source="process-loop" kind="goal">\n'
            "このセッションが統括として担当するagent-toolkit:process-wi all-reposの工程を完遂する"
            "（正当な先送りを結果へ記録した終了を含み、終了後に常駐が行う検収は含まない）\n</atk-auto>"
        )
        assert argv[-1] == ("/goal " if orchestrator == "claude" else "") + expected

    def test_prompt_does_not_inject_publish_destination(self) -> None:
        """対象リポジトリ固有の公開先をprocess-loopが目的文へ注入しない。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001

        assert "refs/remotes/origin/master" not in prompt
        assert "公開先" not in prompt
        assert "git push" not in prompt
        assert "commit" not in prompt
        assert "レビュー" not in prompt

    def test_unstartable_candidate_falls_back_to_next_engine(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """候補の実行ファイルを起動できない場合も残る候補を判定する。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium,claude:haiku/medium")
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if cmd[:2] == ["codex", "exec"]:
                raise FileNotFoundError("codex")
            return subprocess.CompletedProcess(cmd, 0, "OK\n", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        selected = _pl_session.select_available_orchestrator(  # pylint: disable=protected-access  # noqa: SLF001
            [("codex", "gpt-5.6-sol", "medium"), ("claude", "sonnet", "high")],
            {},
            tmp_path,
        )

        assert selected[:4] == ("claude", "sonnet", "high", None)
        assert selected[4] is not None and selected[4].kind == "inherit"
        assert [call[0] for call in calls] == ["codex", "claude"]

    def test_all_unstartable_candidates_use_abnormal_exit(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全候補の実行機能を起動できない場合は既存の異常終了へ戻る。"""
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_FAST_MODEL", "codex:gpt-6-luna/medium,claude:haiku/medium")
        calls: list[list[str]] = []

        def fail_start(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            raise OSError("起動不能")

        monkeypatch.setattr(subprocess, "run", fail_start)

        with pytest.raises(SystemExit) as exc_info:
            _pl_session.select_available_orchestrator(  # pylint: disable=protected-access  # noqa: SLF001
                [("codex", "gpt-5.6-sol", "medium"), ("claude", "sonnet", "high")],
                {},
                tmp_path,
            )

        assert exc_info.value.code == 1
        assert [call[0] for call in calls] == ["codex", "claude"]


class TestCodexWindowsSessionConfiguration:
    """Windows Codexだけに適用するプロセスグループとhook互換PATHを検証する。"""

    def test_windows_codex_uses_new_process_group(self) -> None:
        """Windows Codexだけが親と別のコンソール制御グループで起動すること。"""
        assert _pl_env.session_creation_flags("codex", platform="nt") == 0x00000200  # pylint: disable=protected-access  # noqa: SLF001
        assert _pl_env.session_creation_flags("claude", platform="nt") == 0  # pylint: disable=protected-access  # noqa: SLF001
        assert _pl_env.session_creation_flags("codex", platform="posix") == 0  # pylint: disable=protected-access  # noqa: SLF001

    def test_windows_codex_alone_prepends_bash_shim(self) -> None:
        """共通環境を変更せず、Windows Codex用コピーだけへshimを追加すること。"""
        inherited = {"PATH": os.pathsep.join(("first", "second")), "KEEP": "value"}

        codex_env = _pl_env.session_env(inherited, "codex", platform="nt")  # pylint: disable=protected-access  # noqa: SLF001
        claude_env = _pl_env.session_env(inherited, "claude", platform="nt")  # pylint: disable=protected-access  # noqa: SLF001
        posix_env = _pl_env.session_env(inherited, "codex", platform="posix")  # pylint: disable=protected-access  # noqa: SLF001

        shim_dir = pathlib.Path(_process_loop.__file__).resolve().parents[2] / "windows-shims"
        assert pathlib.Path(codex_env["PATH"].split(os.pathsep, maxsplit=1)[0]) == shim_dir
        assert claude_env["CLAUDE_CODE_RETRY_WATCHDOG"] == "1"
        assert "CLAUDE_CODE_RETRY_WATCHDOG" not in codex_env
        assert "CLAUDE_CODE_RETRY_WATCHDOG" not in posix_env
        assert claude_env["KEEP"] == inherited["KEEP"]
        assert posix_env == inherited
        assert inherited["PATH"] == os.pathsep.join(("first", "second"))

    @pytest.mark.skipif(os.name != "nt", reason="Windows用cmd shimの実機検証")
    def test_bash_shim_converts_windows_script_path_and_preserves_stdin_and_exit_code(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Git BashへWindowsパスを渡し、hookの入出力契約を保持すること。"""
        script = tmp_path / "hook.sh"
        script.write_text(
            "read -r value || exit 31\n"
            '[ "$value" = "ok" ] || exit 32\n'
            '[ "$#" -eq 12 ] || exit 33\n'
            '[ "${10}" = "ten value" ] || exit 34\n'
            "exit 23\n",
            encoding="utf-8",
        )
        shim = pathlib.Path(_process_loop.__file__).resolve().parent / "windows-shims" / "bash.cmd"
        env = os.environ.copy()
        env["PATH"] = os.pathsep.join((str(shim.parent), env.get("PATH", "")))

        result = subprocess.run(
            [
                "cmd.exe",
                "/d",
                "/c",
                "bash",
                str(script),
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
                "ten value",
                "eleven",
                "twelve",
            ],
            input=b"ok\n",
            text=False,
            check=False,
            env=env,
        )

        assert result.returncode == 23


class TestProcessLoopReturncode:
    """process-loopサブコマンドのオーケストレーター・OS別returncode判定を検証する。"""

    @pytest.mark.parametrize(
        ("orchestrator", "platform", "returncode", "expected"),
        [
            ("claude", "posix", 0, True),
            ("claude", "posix", -15, True),
            ("claude", "posix", 15, True),
            ("claude", "posix", 143, True),
            ("codex", "posix", 0, True),
            ("codex", "posix", -15, True),
            ("codex", "nt", 0, True),
            ("codex", "posix", 15, False),
            ("codex", "posix", 143, False),
            ("codex", "nt", 15, False),
            ("codex", "nt", 143, False),
            ("claude", "posix", 42, False),
            ("codex", "posix", 42, False),
            ("codex", "nt", 42, False),
        ],
    )
    def test_normal_exit_codes_follow_orchestrator_and_platform(
        self,
        orchestrator: str,
        platform: str,
        returncode: int,
        expected: bool,
    ) -> None:
        """オーケストレーターとOSごとの正常終了コードだけを受け入れること。"""
        assert (
            _pl_session._is_normal_session_exit(  # pylint: disable=protected-access  # noqa: SLF001
                orchestrator,
                returncode,
                platform=platform,
            )
            is expected
        )


class TestConsoleTitleReset:
    """常駐区間のコンソールタイトル制御を検証する（再起動先は`TestProcessLoopUpdateAndRestart`の既存argv検証が担う）。"""

    @pytest.mark.parametrize(
        ("platform", "tty_result", "reset_path", "expected"),
        [
            ("posix", True, "/usr/bin/reset", [["/usr/bin/reset"]]),
            ("nt", True, "/usr/bin/reset", []),
            ("posix", False, "/usr/bin/reset", []),
            ("posix", True, None, []),
            ("posix", AttributeError, "/usr/bin/reset", []),
            ("posix", ValueError, "/usr/bin/reset", []),
        ],
    )
    def test_reset_console_runs_only_on_posix_tty_with_reset_available(
        self,
        monkeypatch: pytest.MonkeyPatch,
        platform: str,
        tty_result: bool | type[Exception],
        reset_path: str | None,
        expected: list[list[str]],
    ) -> None:
        """POSIXのTTYでresetを解決できる場合だけコンソールを初期化する。"""
        calls: list[list[str]] = []

        class TestStream(io.StringIO):
            def isatty(self) -> bool:
                if isinstance(tty_result, type) and issubclass(tty_result, Exception):
                    raise tty_result
                assert isinstance(tty_result, bool)
                return tty_result

        def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            assert kwargs == {"check": False}
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(shutil, "which", lambda command: reset_path if command == "reset" else None)
        monkeypatch.setattr(subprocess, "run", fake_run)

        _pl_env.reset_console(  # pylint: disable=protected-access  # noqa: SLF001
            platform=platform,
            stream=TestStream(),
        )

        assert calls == expected
