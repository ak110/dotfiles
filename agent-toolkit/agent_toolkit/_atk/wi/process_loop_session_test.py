"""`atk wi process-loop`のプロンプトとオーケストレーターの選択、セッションの起動と終了の扱いのテスト。"""

import argparse
import contextlib
import io
import json
import os
import pathlib
import shutil
import stat
import subprocess
import uuid
from typing import Any, NoReturn, cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._atk.wi import process_loop as _process_loop
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._common import automated_prompt as _automated_prompt
from agent_toolkit._common import (
    claude_usage_limit as _claude_usage_limit,  # noqa: E402  # pylint: disable=wrong-import-position
)
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common import wait_schedule as _wait_schedule
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.process_loop_support import (
    fake_run_with_remote_url,
    hook_debug_log,
    isolate_process_loop_commands,
    set_orchestrate_model,
)
from agent_toolkit.atk_test import _setup_notes


def _run_two_sessions(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, extra_argv: list[str]) -> list[dict[str, Any]]:
    """処理対象が2回続く常駐ループを`--no-update`と`extra_argv`で動かし、起動したセッションの呼び出しを返す。"""
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    claude_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
    counts = iter([1, 1, 0])
    monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

    def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

    with pytest.raises(SystemExit):
        atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", *extra_argv], home=tmp_path)
    return claude_calls


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


_PROCESS_LOOP_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"


_PROCESS_LOOP_SESSION_ID_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"


_DELEGATED_SESSION_ENV = "AGENT_TOOLKIT_DELEGATED_SESSION"


class TestProcessLoopPromptAndEnv:
    """process-loopサブコマンド: claudeへ渡す最初のプロンプトと環境変数、正常終了時の反復継続を検証する。"""

    def test_invokes_claude_with_prompt_env_and_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """新規セッションのプロンプトが単一の`/goal`条件であり、
        `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`が付与され、`returncode=0`後は反復継続すること。
        プロンプトキャッシュTTLが5分の場合は質問タイムアウトへ`60s`を渡すこと。
        件数0到達後は`_wait_for_changes`が呼ばれ、待機解除後に件数再チェックへ戻ること。
        2回目の`_wait_for_changes`呼び出しで`KeyboardInterrupt`を送出し常駐ループを正常終了する。
        ランチャーとの再起動要求の受け渡しファイルを指す環境変数は子セッションへ渡さないことも確認する。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        # ランチャーとの再起動要求の受け渡しファイルは自プロセス専用であり、子孫セッションへは渡さない。
        monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(tmp_path / "restart-spec"))
        monkeypatch.setenv("CLAUDE_CODE_DEBUG_LOGS_DIR", str(tmp_path / "ignored-debug.log"))
        monkeypatch.setenv(_PROCESS_LOOP_SESSION_ENV, "new-original")
        monkeypatch.setenv(_PROCESS_LOOP_SESSION_ID_ENV, "stale-original")
        monkeypatch.setattr(_wait_schedule, "get_prompt_cache_ttl", lambda _bucket: "5m")
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        closed_descriptors: list[int] = []
        hook_descriptors: list[int] = []
        real_close = os.close
        real_fchmod = os.fchmod

        def record_fchmod(descriptor: int, mode: int) -> None:
            hook_descriptors.append(descriptor)
            real_fchmod(descriptor, mode)

        def record_close(descriptor: int) -> None:
            if hook_descriptors and descriptor == hook_descriptors[-1]:
                closed_descriptors.append(descriptor)
            real_close(descriptor)

        # 共通起動の掃引も`os.fchmod`で期限判定記録を書くため、掃引を外してhook診断ログの記述子だけを数える。
        setattr_in_managed_temp_modules(
            monkeypatch, "sweep_managed_temp", lambda *, now: _managed_temp.SweepResult([], (), None)
        )
        monkeypatch.setattr(_process_loop.os, "fchmod", record_fchmod)
        monkeypatch.setattr(_process_loop.os, "close", record_close)

        # 件数: 1回目は1件（claude起動）、2回目以降は0件（待機ループへ）
        count_calls: list[int] = []

        def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
            del private_notes, target_repo
            count_calls.append(len(count_calls))
            return 1 if len(count_calls) == 1 else 0

        wait_calls: list[int] = []

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            wait_calls.append(len(wait_calls))
            if len(wait_calls) >= 2:
                raise KeyboardInterrupt

        monkeypatch.setattr(_wi_readiness, "count_pending_entries", fake_count_pending_entries)
        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1
        prompt = claude_calls[0]["cmd"][-1]
        assert prompt.startswith("/goal ")
        assert "agent-toolkit:process-wi" in prompt
        # cwdをmyrepoへ固定し、claudeセッション内のcwd依存コマンドの解決先を対象リポジトリへ揃える。
        assert claude_calls[0]["cwd"] == myrepo
        command = claude_calls[0]["cmd"]
        debug_log = hook_debug_log(command)
        assert debug_log.parent == tmp_path / ".claude" / "debug"
        if os.name != "nt":
            assert stat.S_IMODE(debug_log.stat().st_mode) == 0o600
        assert command[4:6] + command[8:13] == [
            "--settings",
            '{"askUserQuestionTimeout": "60s", "dialogExpiry": "60s", "remoteControlAtStartup": false}',
            "--permission-mode=auto",
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
        ]
        assert "--autocompact" not in command
        assert "--session-id" in command
        session_id = command[command.index("--session-id") + 1]
        assert uuid.UUID(session_id).version == 4
        assert claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ENV] == "1"
        assert claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ID_ENV] == session_id
        assert "AGENT_TOOLKIT_RESTART_SPEC" not in claude_calls[0]["env"]
        assert len(wait_calls) == 2
        captured = capsys.readouterr()
        assert "Ctrl+Cを検知しました" in captured.out
        assert f"Claude hook診断ログ: {debug_log}" in captured.out
        assert closed_descriptors == hook_descriptors
        assert os.environ[_PROCESS_LOOP_SESSION_ENV] == "new-original"
        assert os.environ[_PROCESS_LOOP_SESSION_ID_ENV] == "stale-original"
        with pytest.raises(OSError):
            os.fstat(closed_descriptors[0])

    def test_abort_request_before_first_session_stops_without_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """反復の開始時点に中断要求があれば、子セッションを起動せずベルを鳴らして終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        session_calls: list[dict[str, Any]] = []
        sleeps: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, session_calls, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)
        monkeypatch.setattr(_process_loop.time, "sleep", sleeps.append)

        with pytest.raises(SystemExit) as abort_exit:
            atk.main(["wi", "process-loop", "abort"], home=tmp_path)
        assert abort_exit.value.code == 0
        capsys.readouterr()

        with pytest.raises(SystemExit) as loop_exit:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert loop_exit.value.code == 0
        assert not session_calls
        assert capsys.readouterr().err == "\a\a\a"
        assert sleeps == [0.1, 0.1]
        assert not (tmp_path / "state" / "agent-toolkit" / "process-wi-abort").exists()

    def test_abort_requested_during_session_stops_before_restart(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """オプションを省略して起動した場合も、セッション中の中断要求を再起動より先に検出して終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        session_calls: list[dict[str, Any]] = []
        sleeps: list[float] = []
        restart_calls: list[tuple[object, ...]] = []
        base_run = fake_run_with_remote_url(myrepo, session_calls, 0)
        abort_path = tmp_path / "state" / "agent-toolkit" / "process-wi-abort"
        abort_requests: list[int] = []

        def request_abort_after_session(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            result = base_run(cmd, *args, **kwargs)
            if session_calls and not abort_requests:
                abort_requests.append(len(abort_requests))
                with contextlib.suppress(SystemExit):
                    atk.main(["wi", "process-loop", "abort"], home=tmp_path)
            return result

        def record_restart(*args: Any, **kwargs: Any) -> None:
            del kwargs
            restart_calls.append(args)

        monkeypatch.setattr(subprocess, "run", request_abort_after_session)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)
        monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: None)
        monkeypatch.setattr(_pl_update, "restart_process_loop", record_restart)
        monkeypatch.setattr(_process_loop.time, "sleep", sleeps.append)
        capsys.readouterr()

        with pytest.raises(SystemExit) as loop_exit:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-alerts"], home=tmp_path)

        assert loop_exit.value.code == 0
        assert len(session_calls) == 1
        assert not restart_calls
        assert capsys.readouterr().err == "\a\a\a"
        assert sleeps == [0.1, 0.1]
        assert not abort_path.exists()

    def test_abort_requested_during_wait_stops_at_next_iteration(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """0件の待機中に設定した中断要求を、復帰後の反復境界で検出して終了する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        session_calls: list[dict[str, Any]] = []
        sleeps: list[float] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, session_calls, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 0)
        monkeypatch.setattr(_process_loop.time, "sleep", sleeps.append)

        def request_abort_while_waiting(*_args: object, **_kwargs: object) -> bool:
            with contextlib.suppress(SystemExit):
                atk.main(["wi", "process-loop", "abort"], home=tmp_path)
            return True

        monkeypatch.setattr(_pl_watch, "wait_for_changes", request_abort_while_waiting)
        capsys.readouterr()

        with pytest.raises(SystemExit) as loop_exit:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert loop_exit.value.code == 0
        assert not session_calls
        assert capsys.readouterr().err == "\a\a\a"
        assert sleeps == [0.1, 0.1]
        assert not (tmp_path / "state" / "agent-toolkit" / "process-wi-abort").exists()

    def test_cancelled_abort_request_keeps_loop_running(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """解除済みの中断要求では現セッション後も反復を継続し、ベルを鳴らさない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        session_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, session_calls, 0))
        monkeypatch.setattr(
            _wi_readiness,
            "count_pending_entries",
            lambda *_a, **_kw: 1 if not session_calls else 0,
        )

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", "abort"], home=tmp_path)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", "abort-cancel"], home=tmp_path)
        capsys.readouterr()

        with pytest.raises(SystemExit) as loop_exit:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert loop_exit.value.code == 0
        assert len(session_calls) == 1
        assert capsys.readouterr().err == ""

    @pytest.mark.parametrize("configured", [None, "", "relative-config"], ids=["unset", "empty", "relative"])
    def test_hook_debug_log_uses_home_when_config_dir_is_unset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        configured: str | None,
    ) -> None:
        """`CLAUDE_CONFIG_DIR`が未設定・空・相対パスの場合はユーザーホーム配下`.claude/debug/`へ保存する。

        Claude Codeの設定ディレクトリを解決する他の処理と同じく、空でない絶対パスだけを採用する。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.delenv(_PROCESS_LOOP_SESSION_ENV, raising=False)
        monkeypatch.delenv(_PROCESS_LOOP_SESSION_ID_ENV, raising=False)
        if configured is None:
            monkeypatch.delenv("CLAUDE_CONFIG_DIR")
        else:
            monkeypatch.setenv("CLAUDE_CONFIG_DIR", configured)
        monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: tmp_path))
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        assert hook_debug_log(claude_calls[0]["cmd"]).parent == tmp_path / ".claude" / "debug"
        assert _PROCESS_LOOP_SESSION_ENV not in os.environ
        assert _PROCESS_LOOP_SESSION_ID_ENV not in os.environ

    @pytest.mark.skipif(os.name == "nt", reason="POSIXの権限設定失敗時だけに適用する契約")
    def test_hook_debug_log_descriptor_closes_when_permission_setting_fails(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """`fchmod`失敗時もfile descriptorを閉じ、Claudeを起動しない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)
        permission_descriptors: list[int] = []
        closed_descriptors: list[int] = []
        real_close = os.close

        def fail_fchmod(descriptor: int, _mode: int) -> None:
            permission_descriptors.append(descriptor)
            raise PermissionError("権限設定失敗")

        def record_close(descriptor: int) -> None:
            if permission_descriptors and descriptor == permission_descriptors[-1]:
                closed_descriptors.append(descriptor)
            real_close(descriptor)

        # 共通起動の掃引も`os.fchmod`で期限判定記録を書くため、掃引を外してhook診断ログの記述子だけを数える。
        setattr_in_managed_temp_modules(
            monkeypatch, "sweep_managed_temp", lambda *, now: _managed_temp.SweepResult([], (), None)
        )
        monkeypatch.setattr(_process_loop.os, "fchmod", fail_fchmod)
        monkeypatch.setattr(_process_loop.os, "close", record_close)

        with pytest.raises(PermissionError, match="権限設定失敗"):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert permission_descriptors == closed_descriptors
        assert len(closed_descriptors) == 1
        with pytest.raises(OSError):
            os.fstat(closed_descriptors[0])
        assert not claude_calls

    def test_removes_inherited_virtual_env(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """起動元ツールの仮想環境を`VIRTUAL_ENV`と`PATH`の双方から取り除いて子セッションへ渡す。

        `uv run`は`VIRTUAL_ENV`の設定と同時にその仮想環境のコマンド格納ディレクトリを`PATH`先頭へ挿入する。
        `VIRTUAL_ENV`だけを除いても`PATH`側が残ると`python`等の解決先が起動元ツールの環境のままになる。
        `PATH`の他要素と`AGENT_TOOLKIT_PROCESS_LOOP_SESSION`が残ることも同時に確認し、過剰除去を防ぐ。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        venv_root = "/home/user/.cache/uv/environments-v2/atk-0123456789abcdef"
        monkeypatch.setenv("VIRTUAL_ENV", venv_root)
        monkeypatch.setenv("PATH", os.pathsep.join((f"{venv_root}/bin", f"{venv_root}/bin", "/usr/local/bin", "/usr/bin")))
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter((1, 0))

        def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
            del private_notes, target_repo
            return next(counts)

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> NoReturn:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_wi_readiness, "count_pending_entries", fake_count_pending_entries)
        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1
        assert "VIRTUAL_ENV" not in claude_calls[0]["env"]
        assert claude_calls[0]["env"]["PATH"] == os.pathsep.join(("/usr/local/bin", "/usr/bin"))
        assert claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ENV] == "1"
        assert (
            claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ID_ENV]
            == claude_calls[0]["cmd"][claude_calls[0]["cmd"].index("--session-id") + 1]
        )

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

    def test_prompt_is_short_goal_with_workflow_boundary(self) -> None:
        """新規セッションの目的文がスキルの完遂だけを伝え、機械生成を示す`atk-auto`要素を持つこと。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001
        assert prompt.startswith("/goal ")
        assert "`agent-toolkit:process-wi`を完遂してください。" in prompt
        assert _automated_prompt.contains(prompt)
        assert "agent-toolkit:exit-session" not in prompt

        forbidden_details = (
            "atk wi list",
            "atk wi show",
            "frontmatter",
            "worker",
            "レビュー",
            "commit",
            "push",
            "CI",
            "atk wi adopt",
            "session-review",
        )
        assert all(detail not in prompt for detail in forbidden_details)

    def test_prompt_references_process_wi(self) -> None:
        """プロンプトが後続工程の集約先としてprocess-wiスキルを参照すること。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001
        assert "agent-toolkit:process-wi" in prompt

    def test_prompt_does_not_inject_publish_destination(self) -> None:
        """対象リポジトリ固有の公開先をprocess-loopが目的文へ注入しない。"""
        prompt = _pl_session.build_process_loop_prompt()  # pylint: disable=protected-access  # noqa: SLF001

        assert "origin/master" not in prompt
        assert "公開先" not in prompt
        assert "git push" not in prompt
        assert "commit" not in prompt
        assert "レビュー" not in prompt

    @pytest.mark.parametrize(
        ("config_value", "expected_model", "expected_effort"),
        [("claude:sonnet/high", "sonnet", "high"), ("claude:sonnet", "sonnet", "medium")],
    )
    def test_configured_model_and_effort_are_passed_to_claude(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        config_value: str,
        expected_model: str,
        expected_effort: str,
    ) -> None:
        """`orchestrate_model`設定のmodelとeffortがClaude起動コマンドへ反映される。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, config_value)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        probe_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, claude_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                probe_calls.append(cmd)
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        count_calls: list[int] = []

        def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
            del private_notes, target_repo
            count_calls.append(len(count_calls))
            return 1 if len(count_calls) == 1 else 0

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_wi_readiness, "count_pending_entries", fake_count_pending_entries)
        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        assert len(probe_calls) == 1
        assert probe_calls[0][2:6] == ["--model", expected_model, "--effort", expected_effort]
        command = claude_calls[0]["cmd"]
        hook_debug_log(command)
        assert command[4:6] + command[8:13] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}',
            "--permission-mode=auto",
            "--model",
            expected_model,
            "--effort",
            expected_effort,
        ]

    def test_candidate_list_probes_in_order_and_starts_first_available_engine(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """異種engineの候補を先頭から実際に試し、最初に成功した候補だけで本作業を起動する。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:sonnet/high,codex:gpt-5.6-sol/low")
        capsys.readouterr()
        probe_prompt = "可用性だけを判定するテスト用プロンプト"
        monkeypatch.setattr(_pl_session, "_AVAILABILITY_PROBE_PROMPT", probe_prompt)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probes: list[list[str]] = []
        sessions: list[list[str]] = []
        probe_envs: list[dict[str, str]] = []
        session_envs: list[dict[str, str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                probes.append(cmd)
                probe_envs.append(cast("dict[str, str]", kwargs["env"]))
                return subprocess.CompletedProcess(cmd, 7, "", "unavailable")
            if cmd[:2] == ["codex", "exec"]:
                probes.append(cmd)
                probe_envs.append(cast("dict[str, str]", kwargs["env"]))
                return subprocess.CompletedProcess(cmd, 0, "OK\n", "")
            if cmd[:1] == ["codex"]:
                sessions.append(cmd)
                session_envs.append(cast("dict[str, str]", kwargs["env"]))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert exc_info.value.code == 0
        assert probes[0][2:6] == ["--model", "sonnet", "--effort", "high"]
        assert probes[1][2:6] == [
            "--model",
            "gpt-5.6-sol",
            "-c",
            "model_reasoning_effort=low",
        ]
        assert probes[1][-1] == probe_prompt
        assert all(env[_DELEGATED_SESSION_ENV] == "1" for env in probe_envs)
        assert len(sessions) == 1
        assert _DELEGATED_SESSION_ENV not in session_envs[0]
        assert sessions[0][1:5] == ["--model", "gpt-5.6-sol", "-c", "model_reasoning_effort=low"]
        captured = capsys.readouterr()
        assert captured.err.count("claude:sonnet/high") == 1
        assert captured.out.count("codex:gpt-5.6-sol/low") == 2

    @pytest.mark.parametrize("limit_type", ["seven_day", "five_hour"])
    def test_claude_usage_limit_waits_and_retries_same_candidate(
        self,
        limit_type: str,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """Weekly limitか5時間の利用上限の拒否では次候補へ進まず、解除まで待って同じClaude候補で本作業を始める。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:opus/high,codex:gpt-5.6-sol/low")
        capsys.readouterr()
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probes: list[list[str]] = []
        sessions: list[list[str]] = []
        sleeps: list[float] = []
        logged: list[tuple[str, dict[str, object]]] = []
        rejected = "\n".join(
            (
                json.dumps({"type": "system", "subtype": "init", "session_id": "probe"}),
                json.dumps(
                    {
                        "type": "rate_limit_event",
                        "rate_limit_info": {"status": "rejected", "rateLimitType": limit_type, "resetsAt": None},
                        "session_id": "probe",
                    }
                ),
                json.dumps({"type": "result", "is_error": True, "result": "You've hit your limit", "session_id": "probe"}),
            )
        )

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                probes.append(cmd)
                if len(probes) <= 2:
                    return subprocess.CompletedProcess(cmd, 1, rejected, "")
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"type": "result", "is_error": False}), "")
            if cmd[:2] == ["codex", "exec"]:
                probes.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "OK\n", "")
            if cmd[:1] == ["claude"]:
                sessions.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop.time, "sleep", sleeps.append)

        def record_append(event: str, **fields: object) -> None:
            logged.append((event, fields))

        monkeypatch.setattr(_process_loop._process_loop_log, "append", record_append)  # pylint: disable=protected-access  # noqa: SLF001
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert exc_info.value.code == 0
        assert [probe[0] for probe in probes] == ["claude", "claude", "claude"]
        assert all(probe[probe.index("--output-format") + 1] == "stream-json" for probe in probes)
        assert sleeps == [_claude_usage_limit.RECHECK_SECONDS, _claude_usage_limit.RECHECK_SECONDS]
        assert len(sessions) == 1
        assert sessions[0][sessions[0].index("--model") + 1] == "opus"
        waits = [fields for event, fields in logged if event == "usage_limit_wait"]
        assert [fields["limit_type"] for fields in waits] == [limit_type, limit_type]
        captured = capsys.readouterr()
        assert captured.out.count(f"Claude Codeの利用上限（{limit_type}）の解除待ち") == 2
        assert "手動での再送や再起動は不要" in captured.out
        assert "モデル候補の可用性判定に失敗しました" not in captured.err

    def test_claude_overage_rejection_falls_back_to_next_candidate(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """待機の対象外（`overage`）の拒否は従来どおり次の候補へ切り替え、待機しない。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:opus/high,codex:gpt-5.6-sol/low")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        sessions: list[list[str]] = []
        sleeps: list[float] = []
        overage = json.dumps(
            {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "overage"}}
        )

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                return subprocess.CompletedProcess(cmd, 1, overage, "")
            if cmd[:2] == ["codex", "exec"]:
                return subprocess.CompletedProcess(cmd, 0, "OK\n", "")
            if cmd[:1] == ["codex"]:
                sessions.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop.time, "sleep", sleeps.append)
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert not sleeps
        assert len(sessions) == 1

    def test_effort_only_difference_changes_probe_and_ignored_claude_effort_falls_back(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """effortだけが異なる候補を区別し、Claudeが値を無視した候補を不成立とする。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:sonnet/ultra,claude:sonnet/high")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probes: list[list[str]] = []
        sessions: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                probes.append(cmd)
                stderr = "Warning: --effort value ultra was ignored" if "ultra" in cmd else ""
                return subprocess.CompletedProcess(cmd, 0, "OK\n", stderr)
            if cmd[:1] == ["claude"]:
                sessions.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert [probe[probe.index("--effort") + 1] for probe in probes] == ["ultra", "high"]
        assert len(sessions) == 1
        assert sessions[0][sessions[0].index("--effort") + 1] == "high"

    def test_codex_probe_uses_returncode_without_interpreting_stderr(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Codex候補は標準エラーの内容ではなく終了コード0だけで可用と判定する。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/medium,claude:sonnet/high")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probes: list[list[str]] = []
        sessions: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["codex", "exec"]:
                probes.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "OK\n", "--effort value was ignored")
            if cmd[:1] == ["codex"]:
                sessions.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert len(probes) == 1
        assert len(sessions) == 1

    def test_abnormal_work_session_does_not_probe_or_start_next_candidate(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """本作業が異常終了しても候補切替を行わず、同じ終了コードで終了する。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:sonnet/high,codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probes: list[list[str]] = []
        sessions: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"] or cmd[:2] == ["codex", "exec"]:
                probes.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "OK\n", "")
            if cmd[:1] == ["claude"]:
                sessions.append(cmd)
                return subprocess.CompletedProcess(cmd, 42, "", "failure")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert exc_info.value.code == 42
        assert len(probes) == 1
        assert len(sessions) == 1

    def test_all_candidate_probes_fail_without_starting_work_session(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """全候補が不成立なら本作業を起動せず、最後の非0終了コードで終了する。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:sonnet/high,codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probes: list[list[str]] = []
        sessions: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                probes.append(cmd)
                return subprocess.CompletedProcess(cmd, 7, "", "failure")
            if cmd[:2] == ["codex", "exec"]:
                probes.append(cmd)
                return subprocess.CompletedProcess(cmd, 9, "", "model gpt-5.6-sol does not support medium effort")
            if cmd[:1] in (["claude"], ["codex"]):
                sessions.append(cmd)
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert exc_info.value.code == 9
        assert len(probes) == 2
        assert not sessions
        diagnostic = capsys.readouterr().err
        assert "codex:gpt-5.6-sol/medium" in diagnostic
        assert "model gpt-5.6-sol does not support medium effort" in diagnostic
        assert "原因: exit code 9; engine診断:" in diagnostic

    def test_unstartable_candidate_falls_back_to_next_engine(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """候補の実行ファイルを起動できない場合も残る候補を判定する。"""
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

        assert selected == ("claude", "sonnet", "high")
        assert [call[0] for call in calls] == ["codex", "claude"]

    def test_all_unstartable_candidates_use_abnormal_exit(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全候補の実行機能を起動できない場合は既存の異常終了へ戻る。"""
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

    def test_each_iteration_restarts_candidate_selection_from_first_candidate(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """次の反復では先頭候補を事前に試すところから候補選択をやり直す。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:sonnet/high,codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        probe_models: list[str] = []
        sessions: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:2] == ["claude", "-p"]:
                probe_models.append(cmd[cmd.index("--model") + 1])
                return subprocess.CompletedProcess(cmd, 0, "OK\n", "")
            if cmd[:1] == ["claude"]:
                sessions.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert probe_models == ["sonnet", "sonnet"]
        assert len(sessions) == 2

    def test_environment_override_controls_process_loop_candidates(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """process-loopも保存値ではなく環境変数の候補列を共通解決関数から取得する。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "claude:sonnet/high")
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", "codex:gpt-5.6-terra/low")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:1] == ["codex"]:
                calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "OK\n" if cmd[:2] == ["codex", "exec"] else "", "")
            return fake_run_with_remote_url(myrepo, [], 0)(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"], home=tmp_path)

        assert len(calls) == 2
        assert all("gpt-5.6-terra" in call for call in calls)

    def test_invalid_saved_orchestrate_model_exits_before_starting_session(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """保存済み設定の書式不正はセッションを起動せずexit 2とする。"""
        _setup_notes(tmp_path)
        config_file = tmp_path / "config" / "config.json"
        config_file.parent.mkdir(parents=True)
        config_file.write_text('{"orchestrate_model": "invalid"}\n', encoding="utf-8")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        session_calls: list[list[str]] = []

        def fail_if_started(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            session_calls.append(cmd)
            raise AssertionError("不正な設定ではセッションを起動しないこと")

        monkeypatch.setattr(subprocess, "run", fail_if_started)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}"], home=tmp_path)

        assert exc_info.value.code == 2
        assert not session_calls
        stderr = capsys.readouterr().err
        assert "現在の設定値: invalid" in stderr
        assert "atk config set orchestrate_model claude:opus[1m]/medium" in stderr

    def test_resume_applied_to_first_session_only(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--resume`指定時は初回だけ再開指定のみで起動する。"""
        claude_calls = _run_two_sessions(tmp_path, monkeypatch, ["--resume"])

        assert len(claude_calls) == 2
        first_command = claude_calls[0]["cmd"]
        second_command = claude_calls[1]["cmd"]
        first_debug_log = hook_debug_log(first_command)
        second_debug_log = hook_debug_log(second_command)
        assert first_debug_log != second_debug_log
        assert first_command[4:] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}',
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
            "--resume",
        ]
        assert _PROCESS_LOOP_SESSION_ID_ENV not in claude_calls[0]["env"]
        assert second_command[4:6] + second_command[8:13] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}',
            "--permission-mode=auto",
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
        ]
        assert "--resume" not in second_command
        assert "--continue" not in second_command
        assert second_command[-1].startswith("/goal ")
        assert claude_calls[1]["env"][_PROCESS_LOOP_SESSION_ID_ENV] == second_command[second_command.index("--session-id") + 1]

    @pytest.mark.parametrize("resume_argv", [["--resume=session-id"], ["--resume", "session-id"]])
    def test_resume_session_id_is_normalized(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        resume_argv: list[str],
    ) -> None:
        """空白区切りと等号区切りのセッションIDをClaudeの等号区切りへ正規化する。"""
        claude_calls = _run_two_sessions(tmp_path, monkeypatch, ["--no-alerts", *resume_argv])

        assert len(claude_calls) == 2
        first_command = claude_calls[0]["cmd"]
        second_command = claude_calls[1]["cmd"]
        assert hook_debug_log(first_command) != hook_debug_log(second_command)
        assert first_command[4:] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}',
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
            "--resume=session-id",
        ]
        assert claude_calls[0]["env"][_PROCESS_LOOP_SESSION_ID_ENV] == "session-id"
        assert second_command[4:6] + second_command[8:13] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}',
            "--permission-mode=auto",
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
        ]
        assert second_command[-1].startswith("/goal ")
        assert claude_calls[1]["env"][_PROCESS_LOOP_SESSION_ID_ENV] == second_command[second_command.index("--session-id") + 1]

    def test_dotfiles_resume_defers_worktree_until_next_session(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """dotfilesの初回再開ではworktree同期を行わず、後続の新規起動時に行う。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "dotfiles"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, claude_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                stdout: Any = (
                    "https://github.com/ak110/dotfiles.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/ak110/dotfiles.git\n"
                )
                return subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="" if kwargs.get("text") else b"")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter([1, 1, 0])
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync_worktree(local_path: pathlib.Path, worktree_name: str) -> pathlib.Path:
            sync_calls.append((local_path, worktree_name))
            return local_path / ".claude" / "worktrees" / worktree_name

        monkeypatch.setattr(_pl_worktree, "_sync_worktree_with_upstream", fake_sync_worktree)

        with pytest.raises(SystemExit):
            atk.main(
                [
                    "wi",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--no-alerts",
                    "--worktree=custom",
                    "--resume",
                ],
                home=tmp_path,
            )

        assert hook_debug_log(claude_calls[0]["cmd"]) != hook_debug_log(claude_calls[1]["cmd"])
        assert claude_calls[0]["cmd"][4:] == [
            "--settings",
            '{"askUserQuestionTimeout": "5m", "dialogExpiry": "5m", "remoteControlAtStartup": false}',
            "--model",
            "opus[1m]",
            "--effort",
            "medium",
            "--resume",
        ]
        assert "--worktree=process-loop" not in claude_calls[1]["cmd"]
        assert claude_calls[1]["cwd"] == myrepo / ".claude" / "worktrees" / "custom"
        assert sync_calls == [(myrepo, "custom")]

    def test_resume_absent_without_option(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--resume`未指定時はClaudeセッションを継続しない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter([1, 0])
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1
        hook_debug_log(claude_calls[0]["cmd"])
        assert "--continue" not in claude_calls[0]["cmd"]
        assert "--resume" not in claude_calls[0]["cmd"]

    def test_auto_resume_and_resume_are_mutually_exclusive(self, tmp_path: pathlib.Path) -> None:
        """`--auto-resume`と`--resume`は同時指定できない。"""
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", "--resume", "--auto-resume"], home=tmp_path)

        assert exc_info.value.code == 2

    def test_auto_resume_selects_session_and_resumes_first_session_only(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--auto-resume`は自動選択したsession_idで初回だけ再開し、2回目以降は新規起動する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter([1, 1, 0])
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        select_calls: list[tuple[str, pathlib.Path]] = []

        def fake_select_session(target_repo_id: str, target_repo_path: pathlib.Path) -> str:
            select_calls.append((target_repo_id, target_repo_path))
            return "auto-selected-id"

        monkeypatch.setattr(_process_loop._auto_resume, "select_session", fake_select_session)  # pylint: disable=protected-access

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--auto-resume"],
                home=tmp_path,
            )

        assert len(select_calls) == 1
        assert len(claude_calls) == 2
        first_command = claude_calls[0]["cmd"]
        second_command = claude_calls[1]["cmd"]
        assert first_command[-1] == "--resume=auto-selected-id"
        assert "--auto-resume" not in second_command
        assert "--resume" not in second_command
        assert not any(arg.startswith("--resume") for arg in second_command)

    def test_auto_resume_absent_without_option_does_not_call_select_session(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`--auto-resume`未指定時は自動選択処理を呼び出さない。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter([1, 0])
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fail_if_called(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("--auto-resume未指定では呼び出さないこと")

        monkeypatch.setattr(_process_loop._auto_resume, "select_session", fail_if_called)  # pylint: disable=protected-access

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> None:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert len(claude_calls) == 1

    @pytest.mark.parametrize(
        ("config_value", "expected_model", "expected_effort"),
        [
            ("codex:gpt-5.6-sol/medium", "gpt-5.6-sol", "medium"),
            ("codex:gpt-5.6-sol/high", "gpt-5.6-sol", "high"),
            ("codex:sol/medium", "gpt-6-sol", "medium"),
        ],
    )
    def test_codex_new_session_uses_interactive_cli(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        config_value: str,
        expected_model: str,
        expected_effort: str,
    ) -> None:
        """Codex新規起動は設定値を対話CLIへ渡し、標準入出力を捕捉しない。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, config_value)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, codex_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--no-alerts",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(codex_calls) == 1
        call = codex_calls[0]
        command = call["cmd"]
        assert command[0] == "codex"
        assert "--approve-for-me" not in command
        assert command[1:5] == ["--model", expected_model, "-c", f"model_reasoning_effort={expected_effort}"]
        assert command[-1].startswith("/goal ")
        assert command[-1].count("/goal ") == 1
        assert "exec" not in command
        assert "--debug=hooks" not in command
        assert "--autocompact" not in command
        assert call["cwd"] == myrepo
        assert all(key not in call["kwargs"] for key in ("stdin", "stdout", "stderr", "capture_output"))
        expected_flags = 0x00000200 if os.name == "nt" else 0
        assert call["kwargs"]["creationflags"] == expected_flags

    @pytest.mark.parametrize(
        ("resume_argv", "expected_tail"),
        [
            (["--resume"], []),
            (["--resume", "session-id"], ["session-id"]),
        ],
    )
    def test_codex_resume_uses_interactive_cli_without_prompt(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
        resume_argv: list[str],
        expected_tail: list[str],
    ) -> None:
        """Codex再開は対話の選択画面またはIDを使い、新しい目的文を渡さない。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/high")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, codex_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit):
            atk.main(
                [
                    "wi",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-update",
                    "--no-alerts",
                    *resume_argv,
                ],
                home=tmp_path,
            )

        assert len(codex_calls) == 1
        command = codex_calls[0]["cmd"]
        assert command == [
            "codex",
            "resume",
            "--model",
            "gpt-5.6-sol",
            "-c",
            "model_reasoning_effort=high",
            *expected_tail,
        ]
        assert all(not arg.startswith("/goal ") for arg in command)
        assert "exec" not in command


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

    @pytest.mark.parametrize("returncode", [0, -15, 15, 143])
    def test_normal_returncode_continues_loop(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        returncode: int,
    ) -> None:
        """`returncode`が`0`・`-15`・`15`・`143`のいずれかなら反復継続し、
        次の待機で`KeyboardInterrupt`が送出されると正常終了すること。
        """
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, returncode))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1 if len(claude_calls) == 0 else 0)

        def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
            del private_notes, target_repo_id
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 0
        assert len(claude_calls) == 1

    def test_abnormal_returncode_exits_with_same_code(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`returncode`が正常集合外なら、CLI自体が同じexit codeで終了すること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []

        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 42))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)

        def fake_wait_for_changes(*_a: object, **_kw: object) -> None:
            raise AssertionError("異常終了時は_wait_for_changesを呼ばないこと")

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

        assert exc_info.value.code == 42
        captured = capsys.readouterr()
        assert "claudeがexit code 42で異常終了しました" in captured.err
        # 原因の確認先と候補の変更手段を次の操作として続ける。
        next_action = captured.err.split("\n次の操作: ", 1)[1]
        assert "セッション記録" in next_action
        assert "atk config set orchestrate_model" in next_action

    def test_codex_rejects_sigterm_style_returncodes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """POSIXのCodexではシェル経由のSIGTERM終了コードを正常扱いしない。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, codex_calls, 15))
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert exc_info.value.code == 15
        assert "codexがexit code 15で異常終了しました" in capsys.readouterr().err

    def test_codex_accepts_posix_sigterm_returncode(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """POSIXのCodexでは直接受信したSIGTERMの終了コードを正常扱いする。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        codex_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, codex_calls, -15))
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def fake_wait_for_changes(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert len(codex_calls) == 1


class TestConsoleTitleReset:
    """常駐区間のコンソールタイトル制御を検証する（再起動先は`TestProcessLoopUpdateAndRestart`の既存argv検証が担う）。"""

    def test_helper_functions_reset_title_after_each_run(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """`_sync_worktree_with_upstream`・`_check_and_restart_on_update`配下の各subprocess.run直後にタイトルを再設定すること。"""
        local_path = tmp_path / "repo"
        (local_path / ".claude" / "worktrees" / "process-loop").mkdir(parents=True)
        calls: list[str] = []
        monkeypatch.setattr(_process_loop._console_title, "set_console_title", calls.append)  # pylint: disable=protected-access  # noqa: SLF001

        def fake_run(cmd: list[str], *_a: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "check-ignore", "-q", ".claude/worktrees/"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd == ["git", "check-ref-format", "--branch", "worktree-process-loop"]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                return subprocess.CompletedProcess(cmd, 0, str(local_path / ".git"), "")
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                return subprocess.CompletedProcess(cmd, 0, str(local_path / ".claude" / "worktrees" / "process-loop"), "")
            if cmd == ["git", "symbolic-ref", "--short", "HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "worktree-process-loop", "")
            if cmd == ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
                return subprocess.CompletedProcess(cmd, 0, "origin/master\n", "")
            if cmd == ["git", "remote"]:
                return subprocess.CompletedProcess(cmd, 0, "origin\n", "")
            stdout = "1\n" if "rev-list" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_pl_update, "code_hash", lambda _d: "same-hash")  # 再起動へ進ませない
        _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001
        _pl_update.check_and_restart_on_update(tmp_path, "same-hash", ["argv0"])  # pylint: disable=protected-access  # noqa: SLF001
        assert calls
        assert all(call == "atk wi process-loop" for call in calls)

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

    def test_console_is_reset_between_child_session_and_title_reset(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """子セッション終了後、タイトル再設定より前にコンソールを初期化する。"""
        calls: list[str] = []

        def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append("session")
            return subprocess.CompletedProcess(command, 0)

        monkeypatch.setattr(_pl_session, "_build_session_argv", lambda *_a, **_kw: (["claude"], None))
        monkeypatch.setattr(_process_loop._process_loop_log, "append", lambda *_a, **_kw: None)  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(_pl_env, "session_env", lambda env, _orchestrator: env)
        monkeypatch.setattr(_pl_env, "session_creation_flags", lambda _orchestrator: 0)
        monkeypatch.setattr(_pl_env, "reset_console", lambda: calls.append("reset"))
        monkeypatch.setattr(_process_loop._console_title, "set_console_title", lambda _title: calls.append("title"))  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(subprocess, "run", fake_run)

        result = _pl_session.run_process_session(  # pylint: disable=protected-access  # noqa: SLF001
            argparse.Namespace(no_update=True),
            tmp_path,
            "prompt",
            {},
            orchestrator="claude",
            model="model",
            effort="effort",
            resume_pending=False,
            dotfiles_root=None,
        )

        assert result is False
        assert calls == ["session", "reset", "title"]
