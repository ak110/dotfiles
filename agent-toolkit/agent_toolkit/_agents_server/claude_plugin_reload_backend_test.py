"""更新の検知からidle・次query・終了回収までの所有順序を検証する。"""

import asyncio
import contextlib
import json
import pathlib
import subprocess
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

import claude_agent_sdk
import psutil
import pytest

from agent_toolkit._agents_server import claude, claude_plugins, claude_stream, launch_prompts, launch_requests
from agent_toolkit._testing import late_descendant
from agent_toolkit._testing.agents_server_support import AssistantMessage, ManualObserver, QueueClaudeClient, ResultMessage

# 所有loopと実helperの接続、およびSDK制御要求の故障境界を直接検証する。
# pylint: disable=protected-access


@pytest.mark.parametrize("versioned_input", [False, True])
@pytest.mark.asyncio
async def test_plugin_reload_waits_for_idle_and_precedes_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, versioned_input: bool
) -> None:
    """busyで検知した更新はidleへ進めた後、同じ接続の次queryより先に反映する。"""
    changed = asyncio.Event()
    observed = asyncio.Event()
    reloaded = asyncio.Event()
    closed = asyncio.Event()
    order: list[str] = []
    mirror = tmp_path / "input/agent-toolkit"
    for name in ("agent-toolkit", "agent-basics"):
        root = mirror.parent / name
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (root / "skills").mkdir()

    class Reloader(claude_plugins.ClaudePluginReloader):
        async def prepare(self) -> None:
            if versioned_input:
                self.options["plugins"][0]["path"] = str(mirror)

        async def wait_for_change(self) -> None:
            await changed.wait()
            changed.clear()
            observed.set()

        async def reload(self, client: Any) -> dict[str, Any]:
            del client
            order.append("reload")
            reloaded.set()
            return {}

        async def close(self, *, retain_input: bool = False) -> None:
            assert not retain_input
            closed.set()

    class Client(QueueClaudeClient):
        async def query(self, prompt: str) -> None:
            order.append("query")
            await super().query(prompt)

    # session_state_changedはCLIが報告する同じSystemMessage形式で渡す。
    client = Client(
        [
            [
                claude_agent_sdk.SystemMessage("init", {"session_id": "reload-order"}),
                claude_agent_sdk.SystemMessage("session_state_changed", {"state": "running"}),
                AssistantMessage("作業中"),
            ],
            [AssistantMessage("継続"), ResultMessage("完了")],
        ]
    )
    monkeypatch.setattr(claude_stream, "ClaudePluginReloader", Reloader)
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
        original = launch_prompts.plugin_resource_instructions(claude.plugin_roots.SERVER_PLUGIN_ROOT, "claude", str(tmp_path))
        session = await backend.start(launch_requests.wrap_delivery_body(original + "\n\n初回"), str(tmp_path))
        assert client.queries[0].count("agent-toolkit plugin root:") == 1
        if versioned_input:
            assert f"agent-toolkit plugin root: {mirror}\n" in client.queries[0]
        changed.set()
        await asyncio.wait_for(observed.wait(), timeout=2)
        assert order == ["query"]
        client.messages.put_nowait(ResultMessage("初回完了"))
        client.messages.put_nowait(claude_agent_sdk.SystemMessage("session_state_changed", {"state": "idle"}))
        await asyncio.wait_for(reloaded.wait(), timeout=2)
        response = await backend.send_message(session, "後続")
        assert response["delivery"] == "reply_started"
        assert order == ["query", "reload", "query"]
    assert closed.is_set()
    assert client.disconnected
    assert client.readers == 0


@pytest.mark.parametrize("failure", ["manifest", "copy", "sdk", "inventory"])
@pytest.mark.asyncio
async def test_reload_failure_keeps_connection_and_completes_followup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, failure: str
) -> None:
    """実helperの更新失敗を所有loopへ渡し、同じ接続の後続queryと旧入力を保持する。"""
    changed = asyncio.Event()
    attempted = asyncio.Event()
    reloaders: list[claude_plugins.ClaudePluginReloader] = []
    root = tmp_path / "cache/mine/agent-toolkit/1.0.0"
    (root / ".claude-plugin").mkdir(parents=True)
    manifest = root / ".claude-plugin/plugin.json"
    manifest.write_text(json.dumps({"name": "agent-toolkit", "version": "1.0.0"}), encoding="utf-8")
    (root / "skills").mkdir()
    (root / "skills/content.md").write_text("旧入力", encoding="utf-8")
    broken = False

    class Reloader(claude_plugins.ClaudePluginReloader):
        async def prepare(self) -> None:
            self.options["plugins"] = [{"type": "local", "path": str(root)}]
            await super().prepare()
            basics = pathlib.Path(self.options["plugins"][0]["path"]).parent / "agent-basics"
            (basics / ".claude-plugin").mkdir(parents=True)
            (basics / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
            (basics / "skills").mkdir()
            reloaders.append(self)

        def _inventory(self) -> list[claude_plugins._InstalledPlugin]:
            if broken and failure == "inventory":
                raise ValueError("不正な導入一覧")
            return [claude_plugins._InstalledPlugin(id="agent-toolkit@mine", installPath=str(root))]

        async def wait_for_change(self) -> None:
            await changed.wait()
            changed.clear()

        async def reload_if_idle(self, client: Any, session: Any) -> None:
            await super().reload_if_idle(client, session)
            if broken:
                attempted.set()

    async def control(_payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("SDK制御要求の失敗")

    def copy_failure(_source: pathlib.Path, _target: pathlib.Path, **_kwargs: Any) -> None:
        raise OSError("複製失敗")

    class Client(QueueClaudeClient):
        _query = SimpleNamespace(_send_control_request=control)

    client = Client(
        [
            [claude_agent_sdk.SystemMessage("init", {"session_id": "reload-failure"}), ResultMessage("初回完了")],
            [AssistantMessage("同じ接続の成果"), ResultMessage("完了")],
        ]
    )
    monkeypatch.setattr(claude_stream, "ClaudePluginReloader", Reloader)
    monkeypatch.setattr(claude_plugins.watchdog.observers, "Observer", ManualObserver)
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
        session = await backend.start("初回", str(tmp_path))
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        reloader = reloaders[0]
        mirror = pathlib.Path(reloader.options["plugins"][0]["path"])
        broken = True
        if failure == "manifest":
            manifest.write_text("{", encoding="utf-8")
        elif failure == "copy":
            monkeypatch.setattr(claude_plugins.shutil, "copytree", copy_failure)
        reloader.mark_changed()
        changed.set()
        await asyncio.wait_for(attempted.wait(), timeout=5)
        assert reloader.dirty and not reloader.updated
        assert (mirror / "skills/content.md").read_text(encoding="utf-8") == "旧入力"
        assert (await backend.send_message(session, "後続"))["delivery"] == "reply_started"
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        assert session.status == "completed" and session.agent_message == "完了"
        assert session.session_id == "reload-failure" and len(client.queries) == 2
    assert client.disconnected and client.readers == 0


@pytest.mark.parametrize("monitor_failure", [False, True])
@pytest.mark.asyncio
async def test_real_prepare_inventory_and_optional_monitor_preserve_backend_work(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, monitor_failure: bool
) -> None:
    """一覧選別と任意監視の縮退を実helperに通し、同接続の後続作業と終了を検証する。"""
    source = tmp_path / "cache/mine/agent-toolkit/1.0.0"
    (source / ".claude-plugin").mkdir(parents=True)
    (source / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": "1.0.0"}), encoding="utf-8"
    )
    (source / "skills").mkdir()
    reloaders: list[claude_plugins.ClaudePluginReloader] = []
    inventory_calls: list[list[str]] = []
    control_calls: list[dict[str, str]] = []
    reloaded = asyncio.Event()
    stopped: list[bool] = []

    def inventory(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        inventory_calls.append(command)
        # 2.1.319で観測した同期2項目の形と、別の対象外形態を混在させる。
        entries = [
            {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(source)},
            {"id": "agent-toolkit@synced", "scope": "synced", "enabled": True, "installPath": ""},
            {"id": "agent-basics@synced", "scope": "synced", "enabled": True, "installPath": ""},
            {"id": "disabled@mine", "scope": "user", "enabled": False, "installPath": None},
        ]
        return subprocess.CompletedProcess(command, 0, json.dumps(entries), "")

    class Reloader(claude_plugins.ClaudePluginReloader):
        async def prepare(self) -> None:
            self.options["plugins"] = [{"type": "local", "path": str(source)}]
            await super().prepare()
            basics = pathlib.Path(self.options["plugins"][0]["path"]).parent / "agent-basics"
            (basics / ".claude-plugin").mkdir(parents=True)
            (basics / ".claude-plugin/plugin.json").write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
            (basics / "skills").mkdir()
            reloaders.append(self)

        def _inventory(self) -> list[claude_plugins._InstalledPlugin]:
            with patch.object(claude_plugins.process_tree, "run_with_timeout", inventory):
                return super()._inventory()

    class UnavailableObserver(ManualObserver):
        def start(self) -> None:
            raise OSError("fixture: monitor unavailable")

        def stop(self) -> None:
            stopped.append(True)

    async def control(payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
        control_calls.append(payload)
        mirror = pathlib.Path(reloaders[0].options["plugins"][0]["path"])
        reloaded.set()
        return {
            "plugins": [{"name": "agent-toolkit", "path": str(mirror), "version": "1.0.0"}, {"path": "builtin"}],
            "error_count": 0,
        }

    class Client(QueueClaudeClient):
        _query = SimpleNamespace(_send_control_request=control)

    client = Client(
        [
            [claude_agent_sdk.SystemMessage("init", {"session_id": "inventory-boundary"}), ResultMessage("初回完了")],
            [AssistantMessage("同じ接続の成果"), ResultMessage("後続完了")],
        ]
    )
    monkeypatch.setattr(claude_stream, "ClaudePluginReloader", Reloader)
    monkeypatch.setattr(
        claude_plugins.watchdog.observers, "Observer", UnavailableObserver if monitor_failure else ManualObserver
    )
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
        session = await backend.start("初回", str(tmp_path))
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        reloader = reloaders[0]
        mirror = pathlib.Path(reloader.options["plugins"][0]["path"])
        assert mirror.is_dir() and reloader._roots <= {source.resolve()}
        if monitor_failure:
            # 開始できなかった監視threadは停止の対象が無い。
            assert reloader._watch_keys is None and not stopped
        else:
            reloader.mark_changed()
            await asyncio.wait_for(reloaded.wait(), timeout=5)
        assert (await backend.send_message(session, "後続"))["delivery"] == "reply_started"
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        assert session.session_id == "inventory-boundary" and session.agent_message == "後続完了"
        assert len(client.queries) == 2
    assert len(inventory_calls) == (1 if monitor_failure else 2)
    assert len(control_calls) == (0 if monitor_failure else 1)
    assert client.disconnected and client.readers == 0
    assert not mirror.exists()


@pytest.mark.parametrize(
    ("failure", "disconnect_timeout"),
    [
        pytest.param("monitor-stop", False, id="monitor-stop"),
        *[
            pytest.param(failure, delayed, id=failure + ("-disconnect-timeout" if delayed else ""))
            for failure in ("inventory-unreclaimed", "inventory-unresolved", "inventory-unreaped-child")
            for delayed in (False, True)
        ],
    ],
)
@pytest.mark.asyncio
async def test_unconfirmed_cleanup_blocks_followup_and_reaches_owner_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    tmp_path: pathlib.Path,
    failure: str,
    disconnect_timeout: bool,
) -> None:
    """停止未確認の監視・一覧処理は次queryを止め、所有タスクの回収へ渡す。"""
    source = tmp_path / "checkout/agent-toolkit"
    for name in ("agent-toolkit", "agent-basics"):
        root = source.parent / name
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin/plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        (root / "skills").mkdir()
    reloaders: list[claude_plugins.ClaudePluginReloader] = []
    closed = asyncio.Event()
    disconnect_cancelled = asyncio.Event()
    stops: list[bool] = []
    # 一覧取得が回収できなかった子孫。所有タスクの後始末が同じ対象を引き取って回収することを確かめる。
    owned = contextlib.ExitStack()
    request.addfinalizer(owned.close)
    survivor = owned.enter_context(late_descendant.held_process(tmp_path))

    def reap_survivor() -> None:
        survivor.kill()
        survivor.wait()

    request.addfinalizer(reap_survivor)

    class HeldChild:
        """待機が時間切れのまま回収できない一覧取得の直接の子。zombie表示でpsutilの残存判定から外れる状態を表す。"""

        pid = survivor.pid
        args = ["claude", "plugin", "list", "--json"]
        released = False

        def wait(self, timeout: float | None = None) -> int:
            if not self.released:
                raise subprocess.TimeoutExpired(self.args, timeout or 0)
            return 0

        def kill(self) -> None:
            pass

        def poll(self) -> int | None:
            return 0 if self.released else None

    held_child = HeldChild()

    class Observer(ManualObserver):
        def stop(self) -> None:
            stops.append(True)
            if failure == "monitor-stop" and len(stops) == 1:
                raise OSError("監視を停止できない")

    class Reloader(claude_plugins.ClaudePluginReloader):
        async def prepare(self) -> None:
            self.options["plugins"] = [{"type": "local", "path": str(source)}]
            await super().prepare()
            reloaders.append(self)

        def _inventory(self) -> list[claude_plugins._InstalledPlugin]:
            def inventory(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                if failure == "inventory-unreclaimed" and reloaders:
                    # 時間上限後に子孫を回収できない結果だけが所有タスクの回収へ渡る。回収済みの時間上限は縮退する。
                    raise claude_plugins.process_tree.ReclaimError(
                        [f"{survivor.pid}: claude"], [psutil.Process(survivor.pid)], context="run_with_timeout"
                    )
                if failure == "inventory-unresolved" and reloaders:
                    # 既知の子孫が無くても、生成の停止を確認できない結果は所有タスクの再開抑止まで届く。
                    raise claude_plugins.process_tree.ReclaimError(
                        ["生成の停止を確認できない: scan failure"], [], context="run_with_timeout", unresolved=["scan failure"]
                    )
                if failure == "inventory-unreaped-child" and reloaders:
                    # 子孫が無く生成停止も確認済みでも、直接の子の回収待ちは所有タスクの再試行まで届く。
                    raise claude_plugins.process_tree.ReclaimError(
                        [f"{held_child.pid}: claude"], [], context="run_with_timeout", child=cast(Any, held_child)
                    )
                entries = [{"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(source)}]
                return subprocess.CompletedProcess(command, 0, json.dumps(entries), "")

            with patch.object(claude_plugins.process_tree, "run_with_timeout", inventory):
                return super()._inventory()

        def _reschedule(self) -> None:
            if failure == "monitor-stop":
                raise OSError("再監視を開始できない")
            super()._reschedule()

        async def close(self, *, retain_input: bool = False) -> None:
            await super().close(retain_input=retain_input)
            closed.set()

    async def control(_payload: dict[str, str], **_kwargs: Any) -> dict[str, Any]:
        return {"plugins": [{"name": "agent-toolkit", "path": str(source)}], "error_count": 0}

    class Client(QueueClaudeClient):
        _query = SimpleNamespace(_send_control_request=control)

        async def disconnect(self) -> None:
            await super().disconnect()
            if disconnect_timeout:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    disconnect_cancelled.set()
                    raise

    client = Client([[claude_agent_sdk.SystemMessage("init", {"session_id": "monitor-cleanup"}), ResultMessage("初回完了")]])
    monkeypatch.setattr(claude_plugins.watchdog.observers, "Observer", Observer)
    monkeypatch.setattr(claude_stream, "ClaudePluginReloader", Reloader)
    async with claude.ClaudeServerManager(client_factory=lambda _options: client) as backend:
        session = await backend.start("初回", str(tmp_path))
        async with backend._condition:
            await asyncio.wait_for(backend._condition.wait_for(lambda: session.result_available), timeout=5)
        owner = next(task for task in backend._tasks if backend._task_sessions.get(task) == session.session_id)
        reloaders[0].request_reload()
        with pytest.raises(claude.SessionOwnerGoneError):
            await backend.send_message(session, "後続")
        # queryの拒否は所有タスクの回収より先に返る。SDKの実際の5秒期限と子孫回収を待って検証する。
        await asyncio.wait_for(asyncio.gather(owner, return_exceptions=True), timeout=15)
        assert closed.is_set()
        assert disconnect_cancelled.is_set() == disconnect_timeout
        assert session.status == "failed" and len(client.queries) == 1
        assert stops == ([True, True] if failure == "monitor-stop" else [True])
        if failure == "inventory-unreclaimed":
            assert survivor.wait(timeout=10) is not None
            assert session.session_id not in backend._residual_processes
        if failure == "inventory-unresolved":
            # 未確認の理由は既知対象の回収では解消せず、同じ会話の再開前の停止で失敗し続ける。
            assert backend._reclaim_failures[session.session_id][0].unresolved == ["scan failure"]
            with pytest.raises(claude.session_errors.DelegateBackendError, match="scan failure"):
                await backend._stop_owned_task(session.session_id)
            backend._reclaim_failures.clear()
            backend._residual_processes.clear()
        if failure == "inventory-unreaped-child":
            # 直接の子の回収を確認できるまで再開前の停止は失敗し、回収できた後に所有状態を解く。
            with pytest.raises(claude.session_errors.DelegateBackendError, match="終了できなかった"):
                await backend._stop_owned_task(session.session_id)
            assert session.session_id in backend._residual_processes
            held_child.released = True
            await backend._stop_owned_task(session.session_id)
            assert session.session_id not in backend._residual_processes
            assert session.session_id not in backend._reclaim_failures
    assert client.disconnected and client.readers == 0
