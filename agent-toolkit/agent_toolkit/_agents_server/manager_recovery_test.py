"""親MCPの公開起動・終了・再起動を経由する委譲先の復旧を検証する。"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

import pytest

# MCPの起動・終了境界と共有状態を通す結合試験。
# pylint: disable=protected-access
from agent_toolkit._agents_server import (
    agents_wait,
    launch_requests,
    mcp_tools,
    notify,
    shared_layout,
    shared_roots,
    shared_sweep,
    state,
    status_file,
)
from agent_toolkit._agents_server.manager import AgentsServerManager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp
from agent_toolkit._atk.managed_temp import creation
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing.agents_server_support import FakeBackend, _complete, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _RecoveryBackend(FakeBackend):
    """モデル呼出だけを代用し、登録・結果の永続化は実Managerへ委ねる。"""

    on_resume: Callable[[state.SessionState], Awaitable[None]] | None = None
    root_session_id: str | None = None

    async def start(self, *args: Any, **kwargs: Any) -> state.SessionState:
        session = await super().start(*args, **kwargs)
        session.publish_registry = True
        if session.engine == "claude":
            session.claude_connection = "subscription"
            session.claude_role = "delegate"
        return session

    async def resume(self, *args: Any, **kwargs: Any) -> state.SessionState:
        connection = kwargs.pop("connection_kind", None)
        session = await super().resume(*args, **kwargs)
        session.claude_connection = connection
        if session.engine == "claude":
            session.claude_role = "orchestrator" if session.model_type == "orchestrate" else "delegate"
        session.owner_root_session_id = self.root_session_id
        if self.on_resume is None:
            _complete(session, message="復旧後の成果")
        else:
            await self.on_resume(session)
        return session


@pytest.mark.parametrize("previous_outcome", ["interrupted", "completed", "stopped"])
@pytest.mark.parametrize("engine", ["claude", "codex", "agy", "opencode"])
@pytest.mark.asyncio
async def test_parent_mcp_restart_recovers_same_child_and_uncollected_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, previous_outcome: str, engine: str
) -> None:
    """公開startの子を保存し、親の再起動だけで同一IDの続行または元結果の回収へ進む。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [(engine, "model", "high")])

    async def ready(_cwd: str) -> None:
        """外部ホストの準備のみを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    old_writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("old-root", "parent.json", "parent"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    old = AgentsServerManager(old_writer)
    first_backend = _RecoveryBackend(old.sessions, engine)
    first_backend.publish_registry = True
    install_backend(old, engine, first_backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", old)
    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        started = await mcp_tools.start(
            str(tmp_path), mode="explore", prompt="保存済みの作業を完遂せよ", model_type="chores_fast"
        )
        identifier = started["session_id"]
        before = old.sessions[identifier]
        child_notes = creation.resolve_session_temp(identifier) / "handoff.md"
        parent_notes = creation.resolve_session_temp("parent") / "work-contract.md"
        child_notes.write_text("子の保存成果と未完了操作", encoding="utf-8")
        parent_notes.write_text("親の現行要求", encoding="utf-8")
        assert session_registry.child_session_ids("parent", state_root=tmp_path) == (identifier,)
        if previous_outcome in {"completed", "stopped"}:
            _complete(before, message="再起動前の成果")
            await old_writer.flush_async()
            if previous_outcome == "stopped":
                await mcp_tools.stop_session(identifier)
    now = datetime.datetime.now(datetime.UTC)
    stamp = (now - datetime.timedelta(days=8)).timestamp()
    for area in (tmp_path / "agents-server", child_notes.parent, parent_notes.parent):
        for item in sorted(area.rglob("*"), reverse=True):
            os.utime(item, (stamp, stamp))
        os.utime(area, (stamp, stamp))
    shared_sweep.sweep_stale_shared_state(keep_root_session_id=None, state_root=tmp_path, now=now.timestamp())
    swept = managed_temp.sweep_managed_temp(now=now)
    assert not swept.failed
    if previous_outcome != "stopped":
        assert child_notes.read_text(encoding="utf-8") == "子の保存成果と未完了操作"
        assert parent_notes.read_text(encoding="utf-8") == "親の現行要求"
    new_writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("new-root", "parent.json", "parent"),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    recovered = AgentsServerManager(new_writer)
    backend = _RecoveryBackend(recovered.sessions, engine)
    backend.root_session_id = "new-root"
    backend.publish_registry = True
    install_backend(recovered, engine, backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", recovered)
    async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
        async with asyncio.timeout(5):
            response = await recovered.wait()
            while response["status"] == "running":
                response = await recovered.wait()
        if previous_outcome == "stopped":
            assert not backend.resume_calls
            assert response["status"] == "expired"
        else:
            assert (response.get("session_id"), response["status"]) == (identifier, "completed")
            assert response["agent_message"] == ("復旧後の成果" if previous_outcome == "interrupted" else "再起動前の成果")
            assert backend.resume_calls == ([identifier] if previous_outcome == "interrupted" else [])
            assert not (shared_layout.results_directory("old-root", tmp_path) / f"{identifier}.json").exists()
            assert first_backend.start_calls == [("model", "high", "explore")]
            if previous_outcome == "interrupted":
                resumed = recovered.sessions[identifier]
                assert (resumed.cwd, resumed.model, resumed.effort, resumed.prompt, resumed.created_at) == (
                    before.cwd,
                    before.model,
                    before.effort,
                    before.prompt,
                    before.created_at,
                )
                assert resumed.claude_connection == before.claude_connection
                assert resumed.claude_role == before.claude_role


@contextlib.asynccontextmanager
async def _mcp_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    root: str,
    parent: str,
    engine: str,
    *,
    on_resume: Callable[[state.SessionState], Awaitable[None]] | None = None,
) -> AsyncGenerator[tuple[AgentsServerManager, _RecoveryBackend]]:
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity(root, f"{parent}.json", parent),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    manager = AgentsServerManager(writer)
    backend = _RecoveryBackend(manager.sessions, engine)
    backend.publish_registry = True
    backend.root_session_id = root
    backend.on_resume = on_resume
    install_backend(manager, engine, backend)
    with monkeypatch.context() as patch:
        patch.setattr(mcp_tools, "_MANAGER", manager)
        patch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [(engine, "model", "high")])
        async with mcp_tools._mcp_lifespan(mcp_tools.mcp):
            yield manager, backend


@pytest.mark.asyncio
async def test_mixed_child_and_grandchild_resume_without_followup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """親の復帰からClaude子・Codex孫の同一ID復旧と結果の上り配送へ接続する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    async def ready(_cwd: str) -> None:
        """外部CLIの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    async with _mcp_owner(monkeypatch, tmp_path, "old-parent-root", "parent", "claude"):
        child = await mcp_tools.start(str(tmp_path), mode="explore", prompt="子の依頼", model_type="chores_fast")
        async with _mcp_owner(monkeypatch, tmp_path, "old-child-root", child["session_id"], "codex"):
            grandchild = await mcp_tools.start(str(tmp_path), mode="explore", prompt="孫の依頼", model_type="chores_fast")
    resumed_grandchildren: list[str] = []

    async def resume_child(session: state.SessionState) -> None:
        async with _mcp_owner(monkeypatch, tmp_path, "new-child-root", session.session_id, "codex") as (owner, backend):
            async with asyncio.timeout(5):
                result = await owner.wait()
                while result["status"] == "running":
                    result = await owner.wait()
            assert result["session_id"] == grandchild["session_id"]
            assert result["status"] == "completed"
            assert not backend.start_calls
            resumed_grandchildren.extend(backend.resume_calls)
        # 同一プロセスで代用するMCPのグローバルを元へ戻してから親の結果を公開する。
        _complete(session, message=f"孫の成果を受領: {result['agent_message']}")

    async with _mcp_owner(monkeypatch, tmp_path, "new-parent-root", "parent", "claude", on_resume=resume_child) as (
        owner,
        backend,
    ):
        async with asyncio.timeout(5):
            result = await owner.wait()
            while result["status"] == "running":
                result = await owner.wait()
        assert result["session_id"] == child["session_id"]
        assert result["agent_message"] == "孫の成果を受領: 復旧後の成果"
        assert not backend.start_calls
        assert backend.resume_calls == [child["session_id"]]
        assert resumed_grandchildren == [grandchild["session_id"]]
        assert session_registry.child_session_ids("parent", state_root=tmp_path) == (child["session_id"],)
        assert session_registry.child_session_ids(child["session_id"], state_root=tmp_path) == (grandchild["session_id"],)


@pytest.mark.asyncio
async def test_live_owner_is_observed_and_cli_collects_old_root_result_and_notice(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """旧所有者が生存する子を二重起動せず、その旧rootの通知・結果を公開waitから受領する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    async def ready(_cwd: str) -> None:
        """外部CLIの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    async with _mcp_owner(monkeypatch, tmp_path, "old-live-root", "parent", "codex") as (old, _):
        started = await mcp_tools.start(str(tmp_path), mode="explore", prompt="生存中の子", model_type="chores_fast")
        identifier = started["session_id"]
        async with _mcp_owner(monkeypatch, tmp_path, "new-live-root", "parent", "codex") as (recovered, backend):
            assert (await recovered.wait())["status"] == "running"
            assert not backend.start_calls and not backend.resume_calls
            notify.save_notification(
                "旧所有者の途中報告",
                shared_roots.StatusFileIdentity("old-live-root", f"{identifier}.json", identifier),
                state_root=tmp_path,
            )
            capsys.readouterr()
            _complete(old.sessions[identifier], message="生存した元の子の成果")
            assert old._status_writer is not None
            await old._status_writer.flush_async()
            async with asyncio.timeout(5):
                code = await asyncio.to_thread(
                    agents_wait.wait_for_result,
                    environment={"AGENT_TOOLKIT_OWNER_SESSION": "new-live-root", "CODEX_THREAD_ID": "parent"},
                    state_root=tmp_path,
                    root_session_id="new-live-root",
                )
            assert code == 0
            rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
            result = next(row for row in rows if row.get("session_id") == identifier and row.get("status") == "completed")
            assert result["agent_message"] == "生存した元の子の成果"
            assert "旧所有者の途中報告" in json.dumps(result.get("notices"), ensure_ascii=False)
            assert not backend.start_calls and not backend.resume_calls


@pytest.mark.asyncio
async def test_owner_lost_executor_stops_before_same_id_resume(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """生存CLIを残す所有者喪失を注入し、記録世代の実プロセス終了後だけ同一IDを再開する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    async def ready(_cwd: str) -> None:
        """外部ホストの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    with subprocess.Popen(
        [sys.executable, "-u", "-c", "import time; print('ready', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=host_environ(),
    ) as process:
        try:
            assert process.stdout is not None
            async with asyncio.timeout(5):
                assert await asyncio.to_thread(process.stdout.readline) == "ready\n"
            identity = session_registry.ProcessIdentity.of(process.pid)
            assert identity is not None
            async with _mcp_owner(monkeypatch, tmp_path, "old-orphan-root", "parent", "codex") as (old, _):
                started = await mcp_tools.start(
                    str(tmp_path), mode="explore", prompt="所有者喪失時の子", model_type="chores_fast"
                )
                identifier = started["session_id"]
                old.sessions[identifier].executor_process = identity
                assert old._status_writer is not None
                await old._status_writer.flush_async()
            assert identity.exists()

            async def resumed(session: state.SessionState) -> None:
                assert not identity.exists()
                _complete(session, message="旧CLI終了後の成果")

            async with _mcp_owner(monkeypatch, tmp_path, "new-orphan-root", "parent", "codex", on_resume=resumed) as (
                owner,
                backend,
            ):
                async with asyncio.timeout(5):
                    result = await owner.wait()
                    while result["status"] == "running":
                        result = await owner.wait()
                assert result["session_id"] == identifier
                assert result["agent_message"] == "旧CLI終了後の成果"
                assert backend.resume_calls == [identifier]
                assert not backend.start_calls
            process.wait(timeout=5)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


@pytest.mark.asyncio
async def test_simultaneous_parent_resumes_start_one_writer_and_collect_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """二つの親MCPが同時に復帰しても、同一IDの再開は1回で結果も1回だけ消費する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    async def ready(_cwd: str) -> None:
        """外部ホストの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    async with _mcp_owner(monkeypatch, tmp_path, "old-concurrent-root", "parent", "codex"):
        original = await mcp_tools.start(str(tmp_path), mode="explore", prompt="同時復帰する子", model_type="chores_fast")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def resumed(session: state.SessionState) -> None:
        entered.set()
        await release.wait()
        _complete(session, message="一度だけの成果")

    async def final_result(owner: AgentsServerManager) -> dict[str, Any]:
        response = await owner.wait()
        while response["status"] == "running":
            response = await owner.wait()
        return response

    async with _mcp_owner(monkeypatch, tmp_path, "first-resumer", "parent", "codex", on_resume=resumed) as (
        first,
        first_backend,
    ):
        async with asyncio.timeout(5):
            await entered.wait()
        async with _mcp_owner(monkeypatch, tmp_path, "second-resumer", "parent", "codex", on_resume=resumed) as (
            second,
            second_backend,
        ):
            assert (await second.wait())["status"] == "running"
            assert len(first_backend.resume_calls) + len(second_backend.resume_calls) == 1
            release.set()
            async with asyncio.timeout(5):
                results = await asyncio.gather(final_result(first), final_result(second))
            completed = [result for result in results if result["status"] == "completed"]
            assert len(completed) == 1
            assert completed[0]["session_id"] == original["session_id"]
            assert completed[0]["agent_message"] == "一度だけの成果"
            assert len(first_backend.resume_calls) + len(second_backend.resume_calls) == 1


@pytest.mark.asyncio
async def test_reinterrupted_recovery_retains_original_launch_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再開の初期公開後に故障しても元の依頼・追送を保ち、次の親復帰で同一IDを続行する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    async def ready(_cwd: str) -> None:
        """外部ホストの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    async with _mcp_owner(monkeypatch, tmp_path, "original-root", "parent", "codex") as (owner, _):
        started = await mcp_tools.start(
            str(tmp_path), mode="explore", prompt="元の要求と認可", model_type="chores_fast", label="元の担当"
        )
        identifier = started["session_id"]
        await mcp_tools.send_message(identifier, "保存すべき追送")
        original = session_registry.LaunchInfo.of(owner.sessions[identifier])

    async def fail_after_publication(session: state.SessionState) -> None:
        session.touch()
        await session.drain_registration()
        raise RuntimeError("制御した再開途中の失敗")

    async with _mcp_owner(monkeypatch, tmp_path, "failed-resume-root", "parent", "codex", on_resume=fail_after_publication) as (
        owner,
        _,
    ):
        capsys.readouterr()
        async with asyncio.timeout(5):
            await asyncio.to_thread(
                agents_wait.wait_for_result,
                environment={"AGENT_TOOLKIT_OWNER_SESSION": "failed-resume-root", "CODEX_THREAD_ID": "parent"},
                state_root=tmp_path,
                root_session_id="failed-resume-root",
            )
        assert "制御した再開途中の失敗" in capsys.readouterr().out
        async with asyncio.timeout(5):
            with pytest.raises(RuntimeError, match="制御した再開途中の失敗"):
                while True:
                    await owner.wait()
    async with _mcp_owner(monkeypatch, tmp_path, "retried-root", "parent", "codex") as (owner, backend):
        async with asyncio.timeout(5):
            result = await owner.wait()
            while result["status"] == "running":
                result = await owner.wait()
        assert result["session_id"] == identifier
        assert result["status"] == "completed"
        assert backend.resume_calls == [identifier]
        recovered = session_registry.LaunchInfo.of(owner.sessions[identifier])
        assert (recovered.label, recovered.prompt, recovered.created_at, recovered.followup_prompts) == (
            original.label,
            original.prompt,
            original.created_at,
            original.followup_prompts,
        )


@pytest.mark.asyncio
async def test_recovery_failure_before_session_creation_reaches_public_wait(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """新しい状態がまだ無い接続失敗でも、親の公開CLIへ原因を配送する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    async def ready(_cwd: str) -> None:
        """外部ホストの準備だけを代用する。"""

    monkeypatch.setattr(launch_requests, "check_plugin_commands", ready)
    async with _mcp_owner(monkeypatch, tmp_path, "before-failure-root", "parent", "codex"):
        started = await mcp_tools.start(str(tmp_path), mode="explore", prompt="保存する原要求", model_type="chores_fast")

    async def fail_before_creation(*_args: Any, **_kwargs: Any) -> state.SessionState:
        raise RuntimeError("制御した接続初期化前の失敗")

    async with _mcp_owner(monkeypatch, tmp_path, "early-failure-root", "parent", "codex") as (owner, backend):
        monkeypatch.setattr(backend, "resume", fail_before_creation)
        async with asyncio.timeout(5):
            while not owner._recovery_errors:
                await asyncio.sleep(0)
        capsys.readouterr()
        async with asyncio.timeout(5):
            code = await asyncio.to_thread(
                agents_wait.wait_for_result,
                environment={"AGENT_TOOLKIT_OWNER_SESSION": "early-failure-root", "CODEX_THREAD_ID": "parent"},
                state_root=tmp_path,
                root_session_id="early-failure-root",
            )
        assert code == 0
        assert "制御した接続初期化前の失敗" in capsys.readouterr().out
        assert not backend.start_calls
        assert started["session_id"] not in owner.sessions
        with pytest.raises(RuntimeError, match="制御した接続初期化前の失敗"):
            await owner.wait()


_COLD_RECOVERY_SOURCE = """\
import asyncio
import json
import os
import pathlib
import sys
import pytest
from agent_toolkit._testing import isolation

root = pathlib.Path(sys.argv[2])
patch = pytest.MonkeyPatch()
isolation.apply_development_state_isolation(root, patch, os.environ["PATH"])
from agent_toolkit._agents_server import backends, launch_requests, manager, mcp_tools
from agent_toolkit._agents_server.manager_recovery_test import _mcp_owner
from agent_toolkit._common import session_registry, state_paths, wait_schedule
from agent_toolkit._testing.agents_server_support import _complete

async def ready(_cwd):
    pass

def refuse_backend(*args, **kwargs):
    raise AssertionError("代用していない実backendの起動を拒否")

patch.setattr(state_paths, "state_dir", lambda: root)
patch.setattr(launch_requests, "check_plugin_commands", ready)
patch.setattr(backends, "create_backend", refuse_backend)
# agents_server_isolationと同じく、代用backendの可用性待ちと外部認証照会だけを短縮する。
patch.setattr(manager, "START_AVAILABILITY_TIMEOUT", 0.05)
patch.setattr(wait_schedule, "get_wait_timeout", lambda _bucket: 0.0)

async def run():
    identity = session_registry.owner_process()
    assert identity is not None
    if sys.argv[1] == "seed":
        async with _mcp_owner(patch, root, "old-parent-root", "parent", "claude") as (parent, _):
            child = await mcp_tools.start(str(root), mode="explore", prompt="子の原要求", model_type="chores_fast")
            async with _mcp_owner(patch, root, "old-child-root", child["session_id"], "codex") as (owner, _):
                grandchild = await mcp_tools.start(str(root), mode="explore", prompt="孫の原要求", model_type="chores_fast")
                for manager in (parent, owner):
                    for session in manager.sessions.values():
                        await session.drain_registration()
                    await manager._status_writer.flush_async()
                print(json.dumps({"child": child["session_id"], "grandchild": grandchild["session_id"],
                                  "process": session_registry.process_payload(identity)}), flush=True)
                os._exit(0)
    grandchildren = []
    async def resume_child(session):
        async with _mcp_owner(patch, root, "new-child-root", session.session_id, "codex") as (owner, backend):
            result = await owner.wait()
            while result["status"] == "running":
                result = await owner.wait()
            assert result["status"] == "completed"
            grandchildren.extend(backend.resume_calls)
        _complete(session, message="子と孫の仕事を続行した")
    async with _mcp_owner(patch, root, "new-parent-root", "parent", "claude", on_resume=resume_child) as (owner, backend):
        result = await owner.wait()
        while result["status"] == "running":
            result = await owner.wait()
        assert result["status"] == "completed"
        print(json.dumps({"child": result["session_id"], "grandchildren": grandchildren,
                          "resumed": backend.resume_calls, "message": result["agent_message"],
                          "process": session_registry.process_payload(identity)}), flush=True)

asyncio.run(run())
"""


def test_new_process_recovers_tree_after_every_original_process_disappears(
    tmp_path: pathlib.Path,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """正常closeを省いて全記録プロセスが消失した後、新しいプロセスで元の親子孫を復旧する。"""
    root = tmp_path / "cold-state"
    root.mkdir()
    driver = tmp_path / "cold_recovery.py"
    driver.write_text(_COLD_RECOVERY_SOURCE, encoding="utf-8")
    seed = subprocess.run(
        [sys.executable, str(driver), "seed", str(root)],
        cwd=tmp_path,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    original = json.loads(seed.stdout)
    assert not session_registry.ProcessIdentity(**original["process"]).exists()
    for identifier in (original["child"], original["grandchild"]):
        resolution = session_registry.resolve(identifier, state_root=root)
        assert resolution.inferred_terminal
        assert resolution.resume_info is not None
        assert resolution.resume_info.status == "interrupted"
    resumed = subprocess.run(
        [sys.executable, str(driver), "recover", str(root)],
        cwd=tmp_path,
        env=host_environ(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=True,
    )
    recovered = json.loads(resumed.stdout)
    assert recovered["process"] != original["process"]
    assert recovered["child"] == original["child"]
    assert recovered["grandchildren"] == [original["grandchild"]]
    assert recovered["resumed"] == [original["child"]]
    assert recovered["message"] == "子と孫の仕事を続行した"
    assert not session_registry.ProcessIdentity(**recovered["process"]).exists()
