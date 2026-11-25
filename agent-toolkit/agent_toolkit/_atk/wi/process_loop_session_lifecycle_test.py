# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。

import argparse
import contextlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

import psutil
import pytest

from agent_toolkit._atk import claude_connection
from agent_toolkit._atk.wi import process_loop_compaction, process_loop_control, process_loop_log
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._common import (
    claude_usage_limit,
    codex_usage_limit,
    next_action,
    process_tree,
    session_record_paths,
    session_registry,
    usage_observation,
)
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.process_loop_session_support import _ORIGINAL_CODEX_SESSION
from agent_toolkit._testing.process_loop_session_support import _isolate_process_loop_commands as _isolate_process_loop_commands


@pytest.mark.parametrize("hold", [False, True])
def test_process_loop_restarts_limited_claude_after_owned_exit(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    host_environ: Callable[[], dict[str, str]],
    hold: bool,
) -> None:
    api = tmp_path / "payg.env"
    api.write_text("ANTHROPIC_API_KEY=test-key\nANTHROPIC_BASE_URL=https://payg.example.test\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_PAYG_ENV_FILE", str(api))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_ORCHESTRATOR_CONNECTIONS", "subscription,payg")
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "from agent_toolkit._hooks import stopfailure_notifier\n"
        "from agent_toolkit._common import session_registry\n"
        "root = pathlib.Path(os.environ['SWITCH_ROOT'])\n"
        "subscription = bool(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN'))\n"
        "if '--no-session-persistence' in sys.argv:\n"
        "    if subscription and (root / 'limited').exists():\n"
        "        print(json.dumps({'type':'rate_limit_event','rate_limit_info':{\n"
        "            'status':'rejected','rateLimitType':'five_hour','resetsAt':int(time.time())+3600}}))\n"
        "        sys.exit(1)\n"
        "    sys.exit(0)\n"
        "records = pathlib.Path(os.environ['CLAUDE_CONFIG_DIR']) / 'projects/test'\n"
        "records.mkdir(parents=True, exist_ok=True)\n"
        "if '-p' in sys.argv and '--resume' in sys.argv:\n"
        "    identifier = sys.argv[sys.argv.index('--resume')+1]\n"
        "    assert sys.argv[-1] == '/compact' and pathlib.Path.cwd() == root\n"
        "    assert not any(key in os.environ for key in ('AGENT_TOOLKIT_PROCESS_LOOP_SESSION',\n"
        "        'AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID', 'AGENT_TOOLKIT_SESSION_START_INPUT',\n"
        "        'AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM'))\n"
        "    assert session_registry.resolve(identifier).state is session_registry.Resolution.TERMINAL\n"
        "    with (records / f'{identifier}.jsonl').open('a', encoding='utf-8') as f:\n"
        "        f.write(json.dumps({'type':'system','subtype':'compact_boundary'})+'\\n')\n"
        "    (root / 'compacted').write_text(identifier)\n"
        "    sys.exit(0)\n"
        "identifier = next((arg.split('=',1)[1] for arg in sys.argv if arg.startswith('--resume=')), None)\n"
        "if identifier is None: identifier = sys.argv[sys.argv.index('--session-id')+1]\n"
        "identity = session_registry.ProcessIdentity.of(os.getpid())\n"
        "assert identity is not None\n"
        "with (root / 'calls').open('a', encoding='utf-8') as log:\n"
        "    log.write(json.dumps({'id':identifier,'subscription':subscription,\n"
        "        'process':session_registry.process_payload(identity)})+'\\n')\n"
        "if subscription:\n"
        "    (root / 'limited').write_text('limited')\n"
        "    stopfailure_notifier.main(json.dumps({'session_id':identifier,'error':'rate_limit'}))\n"
        "    if os.environ['HOLD_LIMIT'] == '1':\n"
        "        deadline = time.monotonic() + 120\n"
        "        while not (root / 'limited.release').exists():\n"
        "            assert time.monotonic() < deadline\n"
        "            time.sleep(0.01)\n"
        "    sys.exit(1)\n"
        "assert (root / 'compacted').read_text() == identifier\n"
        "(root / 'continued').write_text(identifier)\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **host_environ(),
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "SWITCH_ROOT": str(tmp_path),
        "CLAUDE_CONFIG_DIR": str(tmp_path / "claude-home"),
        "HOLD_LIMIT": str(int(hold)),
        _pl_env.PROCESS_LOOP_SESSION_ENV: "1",
    }
    finished: list[bool] = []
    assert _pl_session.run_process_session(
        argparse.Namespace(resume=None),
        tmp_path,
        "元の仕事",
        env,
        orchestrator="claude",
        model="sonnet",
        effort="high",
        resume_pending=False,
        on_session_finished=finished.append,
    )
    calls = [json.loads(line) for line in (tmp_path / "calls").read_text(encoding="utf-8").splitlines()]
    assert len(calls) == 2
    assert [call["subscription"] for call in calls] == [True, False]
    assert calls[0]["id"] == calls[1]["id"] == (tmp_path / "continued").read_text(encoding="utf-8")
    assert not session_registry.ProcessIdentity(**calls[0]["process"]).exists()
    assert finished == [True]
    info = session_registry.resolve(calls[1]["id"]).resume_info
    assert info is not None and info.claude_connection == "payg" and info.turn_seq == 2


@pytest.mark.skipif(os.name == "nt", reason="合成Claude CLIをPOSIXのshebangで起動する")
@pytest.mark.parametrize(
    "case", ["success", "nonzero", "timeout", "no-boundary", "unreadable", "native", "picker", "cancelled"]
)
def test_claude_resume_launch_preserves_reservation_on_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    identifier = "saved-session"
    home = tmp_path / "claude-home"
    native = home / "sessions"
    native.mkdir(parents=True)
    record = home / "projects" / "cwd" / f"{identifier}.jsonl"
    record.parent.mkdir(parents=True)
    record.write_text('{"type":"system","subtype":"compact_boundary"}\n', encoding="utf-8")
    binary = tmp_path / "claude"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys, time\n"
        "root = pathlib.Path.cwd()\n"
        "case = os.environ['COMPACTION_CASE']\n"
        "if '--resume' in sys.argv:\n"
        "    (root / 'compaction-called').touch()\n"
        "    assert '-p' in sys.argv and sys.argv[-1] == '/compact'\n"
        "    assert '--settings' in sys.argv and os.environ.get('AGENT_TOOLKIT_PROCESS_LOOP_SESSION') is None\n"
        "    if case == 'nonzero': sys.exit(14)\n"
        "    if case == 'timeout': time.sleep(30)\n"
        "    if case == 'success':\n"
        "        with (root / 'claude-home/projects/cwd/saved-session.jsonl').open('a', encoding='utf-8') as f:\n"
        "            f.write(json.dumps({'type':'system','subtype':'compact_boundary'})+'\\n')\n"
        "            message = {'content':'compact-conversation 圧縮前のやり取りと要約は現在の文脈にない'}\n"
        "            f.write(json.dumps({'type':'user','message':message})+'\\n')\n"
        "else:\n"
        "    assert '--resume=saved-session' in sys.argv and '追加指示' in sys.argv[-1]\n"
        "    (root / 'interactive-called').touch()\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    if case == "timeout":
        monkeypatch.setattr(process_loop_compaction, "TIMEOUT", 3)
    if case == "unreadable":
        registry = session_registry.registry_directory()
        registry.mkdir(parents=True, exist_ok=True)
        (registry / f"{identifier}.json").write_text("{", encoding="utf-8")
    if case == "native":
        pid = os.getpid()
        stat_path = pathlib.Path(f"/proc/{pid}/stat")
        start = stat_path.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[19] if stat_path.exists() else None
        (native / f"{pid}.json").write_text(
            json.dumps({"pid": pid, "sessionId": identifier, "procStart": start}), encoding="utf-8"
        )
    monkeypatch.setenv(_pl_env.PROCESS_LOOP_SESSION_ENV, "1")
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CLAUDE_CONFIG_DIR": str(home),
        "COMPACTION_CASE": case,
    }
    process_loop_log.append("session_end", session_id=identifier)
    resume = None if case == "picker" else identifier
    process_loop_control.cmd_process_loop_resume(resume)
    request = process_loop_control.read_resume_request()
    assert request is not None
    process_loop_control.cmd_process_loop_instruct("保持する指示")
    if case == "cancelled":
        process_loop_control.cmd_process_loop_resume_cancel()

    def launch() -> bool:
        return _pl_session.run_process_session(
            argparse.Namespace(resume=resume, resume_request_id=request.request_id),
            tmp_path,
            "再開",
            env,
            orchestrator="claude",
            model="sonnet",
            effort="high",
            resume_pending=True,
            on_session_finished=lambda _normal: None,
            claude_connection=claude_connection.Connection("inherit"),
        )

    if case == "success":
        assert launch()
        assert process_loop_control.read_resume_request() is None
        assert process_loop_log.read_instructions() == []
        assert "mode=original-record" in process_loop_log.log_path().read_text(encoding="utf-8")
    else:
        if case == "cancelled":
            with pytest.raises(process_loop_control.ResumeChanged):
                launch()
            assert process_loop_control.read_resume_request() is None
        else:
            with pytest.raises(next_action.ActionableError) as caught:
                launch()
            assert identifier in str(caught.value)
            if case == "nonzero":
                assert "/compact の終了コード: 14" in str(caught.value)
            if case == "timeout":
                assert "TimeoutExpired（上限 3 秒）" in str(caught.value)
            assert process_loop_control.read_resume_request() == request
        assert process_loop_log.read_instructions() == ["保持する指示"]
    assert (tmp_path / "interactive-called").exists() == (case == "success")
    assert (tmp_path / "compaction-called").exists() == (case in {"success", "nonzero", "timeout", "no-boundary"})


@pytest.mark.parametrize("new_turn", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_usage_limit_child_continues_next_iteration(
    tmp_path: pathlib.Path,
    new_turn: bool,
    resume: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binary = tmp_path / "codex"
    marker = tmp_path / "completed"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import datetime, json, os, pathlib, time\n"
        "directory = pathlib.Path(os.environ['CODEX_HOME']) / 'sessions/2026/10/16'\n"
        "directory.mkdir(parents=True, exist_ok=True)\n"
        "stamp = datetime.datetime.now(datetime.UTC).isoformat()\n"
        "events = [\n"
        " {'type': 'session_meta', 'payload': {'id': 'child', 'cwd': os.getcwd(),\n"
        " 'timestamp': '2026-01-01T00:00:00Z' if os.environ['RESUME'] == '1' else stamp,\n"
        " 'originator': 'codex-tui', 'source': 'cli'}},\n"
        " {'type': 'event_msg', 'timestamp': stamp, 'payload': {'type': 'task_started'}},\n"
        " {'type': 'event_msg', 'payload': {'type': 'task_complete', 'error': {'codex_error_info': 'usage_limit_exceeded'}}},\n"
        "]\n"
        "if os.environ['NEW_TURN'] == '1': events.append({'type': 'event_msg', 'payload': {'type': 'task_started'}})\n"
        "record = directory / 'rollout-2026-10-16-00000000-0000-0000-0000-000000000001.jsonl'\n"
        "record.write_text('\\n'.join(json.dumps(e) for e in events))\n"
        "release = pathlib.Path(os.environ['COMPLETED'] + '.release')\n"
        "release.with_suffix('.ready').touch()\n"
        "deadline = time.monotonic() + 120\n"
        "while not release.exists():\n"
        "    assert time.monotonic() < deadline\n"
        "    time.sleep(0.01)\n"
        "pathlib.Path(os.environ['COMPLETED']).write_text('completed')\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "CODEX_HOME": str(tmp_path / "codex-home"),
        "NEW_TURN": "1" if new_turn else "0",
        "RESUME": "1" if resume else "0",
        "COMPLETED": str(marker),
    }
    from_rollout = _pl_session._codex_usage_limit.from_rollout  # pylint: disable=protected-access
    release = marker.with_name(marker.name + ".release")

    def observed_rollout(*args: Any, **kwargs: Any) -> Any:
        result = from_rollout(*args, **kwargs)
        if new_turn:
            assert result is None
            release.touch()
        return result

    class ReadyPopen(subprocess.Popen):
        def wait(self, timeout=None):
            if timeout == 1:
                deadline = time.monotonic() + 30
                while not release.with_suffix(".ready").exists():
                    assert self.poll() is None and time.monotonic() < deadline
                    time.sleep(0.01)
                timeout = 0.05
            return super().wait(timeout=timeout)

    monkeypatch.setattr(_pl_session._codex_usage_limit, "from_rollout", observed_rollout)  # pylint: disable=protected-access
    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    finished: list[bool] = []
    started = time.monotonic()
    assert (
        _pl_session.run_process_session(
            argparse.Namespace(no_update=True, resume="00000000-0000-0000-0000-000000000001" if resume else None),
            tmp_path,
            "goal",
            env,
            orchestrator="codex",
            model="test",
            effort="high",
            resume_pending=resume,
            on_session_finished=finished.append,
        )
        is True
    )
    assert finished == [True]
    assert marker.exists() is new_turn
    assert time.monotonic() - started < 15


@pytest.mark.parametrize("parent_exits", [False, True])
@pytest.mark.parametrize("residual", [False, True])
def test_cli_exception_reclaims_known_descendants(tmp_path, monkeypatch, host_environ, parent_exits, residual):
    """CLIの本体例外は既知子孫の回収後に伝播し、回収不能なら再試行できる。"""
    release = tmp_path / "child.release"
    child_source = late_descendant.held_source(release)
    source = f"import subprocess,sys; subprocess.Popen([sys.executable,'-c',{child_source!r}]); " + late_descendant.held_source(
        tmp_path / "parent.release"
    )
    processes = []
    cohort = []

    class ReadyPopen(subprocess.Popen):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            processes.append(self)
            deadline = time.monotonic() + 30
            while not release.with_suffix(".ready").exists():
                assert self.poll() is None and time.monotonic() < deadline
                time.sleep(0.01)
            cohort.extend(process_tree.collect_descendants(self.pid, include_root=True))

    def started(_identity):
        assert any(target.pid != processes[0].pid for target in cohort)
        if parent_exits:
            processes[0].kill()
            processes[0].wait(timeout=10)
        raise ValueError("本体失敗")

    def leave_owned(process, **kwargs):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        return process_tree.ReclaimError(["owned child"], cohort, context=kwargs["context"])

    monkeypatch.setattr(subprocess, "Popen", ReadyPopen)
    if residual:
        monkeypatch.setattr(process_tree, "stop_owned_process", leave_owned)
    received: list[process_tree.ReclaimError] = []
    try:
        with pytest.raises(process_tree.ReclaimError if residual else ValueError) as raised:
            _ORIGINAL_CODEX_SESSION(
                [sys.executable, "-c", source],
                host_environ(),
                tmp_path,
                orchestrator="claude",
                on_process_started=started,
                on_residual=received.append,
            )
        if residual:
            assert isinstance(raised.value, process_tree.ReclaimError)
            assert received == [raised.value]
            assert not raised.value.retry()
        assert not process_tree.wait_until_released(cohort, timeout=1)
    finally:
        assert not process_tree.stop_tree(None, known=cohort, timeout=1).residual
        for process in processes:
            process.wait(timeout=10)


@pytest.mark.parametrize("site", ["stalled", "codex-usage-limit", "exception", "claude-usage-limit"])
def test_unreclaimed_descendants_reach_owner_from_every_stop_site(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, site: str
) -> None:
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    survivor = owned.enter_context(late_descendant.held_process(tmp_path))

    def reap() -> None:
        if survivor.poll() is None:
            survivor.kill()
        survivor.wait()

    request.addfinalizer(reap)
    stopped: list[str] = []

    def stop_owned_process(process: subprocess.Popen[Any], *, context: str, **_kwargs: Any) -> Any:
        # 停止の結果に、統括CLIの子孫が残った状態を注入する。統括CLI自身は実際に止める。
        process.kill()
        process.wait()
        stopped.append(context)
        return process_tree.ReclaimError([f"{survivor.pid}: sleep"], [psutil.Process(survivor.pid)], context=context)

    monkeypatch.setattr(_pl_session.process_tree, "stop_owned_process", stop_owned_process)
    env = {**os.environ, "CODEX_HOME": str(tmp_path / "codex"), "CLAUDE_CONFIG_DIR": str(tmp_path / "claude")}
    env.pop(_pl_env.PROCESS_LOOP_SESSION_ID_ENV, None)
    orchestrator = "codex" if site == "codex-usage-limit" else "claude"
    if site == "stalled":
        started = time.time()
        clock = iter([started, started + 9601])
        monkeypatch.setattr(_pl_session.time, "time", lambda: next(clock, started + 9601))
    elif site == "codex-usage-limit":
        rollout = tmp_path / "x.jsonl"
        monkeypatch.setattr(codex_usage_limit, "find_session_rollout", lambda *_args, **_kwargs: rollout)
        monkeypatch.setattr(codex_usage_limit, "from_rollout", lambda _path: object())
        monkeypatch.setattr(session_record_paths, "codex_session_id", lambda _path: "codex-session")
    elif site == "claude-usage-limit":
        env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = "limited-session"
        request_file = tmp_path / "claude/agent-toolkit-function-hooks/rate-limit-limited-session.txt"
        request_file.parent.mkdir(parents=True)
        request_file.write_text("requested", encoding="utf-8")
        monkeypatch.setattr(_pl_session, "_attach_orchestrator_child", lambda *_args: None)

    def started_process(_identity: Any) -> None:
        if site == "exception":
            raise RuntimeError("統括の起動処理の失敗")

    received: list[process_tree.ReclaimError] = []
    with pytest.raises(process_tree.ReclaimError):
        _ORIGINAL_CODEX_SESSION(
            [sys.executable, "-c", late_descendant.held_source(tmp_path / "residual.release")],
            env,
            tmp_path,
            orchestrator=orchestrator,
            on_process_started=started_process,
            on_residual=received.append,
        )
    expected = {
        "stalled": "stalled",
        "codex-usage-limit": "usage-limit",
        "exception": "exception",
        "claude-usage-limit": "usage-limit switch",
    }
    assert stopped == [f"process-loop {expected[site]}"]
    assert all([process.pid for process in failure.processes] == [survivor.pid] for failure in received)
    assert len(received) == 1
    monkeypatch.undo()


@pytest.mark.parametrize(
    ("window_id", "policy", "reactivated", "stop"),
    [
        ("weekly_all", "five-hour", False, True),
        ("weekly_scoped", "five-hour", False, False),
        ("five_hour", "five-hour", False, False),
        ("weekly_all", "always", False, False),
        ("weekly_all", "five-hour", True, False),
    ],
)
def test_idle_claude_cli_stops_only_for_unwaited_weekly_limit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, window_id: str, policy: str, reactivated: bool, stop: bool
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", policy)
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path), _pl_env.PROCESS_LOOP_SESSION_ID_ENV: "weekly-limit"}
    monkeypatch.setattr(_pl_session, "_attach_orchestrator_child", lambda *_args: None)
    monkeypatch.setattr(claude_usage_limit, "RECHECK_SECONDS", 0)
    connection = claude_connection.Connection("subscription")
    resets_at = int(time.time()) + 3600
    calls: list[claude_connection.Connection] = []

    path = tmp_path / "active.jsonl"
    if reactivated:
        path.write_text("started", encoding="utf-8")
        os.utime(path, (1, 1))
        monkeypatch.setattr(_pl_session, "_last_record_update", lambda *_: path.stat().st_mtime)

    def refresh(selected, _env):
        if reactivated:
            path.write_text("updated", encoding="utf-8")
        calls.append(selected)
        return [usage_observation.QuotaWindow("claude", "test", window_id, 100, resets_at, time.time())]

    monkeypatch.setattr(_pl_session.usage_refresh, "refresh_claude", refresh)

    def run():
        return _ORIGINAL_CODEX_SESSION(
            [sys.executable, "-c", "import time; time.sleep(1.5)"],
            env,
            tmp_path,
            orchestrator="claude",
            claude_connection=connection,
        )

    if stop:
        with pytest.raises(_pl_session.ClaudeUsageLimit) as caught:
            run()
        assert caught.value.limit == claude_usage_limit.UsageLimitState("rejected", "seven_day", resets_at)
    else:
        assert run().returncode == 0
    assert calls == ([] if policy == "always" else [connection])


def test_idle_claude_cli_stall_stops_while_usage_refresh_hangs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 実例: 上限の照会が戻らず無出力の監視ごと止まり、終了しない統括が5時間残った。  pyfltr: allow-comment
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_USAGE_LIMIT_WAIT", "five-hour")
    env = {**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path), _pl_env.PROCESS_LOOP_SESSION_ID_ENV: "hung-refresh"}
    monkeypatch.setattr(_pl_session, "_attach_orchestrator_child", lambda *_args: None)
    monkeypatch.setattr(claude_usage_limit, "RECHECK_SECONDS", 0)
    monkeypatch.setattr(_pl_session, "_last_record_update", lambda *_: 1.0)
    monkeypatch.setattr(_pl_session._process_loop_log, "append", lambda *_args, **_kwargs: None)  # pylint: disable=protected-access
    release = threading.Event()
    started: list[float] = []

    def hung_refresh(_selected, _env):
        started.append(time.monotonic())
        release.wait(30)
        return []

    monkeypatch.setattr(_pl_session.usage_refresh, "refresh_claude", hung_refresh)
    began = time.monotonic()
    try:
        result = _ORIGINAL_CODEX_SESSION(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env,
            tmp_path,
            orchestrator="claude",
            claude_connection=claude_connection.Connection("subscription"),
        )
    finally:
        release.set()
    assert started, "上限の照会を起動していない"
    assert result.returncode == 1
    assert time.monotonic() - began < 15, "上限の照会が戻るまで無出力の監視が止まった"
