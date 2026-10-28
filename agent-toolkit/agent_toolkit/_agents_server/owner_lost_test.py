"""所有サーバーを失った実行中のsessionを、MCPツールの関数とCLIの公開コマンドから各処理へ通して検証する受入テスト。

委譲元のagents_serverが別のルートの新しいプロセスへ替わった後も、旧サーバーの下で委譲先CLIが作業を続ける状況を再現する。
記録プロセス（所有サーバーと委譲先CLI）は実プロセスで表し、旧サーバーの状態ファイルは生存の印が失効した形で残す。
Codexの記録には、専用App Serverの`thread/read`が元turnを終了と返す偽backendを組み合わせる。
"""

from __future__ import annotations

# 受入テストはMCPツールの関数と共有状態のファイルを直接確かめる。
# pylint: disable=protected-access
import contextlib
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time
from collections.abc import Generator
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import (
    agents_wait,
    mcp_tools,
    owner_lost,
    resume_waits,
    shared_layout,
    shared_roots,
    shared_sweep,
    state,
    status_file,
    wait_targets,
)
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    ThreadReadBackend,
    _install_backend,
    _show_through_tool,
    exited_process_identity,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")

_SESSION_ID = "019a2c41-5b6e-7d10-8f21-3c4d5e6f7a81"
_OLD_ROOT = "mcp-1831ca1999764addb43ebee00782864b"
_NEW_ROOT = "mcp-36ddd3de15894595bd160c24210f5f0e"


@contextlib.contextmanager
def _live_process() -> Generator[tuple[subprocess.Popen[bytes], session_registry.ProcessIdentity]]:
    """テストの間だけ生存する実プロセスと、その識別を返す。"""
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) as process:
        try:
            identity = session_registry.ProcessIdentity.of(process.pid)
            assert identity is not None
            yield process, identity
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()


def _publish_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    engine: str,
    processes: tuple[session_registry.ProcessIdentity, ...],
) -> pathlib.Path:
    """旧サーバーが所有したまま終了を公開していない`running`の記録を保存し、そのパスを返す。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        _SESSION_ID,
        terminal=False,
        engine=engine,
        cwd=str(tmp_path),
        turn_seq=1,
        status="running",
        turn_id="turn-1" if engine == "codex" else None,
        launch_info=session_registry.LaunchInfo(label="公開担当", prompt="公開工程を実施せよ"),
        processes=processes,
        owner_root_session_id=_OLD_ROOT,
    )
    return session_registry.registry_directory() / f"{_SESSION_ID}.json"


def _write_old_root(tmp_path: pathlib.Path, *bodies: str) -> pathlib.Path:
    """旧サーバーのルートへ、生存の印が失効した状態ファイルと委譲先の上り通知を置く。"""
    directory = shared_layout.status_directory(_OLD_ROOT, tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    expired = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=1)).isoformat()
    (directory / "root.json").write_text(
        json.dumps(
            {
                "version": 1,
                "host_session_id": None,
                "heartbeat_at": expired,
                "updated_at": expired,
                "sessions": [{"session_id": _SESSION_ID, "status": "running"}],
            }
        ),
        encoding="utf-8",
    )
    _add_old_notices(tmp_path, *bodies)
    return directory


def _add_old_notices(tmp_path: pathlib.Path, *bodies: str) -> None:
    notices = shared_layout.notices_directory(_OLD_ROOT, tmp_path)
    notices.mkdir(parents=True, exist_ok=True)
    for index, body in enumerate(bodies):
        sent_at = datetime.datetime.now(datetime.UTC) + datetime.timedelta(microseconds=index)
        (notices / f"{_SESSION_ID}.{sent_at.strftime('%Y%m%dT%H%M%S%f')}.{index:016x}.json").write_text(
            json.dumps({"version": 1, "session_id": _SESSION_ID, "sent_at": sent_at.isoformat(), "body": body}),
            encoding="utf-8",
        )


def _old_notice_files(tmp_path: pathlib.Path) -> list[pathlib.Path]:
    directory = shared_layout.notices_directory(_OLD_ROOT, tmp_path)
    return sorted(directory.glob(f"{_SESSION_ID}.*.json")) if directory.is_dir() else []


def _new_writer(tmp_path: pathlib.Path) -> status_file.StatusFileWriter:
    """新しいサーバーが使う、旧サーバーと別のルートの書込主体を返す。"""
    return status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity(_NEW_ROOT, "root.json", None), state_root=tmp_path)


def _backend(
    monkeypatch: pytest.MonkeyPatch,
    manager: server_manager.AgentsServerManager,
    engine: str,
    turns: list[tuple[str, str]],
) -> FakeBackend:
    """偽backendを差し替え、MCPツールの関数がこのmanagerを使うようにする。"""
    backend = ThreadReadBackend(manager.sessions, turns) if engine == "codex" else FakeBackend(manager.sessions, engine)
    _install_backend(manager, engine, backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    return backend


def _cli_json(capsys: pytest.CaptureFixture[str], argv: list[str]) -> dict[str, Any]:
    with pytest.raises(SystemExit, match="0"):
        atk.main(argv)
    return json.loads(capsys.readouterr().out)


def _wait_lines(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> list[dict[str, Any]]:
    """新しいサーバーのルートで`atk agents wait`を1回実行し、標準出力のJSON Linesを返す。"""
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)
    monkeypatch.setattr(agents_wait, "is_human_terminal", lambda: False)
    capsys.readouterr()
    code = agents_wait.wait_for_result(environment={}, state_root=tmp_path, root_session_id=_NEW_ROOT)
    output = capsys.readouterr().out
    assert code in {0, 3}, output
    return [json.loads(line) for line in output.splitlines() if line.strip()]


@pytest.mark.parametrize("owner_alive", [True, False], ids=["owner-hung", "owner-exited"])
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
@pytest.mark.asyncio
async def test_owner_lost_session_stays_running_on_every_entry(
    engine: str,
    owner_alive: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先CLIが生存する記録は、`kill`前のどの照会でも`interrupted`・`failed`の終了を公開しない（完成条件1・4）。

    `thread/read`が元turnを終了と返しても引き継がず、追送は`thread/resume`を呼ばずに案内付きのエラーを返す。
    旧ルートに残った上り通知は、新しいサーバーの`show`・`send_message`と`atk agents wait`が回収して返す。
    """
    with _live_process() as (_executor_process, executor), contextlib.ExitStack() as stack:
        owner = stack.enter_context(_live_process())[1] if owner_alive else exited_process_identity()
        record = _publish_running(monkeypatch, tmp_path, engine, (owner, executor))
        _write_old_root(tmp_path, "公開担当の途中経過")
        original = record.read_bytes()

        async with server_manager.AgentsServerManager(_new_writer(tmp_path)) as manager:
            manager.activate()
            backend = _backend(monkeypatch, manager, engine, [("turn-1", "interrupted")])

            shown = await _show_through_tool(monkeypatch, manager, _SESSION_ID)
            assert shown["status"] == "running"
            assert shown["ownership"] == "owner_lost"
            assert [(item["role"], item["pid"], item["alive"]) for item in shown["recorded_processes"]] == [
                ("owner_server", owner.pid, owner_alive),
                ("executor", executor.pid, True),
            ]
            assert shown["owner_root_session_id"] == _OLD_ROOT
            assert shown["stranded_notice_count"] == 1
            assert shown["next_action"] == owner_lost.OWNER_LOST_NEXT_ACTION
            assert "`kill`" in shown["next_action"] and "`atk agents wait`" in shown["next_action"]
            assert [notice["body"] for notice in shown["notices"]] == ["公開担当の途中経過"]
            assert not _old_notice_files(tmp_path)

            _add_old_notices(tmp_path, "追送前の判断依頼")
            with pytest.raises(ValueError, match="error.recovery=owner_lost") as raised:
                await mcp_tools.send_message(_SESSION_ID, "続行")
            assert getattr(raised.value, "next_action", None) == owner_lost.OWNER_LOST_NEXT_ACTION
            assert "追送前の判断依頼" in str(raised.value)
            assert not _old_notice_files(tmp_path)

            shown_by_cli = _cli_json(capsys, ["agents", "show", _SESSION_ID])
            assert shown_by_cli["status"] == "running"
            assert shown_by_cli["ownership"] == "owner_lost"
            assert shown_by_cli["next_action"] == owner_lost.OWNER_LOST_NEXT_ACTION

            wait_targets.retain_wait_targets(_NEW_ROOT, "root.json", [_SESSION_ID], tmp_path)
            _add_old_notices(tmp_path, "待機中の通知")
            waited = _wait_lines(capsys, monkeypatch, tmp_path)
            assert [(line["session_id"], line["status"]) for line in waited] == [(_SESSION_ID, "running")]
            assert [notice["body"] for notice in waited[0]["notices"]] == ["待機中の通知"]
            assert not _old_notice_files(tmp_path)
            assert _wait_lines(capsys, monkeypatch, tmp_path)[0]["status"] == "running"

            # 予防対象の自動再開も、所有サーバーを失った実行中の子を終了した子として扱わない。
            parent = state.SessionState("parent-session", str(tmp_path), engine=engine)
            parent.live_child_session_ids.add(_SESSION_ID)
            resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
            manager.sessions[parent.session_id] = parent
            await manager._advance_child_session_wait(parent)
            assert _SESSION_ID in parent.live_child_session_ids
            manager.sessions.pop(parent.session_id)

            assert record.read_bytes() == original
            assert session_registry.resolve(_SESSION_ID).state is session_registry.Resolution.RUNNING
            assert not backend.resume_calls
            if isinstance(backend, ThreadReadBackend):
                assert not backend.read_calls


@pytest.mark.parametrize("engine", ["codex", "claude"])
@pytest.mark.asyncio
async def test_kill_stops_only_matching_executor_then_send_message_resumes(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """明示の`kill`は記録と一致する委譲先CLIだけを止め、停止を確かめてから`interrupted`を公開する（完成条件2・4）。

    所有サーバーのプロセスは止めない。続く`send_message`は同じ`session_id`の会話を再開する。
    """
    with _live_process() as (owner_process, owner), _live_process() as (executor_process, executor):
        record = _publish_running(monkeypatch, tmp_path, engine, (owner, executor))
        _write_old_root(tmp_path, "停止直前の通知")
        async with server_manager.AgentsServerManager(_new_writer(tmp_path)) as manager:
            manager.activate()
            backend = _backend(monkeypatch, manager, engine, [("turn-1", "inProgress")])

            killed = await mcp_tools.kill(_SESSION_ID)

            assert killed["status"] == "interrupted"
            assert killed["kill_requested"] is True
            assert [item["pid"] for item in killed["stopped_processes"]] == [executor.pid]
            assert [notice["body"] for notice in killed["notices"]] == ["停止直前の通知"]
            assert executor_process.poll() is not None
            assert owner_process.poll() is None
            published = json.loads(record.read_text(encoding="utf-8"))
            assert published["terminal"] is True and published["status"] == "interrupted"
            assert "interruption" not in published and "processes" not in published

            response = await mcp_tools.send_message(_SESSION_ID, "続行")
            assert response["delivery"] == "reply_started"
            assert backend.resume_calls == [_SESSION_ID]


@pytest.mark.asyncio
async def test_kill_does_not_stop_process_whose_start_time_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動時刻が記録と一致しないプロセスは、同じpidでも停止しない（完成条件2）。"""
    with _live_process() as (owner_process, owner), _live_process() as (reused_process, reused):
        stale = session_registry.ProcessIdentity(reused.pid, reused.create_time - 1000)
        _publish_running(monkeypatch, tmp_path, "codex", (owner, stale))
        async with server_manager.AgentsServerManager(_new_writer(tmp_path)) as manager:
            manager.activate()
            _backend(monkeypatch, manager, "codex", [("turn-1", "inProgress")])
            with pytest.raises(ValueError, match="turn_unobserved"):
                await mcp_tools.kill(_SESSION_ID)
        assert owner_lost.stop_recorded_executor(stale) is None
        assert reused_process.poll() is None
        assert owner_process.poll() is None
        assert owner_lost.stop_recorded_executor(session_registry.ProcessIdentity(os.getpid(), 0.0)) is not None


@pytest.mark.asyncio
async def test_record_whose_executor_exited_under_hung_owner_is_taken_over(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """委譲先CLIが終了し所有サーバーだけが応答せずに残る記録は、`thread/read`の終了を公開して再開できる（完成条件3）。"""
    with _live_process() as (owner_process, owner):
        record = _publish_running(monkeypatch, tmp_path, "codex", (owner, exited_process_identity()))
        async with server_manager.AgentsServerManager(_new_writer(tmp_path)) as manager:
            manager.activate()
            backend = _backend(monkeypatch, manager, "codex", [("turn-1", "completed")])

            shown = await _show_through_tool(monkeypatch, manager, _SESSION_ID)
            assert shown["status"] == "completed"
            assert "ownership" not in shown
            assert json.loads(record.read_text(encoding="utf-8"))["status"] == "completed"
            assert _cli_json(capsys, ["agents", "show", _SESSION_ID])["status"] == "completed"

            response = await mcp_tools.send_message(_SESSION_ID, "続行")
            assert response["delivery"] == "reply_started"
            assert backend.resume_calls == [_SESSION_ID]
        assert owner_process.poll() is None


@pytest.mark.asyncio
async def test_registry_resolved_send_message_collects_stranded_notices(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """登録簿から解決した終了sessionへの追送も、旧ルートに残る上り通知を回収して返す（完成条件4）。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(_SESSION_ID, terminal=True, engine="claude", cwd=str(tmp_path), turn_seq=1, status="completed")
    _write_old_root(tmp_path, "終了前の通知")
    async with server_manager.AgentsServerManager(_new_writer(tmp_path)) as manager:
        manager.activate()
        _backend(monkeypatch, manager, "claude", [])
        response = await mcp_tools.send_message(_SESSION_ID, "続行")
        assert response["delivery"] == "reply_started"
        assert [notice["body"] for notice in response["notices"]] == ["終了前の通知"]
        assert not _old_notice_files(tmp_path)


@pytest.mark.asyncio
async def test_atk_agents_wait_collects_result_left_in_old_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """待機対象の終了時の結果が旧ルートに残る場合も、新しいルートの`atk agents wait`が回収する（反映内容5）。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(_SESSION_ID, terminal=True, engine="claude", cwd=str(tmp_path), turn_seq=1, status="completed")
    old_writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity(_OLD_ROOT, "root.json", None), state_root=tmp_path
    )
    async with server_manager.AgentsServerManager(old_writer):
        finished = state.SessionState(_SESSION_ID, str(tmp_path), engine="claude", turn_seq=1)
        finished.status = "completed"
        finished.agent_message = "旧サーバーが公開した結果"
        finished.finalized_at = datetime.datetime.now(datetime.UTC).isoformat()
        finished.retention_deadline = time.monotonic() + 3600
        old_writer.retain_result(finished)
    assert shared_layout.results_directory(_OLD_ROOT, tmp_path).joinpath(f"{_SESSION_ID}.json").is_file()
    shared_layout.status_directory(_NEW_ROOT, tmp_path).mkdir(parents=True)
    wait_targets.retain_wait_targets(_NEW_ROOT, "root.json", [_SESSION_ID], tmp_path)

    waited = _wait_lines(capsys, monkeypatch, tmp_path)

    assert [(line["session_id"], line["status"], line["agent_message"]) for line in waited] == [
        (_SESSION_ID, "completed", "旧サーバーが公開した結果")
    ]
    assert not shared_layout.results_directory(_OLD_ROOT, tmp_path).joinpath(f"{_SESSION_ID}.json").exists()


@pytest.mark.asyncio
async def test_closing_server_keeps_record_running_while_executor_survives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """backendの停止後も委譲先CLIが残るturnは、停止時に`interrupted`を公開せず実行中のまま残す（反映内容5・完成条件7）。

    後続のサーバーは同じ記録を所有サーバーを失った実行中として扱う。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    with _live_process() as (_process, executor):
        async with server_manager.AgentsServerManager(None) as owner:
            _backend(monkeypatch, owner, "claude", [])
            session = state.SessionState(_SESSION_ID, str(tmp_path), engine="claude", turn_seq=1)
            session.status = "running"
            session.publish_registry = True
            session.executor_process = executor
            owner.sessions[_SESSION_ID] = session
            await session.publish_registration_async()
        record = json.loads((session_registry.registry_directory() / f"{_SESSION_ID}.json").read_text(encoding="utf-8"))
        assert record["terminal"] is False and record["status"] == "running"

        resolution = session_registry.resolve(_SESSION_ID)
        assert resolution.state is session_registry.Resolution.RUNNING
        assert resolution.live_executors == (executor,)
        assert session_registry.running_ownership(resolution, writer_alive=False) is (
            session_registry.RunningOwnership.OWNER_LOST
        )


@pytest.mark.asyncio
async def test_stop_of_record_owned_by_live_process_keeps_record_and_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """自プロセスが所有しない記録で記録プロセスが実在する場合、`stop`は解放済みへ置き換えず結果も削除しない（完成条件7）。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    with _live_process() as (_owner_process, owner), _live_process() as (_executor_process, executor):
        session_registry.publish(
            _SESSION_ID,
            terminal=True,
            engine="claude",
            cwd=str(tmp_path),
            turn_seq=1,
            status="completed",
            processes=(owner, executor),
        )
        record = session_registry.registry_directory() / f"{_SESSION_ID}.json"
        original = record.read_bytes()
        result = shared_layout.results_directory(_NEW_ROOT, tmp_path) / f"{_SESSION_ID}.json"
        async with server_manager.AgentsServerManager(_new_writer(tmp_path)) as manager:
            manager.activate()
            _backend(monkeypatch, manager, "claude", [])
            result.parent.mkdir(parents=True, exist_ok=True)
            result.write_text(json.dumps({"status": "completed", "agent_message": "残す結果"}), encoding="utf-8")
            with pytest.raises(ValueError, match="still alive outside this agents_server"):
                await mcp_tools.stop_session(_SESSION_ID)
            assert record.read_bytes() == original
            assert result.is_file()


@pytest.mark.asyncio
async def test_activation_keeps_same_named_status_file_of_live_writer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動時に同名の状態ファイルを生存中の別プロセスが書いていれば削除せず、自身は別名へ書く（完成条件7）。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    with _live_process() as (_process, previous):
        directory = shared_layout.status_directory("conversation-root", tmp_path)
        directory.mkdir(parents=True)
        now = datetime.datetime.now(datetime.UTC).isoformat()
        path = directory / "root.json"
        path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "host_session_id": None,
                    "heartbeat_at": now,
                    "updated_at": now,
                    "writer_process": session_registry.process_payload(previous),
                    "sessions": [{"session_id": _SESSION_ID, "status": "running"}],
                }
            ),
            encoding="utf-8",
        )
        original = path.read_bytes()
        writer = status_file.StatusFileWriter(
            {}, shared_roots.StatusFileIdentity("conversation-root", "root.json", None), state_root=tmp_path
        )
        async with server_manager.AgentsServerManager(writer) as manager:
            await manager.activate_async()
            assert path.read_bytes() == original
            relocated = directory / f"root-p{os.getpid()}.json"
            assert json.loads(relocated.read_text(encoding="utf-8"))["writer_process"]["pid"] == os.getpid()
            assert status_file.live_writer_holds_session(_SESSION_ID, tmp_path)
        assert path.read_bytes() == original
        assert not relocated.exists()

    # 書いたプロセスが終了した同名ファイルは、従来どおり起動時に自身のファイルとして置き換える。
    payload = json.loads(original)
    payload["writer_process"] = session_registry.process_payload(exited_process_identity())
    path.write_text(json.dumps(payload), encoding="utf-8")
    writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("conversation-root", "root.json", None), state_root=tmp_path
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        await manager.activate_async()
        assert json.loads(path.read_text(encoding="utf-8"))["writer_process"]["pid"] == os.getpid()


def test_registry_sweep_keeps_running_record_with_live_process(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """7日を超えて更新されていない記録も、終了しておらず記録プロセスが実在すれば掃引しない（完成条件7）。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    with _live_process() as (_process, executor):
        live = "019a2c41-5b6e-7d10-8f21-000000000001"
        exited = "019a2c41-5b6e-7d10-8f21-000000000002"
        legacy = "019a2c41-5b6e-7d10-8f21-000000000003"
        for session_id, processes in (
            (live, (exited_process_identity(), executor)),
            (exited, (exited_process_identity(), exited_process_identity())),
            (legacy, ()),
        ):
            session_registry.publish(
                session_id, terminal=False, cwd=str(tmp_path), status="running", processes=processes, state_root=tmp_path
            )
        old = time.time() - shared_sweep.STALE_SHARED_STATE_SECONDS - 60
        for path in session_registry.registry_directory(tmp_path).glob("*.json"):
            os.utime(path, (old, old))

        shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path)

        remaining = {path.stem for path in session_registry.registry_directory(tmp_path).glob("*.json")}
        assert remaining == {live}
        assert session_registry.resolve(live, state_root=tmp_path).state is session_registry.Resolution.RUNNING


def test_process_loop_record_stays_running_while_claude_child_survives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """process-loopの記録は`claude`子プロセスの識別を持ち、process-loopの終了後も子の生存中は終了と解決されない（完成条件7）。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    identifier = "019a2c41-5b6e-7d10-8f21-0000000000aa"
    owner = session_registry.owner_process()
    assert owner is not None
    # `_build_session_argv`と同じく、起動前のprocess-loop自身だけを記録プロセスとして公開する。
    session_registry.publish(
        identifier,
        terminal=False,
        engine="claude",
        cwd=str(tmp_path),
        model_type="orchestrate",
        claude_role="orchestrator",
        processes=(owner,),
    )
    record = session_registry.registry_directory() / f"{identifier}.json"
    environment = {**os.environ, _pl_env.PROCESS_LOOP_SESSION_ID_ENV: identifier}
    with subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"], env=environment) as child:
        try:
            identity = session_registry.ProcessIdentity.of(child.pid)
            assert identity is not None
            # runnerはPopenから受け取ったPIDをそのまま登録する。
            process_loop_session._attach_orchestrator_child(identifier, identity)  # pylint: disable=protected-access
            assert [process.pid for process in session_registry.resolve(identifier).executor_processes] == [child.pid]

            # process-loopが強制終了した後を、記録の所有プロセスを終了済みの識別へ置き換えて表す。
            payload = json.loads(record.read_text(encoding="utf-8"))
            payload["processes"][0] = session_registry.process_payload(exited_process_identity())
            record.write_text(json.dumps(payload), encoding="utf-8")
            assert session_registry.resolve(identifier).state is session_registry.Resolution.RUNNING
        finally:
            child.kill()
            child.wait()
    assert session_registry.resolve(identifier).state is session_registry.Resolution.TERMINAL
