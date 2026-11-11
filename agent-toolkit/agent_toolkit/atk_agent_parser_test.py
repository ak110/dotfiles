"""atk agent parserの契約を検証する。"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._atk import worktree_stash as _worktree_stash
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._hooks import user_prompt_submit
from agent_toolkit._testing.atk_support import _FIXED_DT, _isolate_agent_and_managed_temp_environment
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

__all__ = ["_isolate_agent_and_managed_temp_environment"]


class TestWorktreeStashDispatch:
    """`worktree-stash`の公開dispatchを検証する。"""

    def test_worktree_stash_dispatch_receives_private_notes(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """worktree-stash分岐は解決したprivate-notesをdispatchへ渡す。"""
        received: dict[str, object] = {}

        def fake_dispatch(
            args: object,
            *,
            command_dest: str,
            private_notes: pathlib.Path,
        ) -> int:
            received.update(args=args, command_dest=command_dest, private_notes=private_notes)
            return 0

        monkeypatch.setattr(_worktree_stash, "dispatch", fake_dispatch)
        private_notes = tmp_path / "private-notes"
        monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(private_notes))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["worktree-stash", "save", "--label=test"], home=tmp_path)

        assert exc_info.value.code == 0
        assert received["command_dest"] == "worktree_stash_subcommand"
        assert received["private_notes"] == private_notes


class TestAgentsWaitParser:
    """`agents wait`の公開parserを検証する。"""

    def test_accepts_no_arguments(self) -> None:
        """待機対象と待機上限の入力を持たない。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["agents", "wait"])
        assert args.command == "agents"
        assert args.agents_subcommand == "wait"
        assert not hasattr(args, "timeout")
        assert not hasattr(args, "session_id")

    @pytest.mark.parametrize(
        "argv",
        [
            ["agents", "wait", "session-1"],
            ["agents", "wait", "session-1", "--timeout=0"],
            ["agents", "wait", "--timeout=12"],
        ],
    )
    def test_rejects_wait_target_arguments(self, argv: list[str]) -> None:
        """撤去した待機対象の指定を拒否する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 2

    def test_publishes_only_one_wait_subcommand(self) -> None:
        """待機を`agents wait`だけで公開する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        command_action = next(
            action
            for action in parser._actions  # pylint: disable=protected-access  # noqa: SLF001
            if action.dest == "command"
        )
        assert command_action.choices is not None
        command_choices = dict(command_action.choices)
        assert "agents-wait" not in command_choices
        agents_parser = command_choices["agents"]
        agents_action = next(
            action
            for action in agents_parser._actions  # pylint: disable=protected-access  # noqa: SLF001
            if action.dest == "agents_subcommand"
        )
        assert agents_action.choices is not None
        assert "wait" in agents_action.choices


class TestAgentsExitSessionParser:
    """`agents-exit-session`は外部から識別子を受け取らない。"""

    def test_accepts_no_arguments(self) -> None:
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        args = parser.parse_args(["agents-exit-session"])
        assert args.command == "agents-exit-session"

    def test_rejects_target_argument(self) -> None:
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["agents-exit-session", "123"])
        assert exc_info.value.code == 2


class TestWaitScheduleParser:
    """`wait-schedule`の公開parserとdispatchを検証する。"""

    def test_accepts_no_arguments(self) -> None:
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        assert parser.parse_args(["wait-schedule"]).command == "wait-schedule"

    @pytest.mark.parametrize(
        "argv",
        [
            ["wait-schedule", "--request-bucket=main"],
            ["wait-schedule", "--format", "json"],
            ["wait-schedule", "extra"],
        ],
    )
    def test_rejects_removed_or_extra_arguments(self, argv: list[str]) -> None:
        """廃止した`--request-bucket`・`--format`と余分な引数を拒否する。"""
        parser = atk._build_parser()  # pylint: disable=protected-access  # noqa: SLF001
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(argv)
        assert exc_info.value.code == 2

    def test_outputs_main_schedule_and_prompt_as_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """メインのcron式と、1行目がUserPromptSubmitの機械注入の標識であるpromptを1行のJSONで出力する。"""
        buckets: list[str] = []

        def fake_get_schedule(request_bucket: str) -> str:
            buckets.append(request_bucket)
            return "*/3 * * * *"

        monkeypatch.setattr(_wait_schedule, "get_schedule", fake_get_schedule)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path)
        assert exc_info.value.code == 0
        out = capsys.readouterr().out
        assert out.count("\n") == 1
        payload = json.loads(out)
        assert buckets == ["main"]
        assert payload["cron"] == "*/3 * * * *"
        assert payload["prompt"].split("\n", 1)[0] == user_prompt_submit.PERIODIC_RECHECK_MARKER

    def test_sweeps_managed_temp_before_dispatch(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """任意のサブコマンドに共通する実行処理でmanaged-tempを整理する。"""
        calls: list[datetime.datetime] = []

        def fake_sweep(*, now: datetime.datetime) -> _managed_temp.SweepResult:
            calls.append(now)
            return _managed_temp.SweepResult([], ())

        setattr_in_managed_temp_modules(monkeypatch, "sweep_managed_temp", fake_sweep)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        assert calls == [_FIXED_DT]

    @pytest.mark.parametrize(
        ("delegated_session", "expects_warning"),
        [(None, True), ("1", False), ("other", True)],
    )
    def test_sweep_failure_warning_depends_on_delegated_session(
        self,
        delegated_session: str | None,
        expects_warning: bool,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """削除できなかった対象の警告は、回収を判断できない委譲先（値が1）だけで抑止する。"""
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
        monkeypatch.setattr(_wait_schedule, "get_schedule", lambda _request_bucket: "*/3 * * * *")
        setattr_in_managed_temp_modules(
            monkeypatch,
            "sweep_managed_temp",
            lambda *, now: _managed_temp.SweepResult([], ((tmp_path / "orphan", "削除失敗"),)),
        )
        if delegated_session is None:
            monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
        else:
            monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", delegated_session)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path, now=_FIXED_DT)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["cron"] == "*/3 * * * *"
        assert ("自動削除できなかった" in captured.err) is expects_warning

    def test_does_not_expose_environment_values(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """環境変数値を公開出力へ混在させない。"""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "environment-secret")
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["cron"] == "*/3 * * * *"
        assert not captured.err
        assert "environment-secret" not in captured.out + captured.err

    def test_does_not_expose_auth_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """認証応答を公開出力へ混在させない。"""

        def fake_run(cmd: list[str], *args: object, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            del args
            del kwargs
            return subprocess.CompletedProcess(
                cmd,
                returncode=0,
                stdout=json.dumps(
                    {
                        "loggedIn": True,
                        "authMethod": "claude.ai",
                        "subscriptionType": "max",
                        "token": "auth-secret",
                    }
                ),
                stderr="auth-error-secret",
            )

        monkeypatch.setattr(_wait_schedule.subprocess, "run", fake_run)
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wait-schedule"], home=tmp_path)
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert json.loads(captured.out)["cron"] == "*/30 * * * *"
        assert not captured.err
        assert "auth-secret" not in captured.out + captured.err
        assert "auth-error-secret" not in captured.out + captured.err
