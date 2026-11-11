# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""統括の利用上限からの継続と実子プロセスの回収を検証する。"""

import argparse
import contextlib
import json
import os
import pathlib
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any

import psutil
import pytest

from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._common import codex_usage_limit, next_action, process_tree, session_record_paths, session_registry
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
    """StopFailureの公開要求から旧CLIを回収し、同じIDの実CLIで仕事を続行する。"""
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
        "(root / 'continued').write_text(identifier)\n",
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    env = {
        **host_environ(),
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "SWITCH_ROOT": str(tmp_path),
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


@pytest.mark.parametrize("new_turn", [False, True])
@pytest.mark.parametrize("resume", [False, True])
def test_codex_usage_limit_child_continues_next_iteration(
    tmp_path: pathlib.Path,
    new_turn: bool,
    resume: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実子の上限停止だけを親が終了し、新turn開始後の子は自身の正常終了まで保持する。"""
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


@pytest.mark.parametrize("site", ["stalled", "codex-usage-limit", "exception", "claude-usage-limit"])
def test_unreclaimed_descendants_reach_owner_from_every_stop_site(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, site: str
) -> None:
    """統括CLIを止める4箇所のいずれでも、回収できなかった子孫を診断ログだけで済ませず呼び出し元へ渡す。"""
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    survivor = owned.enter_context(late_descendant.held_process(tmp_path))

    def reap() -> None:
        survivor.kill()
        survivor.wait()

    request.addfinalizer(reap)
    stopped: list[str] = []

    def stop_owned_process(process: subprocess.Popen[Any], *, context: str) -> Any:
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
    expected: type[BaseException] | None = {
        "exception": RuntimeError,
        "claude-usage-limit": next_action.ActionableError,
    }.get(site)
    with contextlib.ExitStack() as stack:
        if expected is not None:
            stack.enter_context(pytest.raises(expected))
        _ORIGINAL_CODEX_SESSION(
            [sys.executable, "-c", late_descendant.held_source(tmp_path / "residual.release")],
            env,
            tmp_path,
            orchestrator=orchestrator,
            on_process_started=started_process,
            on_residual=received.append,
        )
    assert len(stopped) == 1
    assert [process.pid for failure in received for process in failure.processes] == [survivor.pid]
