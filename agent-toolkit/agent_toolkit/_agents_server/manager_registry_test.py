"""`_agents_server/manager_registry.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import dataclasses
import json
import logging
import pathlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

import agent_toolkit._agents_server.commands as atk_agents
from agent_toolkit import atk
from agent_toolkit._agents_server import (
    agents_wait,
    logging_config,
    manager_base,
    responses,
    resume_waits,
    retained_results,
    shared_layout,
    shared_roots,
    state,
    status_file,
    wait_targets,
)
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError
from agent_toolkit._testing.agents_server_support import (
    BlockingResumeBackend,
    FakeBackend,
    ThreadReadBackend,
    UnavailableStartBackend,
    _actionable_message,
    _complete,
    _install_backend,
    _manager_with_fake,
    _publish_orphaned_codex,
    _publish_recovered_session,
    _show_through_tool,
    _without_body_path,
    exited_process_identity,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("cache", ["stopped", "expired"])
@pytest.mark.parametrize("terminal_status", ["completed", "failed"])
@pytest.mark.asyncio
async def test_show_does_not_report_stale_terminal_after_resume_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    cache: str,
    terminal_status: str,
) -> None:
    """所有側が次turnへ進んだ後の照会・停止・回収は共有登録の現在状態に従う。"""
    session_id = "external-resume"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")
    writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root-session", "observer.json", None), state_root=tmp_path
    )
    owner_writer = status_file.StatusFileWriter(
        {}, shared_roots.StatusFileIdentity("root-session", "owner.json", None), state_root=tmp_path
    )
    async with (
        server_manager.AgentsServerManager(owner_writer) as owner,
        server_manager.AgentsServerManager(writer) as observer,
    ):
        previous = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
        _complete(previous, message="前turnの未回収結果")
        owner_writer.retain_result(previous)
        result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session_id}.json"
        previous_bytes = result_path.read_bytes()
        try:
            assert (await observer.show_session(session_id))["status"] == "completed"
            if cache == "expired":
                observer.expired_sessions[session_id] = observer.stopped_sessions.pop(session_id)

            # 共有登録の生成側だけを進め、照会側の復元済みメモリーはそのまま残す。
            current = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=3)
            owner.sessions[session_id] = current
            session_registry.publish(session_id, terminal=False, cwd=str(tmp_path), turn_seq=3, status="running")
            assert session_registry.resolve(session_id).state is session_registry.Resolution.RUNNING
            with pytest.raises(ValueError, match="turn_unobserved"):
                (await observer.show_session(session_id))
            assert not (await observer.list_sessions(include_terminated=True))["sessions"]
            for operation in (
                observer.stop(session_id),
                observer.kill(session_id, timeout=0),
                observer.send_message(session_id, "続行"),
                observer.resolve_handoff_predecessor(session_id),
            ):
                with pytest.raises(ValueError, match="turn_unobserved"):
                    await operation
            assert result_path.read_bytes() == previous_bytes

            session_registry.publish(
                session_id, terminal=True, cwd=str(tmp_path), turn_seq=3, status=cast(Any, terminal_status)
            )
            shown = await observer.show_session(session_id, verbose=True)
            assert shown["turn_seq"] == 3
            assert shown["status"] == terminal_status
            assert shown["result_available"] is False
            assert result_path.read_bytes() == previous_bytes
            assert (await observer.list_sessions(include_terminated=True))["sessions"][0]["status"] == terminal_status

            _complete(current, message="現在turnの結果", error="失敗" if terminal_status == "failed" else None)
            owner_writer.retain_result(current)
            assert (await observer.show_session(session_id))["result_available"] is True
            result = await observer.kill(session_id, timeout=0)
            assert result["agent_message"] == "現在turnの結果"
            assert result["status"] == terminal_status
        finally:
            owner.sessions.clear()
            await observer.close()
            await owner.close()


@pytest.mark.asyncio
async def test_list_sessions_projects_all_retention_states_in_start_order(tmp_path: pathlib.Path) -> None:
    """active、再開中および期限切れのsessionを同じ項目集合で開始順に返す。"""
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        active = state.SessionState(
            "duplicate",
            str(tmp_path),
            model="active-model",
            effort="medium",
            engine="codex",
            model_type="execute",
            label="active",
            started_at="2026-09-06T00:00:02+00:00",
            updated_at="2026-09-06T00:00:05+00:00",
        )
        active.set_progress("実行中")
        manager.sessions[active.session_id] = active
        expired = state.SessionResumeState(
            session_id="expired",
            cwd=str(tmp_path),
            model="expired-model",
            effort="high",
            engine="claude",
            model_type="plan",
            launch_kind="delegate",
            label="expired",
            started_at="2026-09-06T00:00:01+00:00",
            updated_at="2026-09-06T00:00:04+00:00",
        )
        manager.expired_sessions[expired.session_id] = expired
        pending_state = state.SessionResumeState(
            session_id="pending",
            cwd=str(tmp_path),
            model="pending-model",
            effort="low",
            engine="codex",
            model_type="execute_fast",
            launch_kind="explore",
            label="pending",
            started_at="2026-09-06T00:00:03+00:00",
            updated_at="2026-09-06T00:00:06+00:00",
        )

        async def pending_session() -> state.SessionState:
            await asyncio.Future()
            raise AssertionError("unreachable")

        task = asyncio.create_task(pending_session())
        manager._pending_resumes[pending_state.session_id] = manager_base.PendingResume(
            state=pending_state,
            task=task,
            prompt=state.ResumePrompt("続行"),
        )
        manager.expired_sessions[active.session_id] = state.SessionResumeState.from_session(active)
        try:
            response = await manager.list_sessions(include_terminated=True)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert [session["session_id"] for session in response["sessions"]] == ["expired", "duplicate", "pending"]
        assert all(
            {"session_id", "status"}
            <= set(session)
            <= {"session_id", "status", "started_at", "updated_at", "seconds_since_activity"}
            for session in response["sessions"]
        )
        assert response["sessions"][0]["status"] == "expired"
        assert response["sessions"][1]["status"] == "running"
        assert response["sessions"][2]["status"] == "running"
        assert "omitted" not in response


@pytest.mark.asyncio
async def test_list_sessions_omits_terminated_sessions_without_pending_result(
    tmp_path: pathlib.Path,
) -> None:
    """表示範囲を指定しない場合は未回収結果を持たない終端sessionだけを除く。"""
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        active = state.SessionState("active", str(tmp_path))
        manager.sessions[active.session_id] = active
        completed = state.SessionState("completed", str(tmp_path))
        completed.status = "completed"
        manager.sessions[completed.session_id] = completed
        pending_expired = state.SessionState("pending-expired", str(tmp_path))
        _complete(pending_expired)
        manager.expired_sessions[pending_expired.session_id] = state.SessionResumeState.from_session(pending_expired)
        delivered_expired = state.SessionResumeState(
            session_id="delivered-expired",
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="codex",
            status="completed",
            finalized_at="2026-09-06T00:00:00+00:00",
            result_delivered=True,
        )
        manager.expired_sessions[delivered_expired.session_id] = delivered_expired

        response = await manager.list_sessions()
        assert {session["session_id"] for session in response["sessions"]} == {
            "active",
            "pending-expired",
        }
        assert response["omitted"] == 2
        all_sessions = await manager.list_sessions(include_terminated=True)
        assert len(all_sessions["sessions"]) == 4
        assert "omitted" not in all_sessions


@pytest.mark.asyncio
async def test_list_sessions_omits_labels(tmp_path: pathlib.Path) -> None:
    """一覧は活動時刻を返し、長い識別名などの詳細を返さない。"""
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        long_label = state.SessionState("long", str(tmp_path), label="a" * 101)
        exact_label = state.SessionState("exact", str(tmp_path), label="b" * 100)
        manager.sessions = {long_label.session_id: long_label, exact_label.session_id: exact_label}

        listed = (await manager.list_sessions())["sessions"]

        assert [item["session_id"] for item in listed] == ["long", "exact"]
        assert all(
            {"session_id", "status"}
            <= item.keys()
            <= {"session_id", "status", "started_at", "updated_at", "seconds_since_activity"}
            for item in listed
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("fast_mode", [True, False, None])
async def test_fallback_start_and_verbose_show_publish_codex_speed(
    fast_mode: bool | None, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """候補切替の起動応答と詳細showが送信した速度を返し、通常の一覧は既存の項目を保つ。"""
    async with server_manager.AgentsServerManager() as manager:
        monkeypatch.setattr(
            _atk_config,
            "parse_unresolved_model_candidates",
            lambda _kind, **_kwargs: [
                ("claude", "first", "high"),
                ("codex", "second", "medium"),
            ],
        )
        _install_backend(manager, "claude", UnavailableStartBackend(manager.sessions, "claude"))
        backend = FakeBackend(manager.sessions, "codex")
        original_start = backend.start

        async def start_with_speed(*args: Any, **kwargs: Any) -> state.SessionState:
            session = await original_start(*args, **kwargs)
            session.fast_mode = fast_mode
            return session

        monkeypatch.setattr(backend, "start", start_with_speed)
        _install_backend(manager, "codex", backend)
        raw = await manager.start("plan", "調査", str(tmp_path))
        public = responses.public_start_response(raw)
        shown = await manager.show_session(raw["session_id"], verbose=True)
        assert public["engine"] == "codex"
        if fast_mode is None:
            assert "fast_mode" not in public
            assert "fast_mode" not in shown
        else:
            assert public["fast_mode"] is fast_mode
            assert shown["fast_mode"] is fast_mode
        assert "fast_mode" not in (await manager.show_session(raw["session_id"]))
        await manager.close()


@pytest.mark.parametrize("engine", ["claude", "agy"])
@pytest.mark.asyncio
async def test_verbose_show_omits_speed_for_other_engines(engine: str, tmp_path: pathlib.Path) -> None:
    """他engineの構造化出力はCodexの速度項目を持たない。"""
    async with server_manager.AgentsServerManager() as manager:
        session = state.SessionState("other-speed", str(tmp_path), engine=engine, fast_mode=True)
        manager.sessions[session.session_id] = session
        assert "fast_mode" not in (await manager.show_session(session.session_id, verbose=True))


@pytest.mark.asyncio
async def test_show_reports_activity_and_output_elapsed_with_activity_based_stall(tmp_path: pathlib.Path) -> None:
    """showは活動時刻とテキスト出力時刻を別項目で返し、停滞の印を活動の経過だけで決める。"""
    async with _manager_with_fake("codex") as (manager, _):
        fresh = state.SessionState("thread-fresh", str(tmp_path), engine="codex")
        text_silent = state.SessionState("thread-text-silent", str(tmp_path), engine="codex")
        text_silent.output_updated_at = "2000-01-01T00:00:00+00:00"
        inactive = state.SessionState("thread-inactive", str(tmp_path), engine="codex")
        inactive.updated_at = "2000-01-01T00:00:00+00:00"
        manager.sessions.update(
            {
                fresh.session_id: fresh,
                text_silent.session_id: text_silent,
                inactive.session_id: inactive,
            }
        )

        fresh_detail = await manager.show_session(fresh.session_id)
        text_silent_detail = await manager.show_session(text_silent.session_id)
        inactive_detail = await manager.show_session(inactive.session_id)

        assert {"updated_at", "output_updated_at", "seconds_since_output"}.isdisjoint(fresh_detail)
        assert isinstance(fresh_detail["seconds_since_activity"], int)
        # テキスト出力だけが閾値を超えて止まっている状態と、活動そのものが止まった状態を経過秒数で区別できる。
        assert {"updated_at", "output_updated_at", "seconds_since_output"}.isdisjoint(text_silent_detail)
        diagnostic = await manager.show_session(text_silent.session_id, verbose=True)
        assert diagnostic["output_updated_at"] == text_silent.output_updated_at
        assert diagnostic["updated_at"] == text_silent.updated_at
        assert text_silent_detail["seconds_since_activity"] < state.STALL_NOTICE_SECONDS
        assert inactive_detail["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS

        response = await manager.wait()

        assert {
            "output_updated_at",
            "seconds_since_output",
            "updated_at",
            "seconds_since_activity",
        }.isdisjoint(response)


@pytest.mark.asyncio
async def test_show_returns_error_of_terminal_session(tmp_path: pathlib.Path) -> None:
    """保持中・期限切れ・破棄後の終端sessionが空でない`error`を持つ場合だけ、`show`が`error`を返す。

    `show`が`error`を返さないと、結果を回収できなかった委譲元は失敗の原因を確かめられず、
    利用上限で失敗したsessionへ継続を送り直す。
    """
    async with _manager_with_fake("codex") as (manager, _):
        usage_limit = {"codexErrorInfo": "usageLimitExceeded", "message": "Your workspace is out of credits."}
        failed = state.SessionState("failed-held", str(tmp_path), engine="codex")
        _complete(failed, message="", error=usage_limit)
        manager.sessions[failed.session_id] = failed
        completed = state.SessionState("completed-held", str(tmp_path), engine="codex")
        _complete(completed)
        manager.sessions[completed.session_id] = completed
        running = state.SessionState("running-held", str(tmp_path), engine="codex")
        running.error = {"message": "前のturnの失敗"}
        manager.sessions[running.session_id] = running
        manager.expired_sessions["failed-expired"] = state.SessionResumeState(
            session_id="failed-expired",
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="claude",
            status="failed",
            error="API Error",
        )
        manager.stopped_sessions["failed-stopped"] = state.SessionResumeState(
            session_id="failed-stopped",
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="codex",
            status="failed",
            error=usage_limit,
        )
        manager.stopped_sessions["completed-stopped"] = state.SessionResumeState(
            session_id="completed-stopped",
            cwd=str(tmp_path),
            model=None,
            effort=None,
            engine="codex",
            status="completed",
            error={},
        )

        assert (await manager.show_session("failed-held"))["error"] == usage_limit
        assert (await manager.show_session("failed-expired"))["error"] == "API Error"
        assert (await manager.show_session("failed-stopped", verbose=True))["error"] == usage_limit
        for session_id in ("completed-held", "running-held", "completed-stopped"):
            assert "error" not in (await manager.show_session(session_id))


@pytest.mark.asyncio
async def test_claude_api_error_is_visible_in_show_and_list(tmp_path: pathlib.Path) -> None:
    """ClaudeのAPI失敗を両方の公開手段で示し、再試行と回復を区別できる。"""
    async with _manager_with_fake("claude") as (manager, _):
        session = state.SessionState("claude-session", str(tmp_path), engine="claude")
        session.updated_at = "2000-01-01T00:00:00+00:00"
        manager.sessions[session.session_id] = session
        failure = SimpleNamespace(content=[SimpleNamespace(text="API Error: 429 rate limit")], error="rate_limit")

        claude_backend.consume_assistant_message(session, failure)
        claude_backend.consume_assistant_message(session, failure)
        shown = await manager.show_session(session.session_id)
        listed = (await manager.list_sessions())["sessions"][0]

        assert shown["api_error"]["type"] == "rate_limit_error"
        assert shown["api_error"]["http_status"] == 429
        assert {"count", "first_at"}.isdisjoint(shown["api_error"])
        assert session.api_error is not None and session.api_error["count"] == 2
        assert shown["api_error"]["elapsed_seconds"] >= 0
        assert listed["api_error"] == shown["api_error"]
        assert shown["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS
        assert listed["seconds_since_activity"] >= state.STALL_NOTICE_SECONDS

        claude_backend.consume_assistant_message(session, SimpleNamespace(content=[], error=None))

        assert "api_error" not in (await manager.show_session(session.session_id))
        assert "api_error" not in (await manager.list_sessions())["sessions"][0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("updated_at", "expected_stalled"),
    [("2000-01-01T00:00:00+00:00", True), ("2099-01-01T00:00:00+00:00", False)],
    ids=["inactive", "active"],
)
async def test_four_observation_paths_share_the_same_activity_projection(
    tmp_path: pathlib.Path,
    updated_at: str,
    expected_stalled: bool,
) -> None:
    """`show`・`list`・待機CLI・`atk agents list`が、同じ入力へ同じ経過秒数を返す。

    いずれかの呼び出し手段が共通の射影から外れて独自の判定入力へ戻る退行を検出する。
    停滞の判定は委譲元が経過秒数と閾値の比較で行うため、4つの呼び出し手段が返す経過秒数で同じ判定が得られることを確認する。
    """
    started_at = "2000-01-01T00:00:00+00:00"
    output_updated_at = "2000-01-01T00:00:00+00:00"
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        session.started_at = started_at
        session.output_updated_at = output_updated_at
        session.updated_at = updated_at
        session.status = "running"
        manager.sessions[session.session_id] = session
        serialized = {
            "session_id": session.session_id,
            "status": "running",
            "started_at": started_at,
            "output_updated_at": output_updated_at,
            "updated_at": updated_at,
        }
        status_path = tmp_path / "root.json"
        status_path.write_text(
            json.dumps({"version": 1, "sessions": [dict(serialized)]}, ensure_ascii=False),
            encoding="utf-8",
        )
        listed_by_cli = dict(serialized)
        atk_agents._add_output_activity(listed_by_cli)  # pylint: disable=protected-access  # noqa: SLF001

        threshold = state.STALL_NOTICE_SECONDS

        def _stalled(payload: dict[str, Any]) -> bool:
            return cast(float, payload["seconds_since_activity"]) >= threshold

        flags = [
            _stalled(await manager.show_session(session.session_id)),
            _stalled((await manager.list_sessions())["sessions"][0]),
            _stalled(
                agents_wait._session_output_activity(  # pylint: disable=protected-access  # noqa: SLF001
                    [status_path],
                    session.session_id,
                )
            ),
            _stalled(listed_by_cli),
        ]

        assert flags == [expected_stalled] * 4


@pytest.mark.asyncio
async def test_show_reports_active_tool_uses_with_input_detail(tmp_path: pathlib.Path) -> None:
    """showは未完了のツール呼び出しをツール名、開始時刻および入力の要約で返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        running = state.SessionState("thread-running", str(tmp_path), engine="claude")
        running.pending_tool_uses["toolu_1"] = ("Bash", "2026-09-15T00:00:01+00:00", "command=git status")
        idle = state.SessionState("thread-idle", str(tmp_path), engine="claude")
        terminal = state.SessionState("thread-terminal", str(tmp_path), engine="claude")
        terminal.pending_tool_uses["toolu_2"] = ("Read", "2026-09-15T00:00:02+00:00", "file_path=/tmp/a.py")
        _complete(terminal, message="完了")
        codex_running = state.SessionState("thread-codex", str(tmp_path), engine="codex")
        codex_running.record_current_item_start({"type": "commandExecution", "id": "item-1", "command": "git status"})
        manager.sessions.update(
            {
                running.session_id: running,
                idle.session_id: idle,
                terminal.session_id: terminal,
                codex_running.session_id: codex_running,
            }
        )

        running_detail = await manager.show_session(running.session_id)
        codex_detail = await manager.show_session(codex_running.session_id)

        assert running_detail["active_tool_uses"] == [
            {"name": "Bash", "started_at": "2026-09-15T00:00:01+00:00", "detail": "command=git status"}
        ]
        assert codex_detail["active_tool_uses"][0]["type"] == "commandExecution"
        assert "id" not in codex_detail["active_tool_uses"][0]
        assert codex_detail["active_tool_uses"][0]["detail"] == "command=git status"
        assert "active_tool_uses" not in (await manager.show_session(idle.session_id))
        assert "active_tool_uses" not in (await manager.show_session(terminal.session_id))


@pytest.mark.asyncio
async def test_show_reports_sorted_live_child_sessions_only_for_running_parent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """showは稼働中の親に実在する子識別子がある場合だけ安定順で返す。"""
    _publish_recovered_session(monkeypatch, tmp_path, "child-a", "completed")
    _publish_recovered_session(monkeypatch, tmp_path, "child-b", "completed")
    async with _manager_with_fake("codex") as (manager, _):
        parent = state.SessionState("parent", str(tmp_path), engine="codex")
        parent.live_child_session_ids.update({"child-b", "child-a", "child-unknown", "child!invalid"})
        no_child = state.SessionState("no-child", str(tmp_path), engine="codex")
        terminal = state.SessionState("terminal", str(tmp_path), engine="codex")
        terminal.live_child_session_ids.add("child-terminal")
        _complete(terminal, message="完了")
        manager.sessions.update(
            {
                parent.session_id: parent,
                no_child.session_id: no_child,
                terminal.session_id: terminal,
            }
        )

        parent_detail = await manager.show_session(parent.session_id)

        assert parent_detail["live_child_sessions"] == [
            {"session_id": "child-a", "cwd": str(tmp_path)},
            {"session_id": "child-b", "cwd": str(tmp_path)},
        ]
        assert parent_detail["live_child_session_ids_without_cwd"] == ["child!invalid", "child-unknown"]
        assert "live_child_sessions" not in (await manager.show_session(no_child.session_id))
        assert "live_child_sessions" not in (await manager.show_session(terminal.session_id))
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_restores_persisted_result_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再起動後の最初の待機だけが保存済みの終端結果本文を返す。"""
    session_id = "recovered-wait"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "failed")
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    persisted = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
    _complete(persisted, message="永続結果", error={"message": "失敗結果"})
    writer.retain_result(persisted)
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session_id}.json"
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)

        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 0
        )
        restored = _without_body_path(json.loads(capsys.readouterr().out))
        second = await manager.kill(session_id, timeout=0)

        assert "`send_message`" in restored.pop("next_action")

        assert restored == {
            "session_id": session_id,
            "status": "failed",
            "agent_message": "永続結果",
            "error": {"message": "失敗結果"},
            "engine": "codex",
            "model": None,
            "effort": None,
            "model_type": None,
        }
        assert second == {"status": "failed", "recovery": "result_unavailable", "kill_requested": False}
        assert not result_path.exists()
        assert backend.send_calls == 0
        assert not backend.resume_calls
        assert not backend.release_calls
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_marks_persisted_result_as_uncollected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿からの復元時は結果ファイルの存在を回収状態へ反映する。"""
    session_id = "recovered-result"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "current.json", None),
        state_root=tmp_path,
    )
    persisted = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
    _complete(persisted, message="永続結果")
    writer.retain_result(persisted)
    async with server_manager.AgentsServerManager(writer) as manager:
        resume_state = await manager._restore_registry_session(session_id)

        assert resume_state is not None
        assert resume_state.result_delivered is False
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_kill_reports_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿から復元した終端sessionのkillはbackendへ触れない。"""
    _publish_recovered_session(monkeypatch, tmp_path, "recovered-kill", "failed")
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)

        response = await manager.kill("recovered-kill", timeout=0)

        assert response == {
            "status": "failed",
            "recovery": "result_unavailable",
            "kill_requested": False,
        }
        assert backend.interrupt_calls == 0
        assert not backend.release_calls
        await manager.close()


@pytest.mark.asyncio
async def test_recovered_session_stop_discards_previous_process_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """復元したsessionの明示破棄は前プロセスが公開した結果も削除する。"""
    session_id = "recovered-stop"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")
    previous_writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "previous.json", None),
        state_root=tmp_path,
    )
    previous_session = state.SessionState(session_id, str(tmp_path), engine="codex", turn_seq=2)
    _complete(previous_session, message="前プロセスの結果")
    previous_writer.retain_result(previous_session)
    result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session_id}.json"
    previous_result = result_path.read_bytes()

    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "current.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)

        assert await manager.stop(session_id, retain_result=True) == {}
        assert result_path.read_bytes() == previous_result

        assert await manager.stop(session_id) == {}
        assert not result_path.exists()

        assert await manager.stop(session_id) == {}
        assert not result_path.exists()

        assert not backend.release_calls
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_status", ["completed", "failed", "interrupted"])
async def test_recovered_session_restores_each_terminal_status(
    terminal_status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿に記録した各終端種別を復元応答へ維持する。"""
    session_id = f"recovered-{terminal_status}"
    _publish_recovered_session(monkeypatch, tmp_path, session_id, terminal_status)
    async with server_manager.AgentsServerManager(status_writer=None) as manager:
        response = await manager.kill(session_id, timeout=0)

        assert response == {"status": terminal_status, "recovery": "result_unavailable", "kill_requested": False}
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("engine", "model_type"), (("codex", None), ("claude", "execute_fast")))
async def test_stop_discards_terminal_session(
    engine: str,
    model_type: str | None,
    tmp_path: pathlib.Path,
) -> None:
    """終端済みsessionを一覧と通常保持先から除き、backend資源を解放する。"""
    async with _manager_with_fake(engine) as (manager, backend):
        session = state.SessionState("terminal", str(tmp_path), engine=engine, model_type=model_type, turn_seq=3)
        _complete(session)
        manager.sessions[session.session_id] = session

        response = await manager.stop(session.session_id)

        assert response == {}
        listed = await manager.list_sessions()
        assert listed["sessions"] == []
        assert "omitted" not in listed
        assert "terminal" not in manager.sessions
        assert "terminal" not in manager.expired_sessions
        assert manager.stopped_sessions["terminal"].session_id == "terminal"
        assert backend.release_calls == ["terminal"]
        assert await manager.wait() == {"status": "expired"}


@pytest.mark.asyncio
async def test_stop_removes_status_file_projection_and_retained_result(
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """破棄したsessionをstatusLineの射影と終端結果ファイルから除く。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        session = state.SessionState("visible", str(tmp_path), engine="codex", announced=True)
        _complete(session)
        manager.sessions[session.session_id] = session
        writer.flush()
        result_path = shared_layout.results_directory("root-session", tmp_path) / "visible.json"
        assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"][0]["session_id"] == "visible"
        assert result_path.exists()

        with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.status-file"):
            await manager.stop(session.session_id)
        writer.flush()

        assert json.loads(writer.path.read_text(encoding="utf-8"))["sessions"] == []
        assert not result_path.exists()
        assert "result_deleted session_id=visible writer=root.json collector=stop" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize(("operation", "collector"), (("kill", "kill"), ("send_message", "send-message")))
async def test_result_deletion_logs_actual_mcp_actor(
    operation: str,
    collector: str,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """killとsend_messageによる結果削除をMCP待機へ誤分類しない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        session = state.SessionState("terminal", str(tmp_path), engine="codex")
        _complete(session)
        manager.sessions[session.session_id] = session
        writer.flush()

        with caplog.at_level(logging.INFO, logger="agent-toolkit.agents-server.status-file"):
            if operation == "kill":
                await manager.kill(session.session_id, timeout=0)
            else:
                await manager.send_message(session.session_id, "続行")

        assert f"result_deleted session_id=terminal writer=root.json collector={collector}" in caplog.text
        assert "collector=mcp-wait" not in caplog.text
        await manager.close()


@pytest.mark.asyncio
async def test_stop_releases_wait_target_before_waiting_for_new_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """結果を破棄するstopは旧待機対象を解放し、後続sessionの結果待機を妨げない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        writer.activate()
        old_session = state.SessionState("old-session", str(tmp_path), engine="codex", announced=True)
        manager.sessions[old_session.session_id] = old_session
        writer.flush()
        monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 0)

        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 3
        )
        running_response = json.loads(capsys.readouterr().out)
        assert running_response["session_id"] == "old-session"
        assert running_response["status"] == "running"
        _complete(old_session)
        writer.flush()

        await manager.stop(old_session.session_id)
        writer.flush()

        retained, error = wait_targets.read_wait_targets("root-session", "root.json", tmp_path)
        assert retained == set()
        assert error is None
        new_session = state.SessionState("new-session", str(tmp_path), engine="codex", announced=True)
        _complete(new_session, message="新しい結果")
        manager.sessions[new_session.session_id] = new_session
        writer.flush()

        assert (
            agents_wait.wait_for_result(
                environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
                state_root=tmp_path,
            )
            == 0
        )
        assert _without_body_path(json.loads(capsys.readouterr().out)) == {
            "session_id": "new-session",
            "status": "completed",
            "agent_message": "新しい結果",
            "engine": "codex",
            "model": None,
            "effort": None,
            "model_type": None,
        }
        await manager.close()


@pytest.mark.asyncio
async def test_stop_republishes_result_after_retain_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果公開の失敗後は解放を重ねず同じ本文だけを再公開する。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)
        session = state.SessionState("retain-retry", str(tmp_path), engine="codex", turn_seq=4)
        _complete(session, message="保持する結果")
        manager.sessions[session.session_id] = session
        original_retain = writer.retain_result_async
        retain_calls = 0

        async def fail_once(resume_state: state.SessionResumeState) -> None:
            nonlocal retain_calls
            retain_calls += 1
            if retain_calls == 1:
                raise OSError("retain failed once")
            await original_retain(resume_state)

        monkeypatch.setattr(writer, "retain_result_async", fail_once)

        with pytest.raises(
            RuntimeError,
            match="backend resources released; state synchronization incomplete.*retain failed once",
        ):
            await manager.stop(session.session_id, retain_result=True)
        await manager.stop(session.session_id, retain_result=True)

        result_path = shared_layout.results_directory("root-session", tmp_path) / f"{session.session_id}.json"
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        assert payload["agent_message"] == "保持する結果"
        assert payload["turn_seq"] == 4
        assert retain_calls == 2
        assert backend.release_calls == [session.session_id]
        await manager.close()


@pytest.mark.asyncio
async def test_stop_does_not_release_twice_after_state_sync_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """状態同期の再発行では成功済みのbackend解放を繰り返さない。"""
    writer = status_file.StatusFileWriter(
        {},
        shared_roots.StatusFileIdentity("root-session", "root.json", None),
        state_root=tmp_path,
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        _install_backend(manager, "codex", backend)
        session = state.SessionState("schedule-retry", str(tmp_path), engine="codex")
        _complete(session, message="保持する結果")
        manager.sessions[session.session_id] = session
        original_schedule = writer.schedule
        schedule_calls = 0

        def fail_once() -> None:
            nonlocal schedule_calls
            schedule_calls += 1
            if schedule_calls == 1:
                raise OSError("schedule failed once")
            original_schedule()

        monkeypatch.setattr(writer, "schedule", fail_once)

        with pytest.raises(
            RuntimeError,
            match="backend resources released; state synchronization incomplete.*schedule failed once",
        ) as raised:
            await manager.stop(session.session_id, retain_result=True)
        next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
        assert "`stop`" in next_action
        assert "`list`" in next_action
        await manager.stop(session.session_id, retain_result=True)

        assert schedule_calls == 2
        assert backend.release_calls == [session.session_id]
        assert writer.result_state(session.session_id) == "published"
        await manager.close()


@pytest.mark.asyncio
async def test_stop_discards_expired_session(tmp_path: pathlib.Path) -> None:
    """期限切れ保持先のsessionも破棄し、一覧非表示の再開状態へ移す。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("expired-stop", str(tmp_path), engine="codex")
        _complete(session)
        manager.expired_sessions[session.session_id] = state.SessionResumeState.from_session(session)

        response = await manager.stop(session.session_id)

        assert response == {}
        assert session.session_id not in manager.expired_sessions
        assert session.session_id in manager.stopped_sessions
        listed = await manager.list_sessions()
        assert listed["sessions"] == []
        assert "omitted" not in listed
        # expiredへ移した時点で資源は解放済みなので、stopは再開状態の移動だけを行う。
        assert not backend.release_calls


@pytest.mark.parametrize("with_writer", [False, True])
@pytest.mark.asyncio
async def test_stop_reports_registry_failure_after_releasing_resources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, with_writer: bool
) -> None:
    """登録簿の保存失敗でも解放済みを明示し、再発行は状態の同期だけを行う。"""
    writer = (
        status_file.StatusFileWriter(
            {}, shared_roots.StatusFileIdentity("root-session", "root.json", None), state_root=tmp_path
        )
        if with_writer
        else None
    )
    async with server_manager.AgentsServerManager(writer) as manager:
        backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", backend)
        session = state.SessionState("registry-retry", str(tmp_path), engine="codex")
        _complete(session)
        manager.sessions[session.session_id] = session
        release = session_registry.release
        calls = 0

        def fail_once(*args: Any, **kwargs: Any) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError("registry failed once")
            release(*args, **kwargs)

        monkeypatch.setattr(session_registry, "release", fail_once)
        with pytest.raises(RuntimeError, match="backend resources released; state synchronization incomplete") as caught:
            await manager.stop(session.session_id)
        assert "`stop`" in _actionable_message(caught.value)
        assert isinstance(caught.value.__cause__, OSError)
        await manager.stop(session.session_id)
        assert calls == 2
        assert backend.release_calls == [session.session_id]
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("registry_content", "code", "operation"),
    [
        (None, "turn_unobserved", "新しいstartでやり直さない"),
        ("{", "unreadable", "atk agents wait"),
    ],
    ids=["running-elsewhere", "unreadable"],
)
async def test_unrecoverable_registry_record_reports_next_action(
    registry_content: str | None,
    code: str,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再開できない登録簿の状態は、`error.recovery`の値に加えて、取るべき操作を次の操作の行で示す。

    `error.recovery`の値だけを返すと、委譲元は別の主体が実行中のsessionを新しいstartでやり直し得る。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_id = "0ba2f3f4-3e6c-4a1a-9a35-9f5e30b9f9b1"
    session_registry.publish(session_id, terminal=False, engine="codex", cwd=str(tmp_path))
    if registry_content is not None:
        (session_registry.registry_directory() / f"{session_id}.json").write_text(registry_content, encoding="utf-8")
    async with _manager_with_fake("codex") as (manager, backend):
        with pytest.raises(ValueError, match=f"error.recovery={code}") as raised:
            await manager.send_message(session_id, "続行")

        assert operation in _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
        assert not backend.resume_calls
        await manager.close()


@pytest.mark.asyncio
async def test_stop_rejects_running_session(tmp_path: pathlib.Path) -> None:
    """実行中turnは中断も破棄もせず、killの先行を要求する。"""
    async with _manager_with_fake("codex") as (manager, backend):
        session = state.SessionState("running", str(tmp_path), engine="codex")
        manager.sessions[session.session_id] = session

        with pytest.raises(ValueError, match="session is running: running") as exc_info:
            await manager.stop(session.session_id)
        assert "`kill`" in _actionable_message(exc_info.value)

        assert manager.sessions[session.session_id] is session
        assert backend.interrupt_calls == 0
        assert not backend.release_calls


@pytest.mark.asyncio
async def test_stop_rejects_pending_resume(tmp_path: pathlib.Path) -> None:
    """進行中の再開を実行中として拒否する。"""
    async with _manager_with_fake("codex") as (manager, _):
        resume_state = state.SessionResumeState.from_session(state.SessionState("pending", str(tmp_path)))
        manager.expired_sessions[resume_state.session_id] = resume_state
        blocker = BlockingResumeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", blocker)
        send_task = asyncio.create_task(manager.send_message(resume_state.session_id, "再開", timeout=1))
        await blocker.resume_started.wait()

        with pytest.raises(ValueError, match="session is running: pending") as exc_info:
            await manager.stop(resume_state.session_id)
        assert "`kill`" in _actionable_message(exc_info.value)

        assert not blocker.release_calls
        blocker.release_resume.set()
        await send_task
        await manager.close()


@pytest.mark.asyncio
async def test_stop_rejects_unknown_session() -> None:
    """保持していないsession IDは既存の未解決診断を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "3468feae-b2bf-4d67-ac55-3c40207e8b5b"

        with pytest.raises(ValueError, match=f"unknown session: {session_id}"):
            await manager.stop(session_id)


@pytest.mark.asyncio
async def test_show_reports_another_writer_for_running_registry_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿が実行中として保持する識別子は、別主体の実行中として案内する。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "0ba2f3f4-3e6c-4a1a-9a35-9f5e30b9f9b1"
        monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
        session_registry.publish(session_id, terminal=False, engine="codex", cwd=str(tmp_path))

        with pytest.raises(ValueError) as excinfo:
            (await manager.show_session(session_id))

        message = _actionable_message(excinfo.value)
        assert "may have restarted" not in message
        assert "another writer" in message
        assert "atk agents wait" in message
        await manager.close()


@pytest.mark.asyncio
async def test_show_keeps_lost_session_diagnosis_without_registry_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿にレコードが無い識別子では従来の喪失の案内を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "5c9c2ec4-08f0-4a1e-9a02-9e8f0a4a4f21"
        monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

        with pytest.raises(ValueError, match=f"unknown session: {session_id}"):
            (await manager.show_session(session_id))

        await manager.close()


@pytest.mark.asyncio
async def test_show_returns_terminal_session_restored_from_registry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """登録簿から復元できた終端sessionは通常の応答として返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "9f2a37f4-40f8-4d38-9c1b-1f6f9f52a1c3"
        _publish_recovered_session(monkeypatch, tmp_path, session_id, "completed")

        response = await manager.show_session(session_id)

        assert response["session_id"] == session_id
        assert response["status"] == "completed"
        await manager.close()


@pytest.mark.asyncio
async def test_other_manager_shows_saved_activity_and_lists_unknown_legacy_time(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別Managerが復元したsessionの一覧・詳細は保存時刻を使い、旧記録を照会時刻で補わない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with _manager_with_fake("codex") as (owner, _):
        session = state.SessionState("saved-activity", str(tmp_path), engine="codex", publish_registry=True)
        session.started_at = "2026-09-27T14:38:32+00:00"
        session.status = "completed"
        session.turn_completed = True
        session.touch()
        owner.sessions[session.session_id] = session
        await owner.close()
        session_registry.publish("legacy-activity", terminal=True, cwd=str(tmp_path))
        async with _manager_with_fake("codex") as (observer, _):
            saved = await observer.show_session(session.session_id, verbose=True)
            legacy = await observer.show_session("legacy-activity", verbose=True)
            listed = {item["session_id"]: item for item in (await observer.list_sessions(include_terminated=True))["sessions"]}

            assert saved["started_at"] == session.started_at
            assert saved["updated_at"] == session.updated_at
            assert legacy["started_at"] is None
            assert legacy["updated_at"] is None
            assert {"started_at", "updated_at", "output_updated_at"}.isdisjoint(listed[session.session_id])
            assert "updated_at" not in listed["legacy-activity"]
            await observer.close()
            await owner.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["wait", "kill"])
async def test_stopped_result_remains_available_after_retention_deadline(
    operation: str,
    tmp_path: pathlib.Path,
) -> None:
    """stop=trueで保持した結果は期限到来後も最初の観測へ本文を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        session = state.SessionState(f"stopped-{operation}", str(tmp_path), engine="codex")
        _complete(session, message="保持結果")
        manager.sessions[session.session_id] = session
        await manager.kill(session.session_id, timeout=0, stop=True)
        retained = manager.stopped_sessions[session.session_id]
        assert retained.retention_deadline == session.retention_deadline
        manager.stopped_sessions[session.session_id] = dataclasses.replace(
            retained,
            retention_deadline=asyncio.get_running_loop().time() - 1,
        )

        if operation == "wait":
            response = await manager.wait()
            assert response["status"] == "completed"
            assert response["agent_message"] == "保持結果"
        else:
            response = await manager.kill(session.session_id, timeout=0)
            assert response["status"] == "completed"
            assert response["kill_requested"] is False
            assert response["agent_message"] == "保持結果"
        assert manager.stopped_sessions[session.session_id].result_delivered is True


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send_message", "kill"])
async def test_unknown_session_is_distinct_from_expired_session(
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """未登録のUUIDを期限切れ識別子と区別し、記録が無いことと復旧手順を返す。

    登録簿のレコードは再起動では削除されないため、再起動を原因として案内しない。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    async with _manager_with_fake("codex") as (manager, _):
        session_id = "3468feae-b2bf-4d67-ac55-3c40207e8b5b"
        with pytest.raises(ValueError) as exc_info:
            if operation == "send_message":
                await manager.send_message(session_id, "続行")
            else:
                await manager.kill(session_id, timeout=0)
        message = _actionable_message(exc_info.value)
        assert message.startswith(f"unknown session: {session_id}")
        assert "restarted" not in message
        assert "no agents_server on this host has a record" in message
        next_action = message.split(f"\n{NEXT_ACTION_PREFIX}", 1)[1]
        assert "`list`" in next_action
        assert "atk agents wait" in next_action


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["show", "send_message", "kill", "stop"])
@pytest.mark.parametrize(("release", "reason_text"), [("expire", "retention expired"), ("stop", "stopped")])
async def test_session_released_by_another_server_is_reported_as_released(
    operation: str,
    release: str,
    reason_text: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """別のagents_serverが解放したsessionの照会は、解放済みであることと理由・時刻を返す。

    どちらの応答も継続不能の判定に使う`unknown session: <id>`で始め、再起動を案内しない。
    記録が無い識別子とは文面が異なる。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_id = "7f0c2b8e-5d1a-4e3b-9c6f-2a8d4e1b7c90"
    async with _manager_with_fake("codex") as (owner, _):
        session = state.SessionState(session_id, str(tmp_path), engine="codex", publish_registry=True)
        _complete(session)
        owner.sessions[session_id] = session
        if release == "expire":
            owner._expire_session(session_id)
            await owner.close()
        else:
            await owner.stop(session_id)
        async with _manager_with_fake("codex") as (observer, _):
            with pytest.raises(ValueError) as exc_info:
                if operation == "show":
                    (await observer.show_session(session_id))
                elif operation == "send_message":
                    await observer.send_message(session_id, "続行")
                elif operation == "kill":
                    await observer.kill(session_id, timeout=0)
                else:
                    await observer.stop(session_id)
            message = _actionable_message(exc_info.value)
            assert "atk agents wait" in message.split(NEXT_ACTION_PREFIX, 1)[1]
            released_at = session_registry.resolve(session_id).released_at
            assert released_at is not None
            assert message.startswith(f"unknown session: {session_id}")
            assert f"released by the owning agents_server ({reason_text}, {released_at})" in message
            assert "restarted" not in message
            assert "no agents_server on this host has a record" not in message
            await owner.close()
            await observer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send_message", "kill", "stop"])
async def test_non_uuid_session_id_reports_identifier_scheme_mismatch(operation: str) -> None:
    """体系外の識別子をsession喪失と区別して公開操作から返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        with pytest.raises(ValueError) as exc_info:
            if operation == "send_message":
                await manager.send_message("agent-session", "続行")
            elif operation == "kill":
                await manager.kill("agent-session", timeout=0)
            else:
                await manager.stop("agent-session")
        message = _actionable_message(exc_info.value)
        assert message.startswith("session identifier scheme mismatch: agent-session")
        assert "unknown session" not in message
        assert "`list`" in message.split(NEXT_ACTION_PREFIX, 1)[1]


@pytest.mark.asyncio
async def test_identifier_resolution_precedes_scheme_classification(tmp_path: pathlib.Path) -> None:
    """登録済み状態の解決後にだけ未解決識別子の体系を判定する。"""
    async with _manager_with_fake("codex") as (manager, _):
        registered = state.SessionState("agent-session", str(tmp_path), engine="codex")
        _complete(registered, message="登録済み")
        manager.sessions[registered.session_id] = registered

        assert (await manager.kill(registered.session_id, timeout=0))["agent_message"] == "登録済み"

        manager.sessions.pop(registered.session_id)
        with pytest.raises(ValueError) as mismatch_exc:
            await manager.kill(registered.session_id, timeout=0)
        assert "identifier scheme mismatch" in str(mismatch_exc.value)
        assert "unknown session" not in str(mismatch_exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
async def test_close_publishes_running_session_as_interrupted_for_restart(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """実行中sessionを持つマネージャーの停止で登録簿と結果ファイルへ終端を公開し、別のマネージャーが再開できる。

    停止時に公開しないと登録簿が`running`のまま残り、再起動後の`show`・`send_message`が実行中の可能性として拒否し続け、
    委譲元の`atk agents wait`も待機上限での終了を繰り返す。
    """
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter({}, shared_roots.StatusFileIdentity("root-session", "root.json", None))
    async with server_manager.AgentsServerManager(writer) as manager:
        _install_backend(manager, engine, FakeBackend(manager.sessions, engine))
        writer.activate()
        session_id = f"{engine}-closing"
        running = state.SessionState(session_id, str(tmp_path), engine=engine, turn_seq=1, publish_registry=True)
        state.initialize_turn(running)
        running.turn_id = "turn-9"
        running.announced = True
        manager.sessions[session_id] = running
        running.touch()
        await running.drain_registration()
        assert session_registry.resolve(session_id).state is session_registry.Resolution.RUNNING

        await manager.close()

        resolution = session_registry.resolve(session_id)
        assert resolution.state is session_registry.Resolution.TERMINAL
        assert resolution.resume_info is not None and resolution.resume_info.status == "interrupted"
        # 再起動後の引き継ぎが`thread/read`の元turnと比べられるよう、Codexのturn識別子も登録簿へ残す。
        assert resolution.resume_info.turn_id == "turn-9"
        assert retained_results.read_retained_result("root-session", session_id, tmp_path) is not None
        assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

        async with _manager_with_fake(engine) as (restarted, backend):
            assert (await _show_through_tool(monkeypatch, restarted, session_id))["status"] == "interrupted"
            response = await restarted.send_message(session_id, "続行")
            assert response["delivery"] == "reply_started"
            assert backend.resume_calls == [session_id]
            await restarted.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("turn_id", "turns", "status"),
    [
        ("turn-2", [("turn-1", "completed"), ("turn-2", "interrupted")], "interrupted"),
        ("turn-1", [("turn-1", "completed")], "completed"),
        (None, [("turn-1", "completed"), ("turn-2", "interrupted")], "interrupted"),
    ],
    ids=["recorded-turn", "single-turn", "legacy-record-all-terminal"],
)
async def test_orphaned_codex_record_is_taken_over_when_turn_has_ended(
    turn_id: str | None,
    turns: list[tuple[str, str]],
    status: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """所有者が不在で`thread/read`が元turnの終端を示す残存記録は、`show`と`send_message`で終端として復元する。"""
    session_id = "01a0f920-43f0-7cb3-b1e6-e06cd9bb7cad"
    _publish_orphaned_codex(monkeypatch, tmp_path, session_id, turn_id)
    async with server_manager.AgentsServerManager(None) as manager:
        backend = ThreadReadBackend(manager.sessions, turns)
        _install_backend(manager, "codex", backend)

        assert (await _show_through_tool(monkeypatch, manager, session_id))["status"] == status
        resolution = session_registry.resolve(session_id)
        assert resolution.state is session_registry.Resolution.TERMINAL
        assert resolution.resume_info is not None and resolution.resume_info.status == status

        response = await manager.send_message(session_id, "続行")
        assert response["delivery"] == "reply_started"
        assert backend.resume_calls == [session_id]
        await manager.close()


_ORPHANED_SESSION_ID = "a0278936-c56f-4a22-9f34-2a5683019a57"


def _publish_record_with_processes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    engine: str,
    processes: tuple[session_registry.ProcessIdentity, ...],
) -> None:
    """所有するagents_serverと委譲先CLIの識別を持つ`running`の記録を、停止処理を経ずに残った形で保存する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish(
        _ORPHANED_SESSION_ID,
        terminal=False,
        engine=engine,
        cwd=str(tmp_path),
        turn_seq=2,
        status="running",
        processes=processes,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_record_whose_owner_and_executor_exited_is_interrupted_on_every_path(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有プロセスと委譲先CLIがともに終了した`running`の記録は、次の照会のいずれでも`interrupted`の終端として扱う。

    `atk agents show`は中断を返し、自動再開は終端した子として扱う。
    MCPの`show`は終了の理由を`error`で返し、`send_message`は拒否せず通常の復元へ進む。
    """
    _publish_record_with_processes(monkeypatch, tmp_path, engine, (exited_process_identity(), exited_process_identity()))
    record = session_registry.registry_directory() / f"{_ORPHANED_SESSION_ID}.json"
    assert json.loads(record.read_text(encoding="utf-8"))["terminal"] is False

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", _ORPHANED_SESSION_ID])
    shown_by_cli = json.loads(capsys.readouterr().out)
    assert shown_by_cli["status"] == "interrupted"
    assert shown_by_cli["error"]["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
    # 照会側の解決は登録簿を書き換えない。
    assert json.loads(record.read_text(encoding="utf-8"))["terminal"] is False

    async with _manager_with_fake("claude") as (parent_manager, parent_backend):
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
        parent.live_child_session_ids.add(_ORPHANED_SESSION_ID)
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
        parent_manager.sessions[parent.session_id] = parent
        await parent_manager._advance_child_session_wait(parent)  # pylint: disable=protected-access
        assert _ORPHANED_SESSION_ID not in parent.live_child_session_ids
        assert any(f"終端したsession/thread: {_ORPHANED_SESSION_ID}" in prompt for prompt in parent_backend.prompts)
        await parent_manager.close()

        async with server_manager.AgentsServerManager(None) as manager:
            backend = ThreadReadBackend(manager.sessions, None) if engine == "codex" else FakeBackend(manager.sessions, engine)
            _install_backend(manager, engine, backend)

            shown = await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
            assert shown["status"] == "interrupted"
            assert shown["error"]["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
            published = json.loads(record.read_text(encoding="utf-8"))
            assert published["terminal"] is True and published["status"] == "interrupted"
            assert published["interruption"] == session_registry.OWNER_PROCESSES_EXITED

            response = await manager.send_message(_ORPHANED_SESSION_ID, "続行")
            assert response["delivery"] == "reply_started"
            assert backend.resume_calls == [_ORPHANED_SESSION_ID]
            if isinstance(backend, ThreadReadBackend):
                assert not backend.read_calls
            await manager.close()


@pytest.mark.asyncio
async def test_record_with_a_live_recorded_process_keeps_running_on_every_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録したプロセスが1つでも存在する記録は、同じ照会のいずれでも実行中として扱う。

    所有サーバーが終了し委譲先CLIが生存する記録は、所有サーバーを失った実行中として案内を返し、追送は再開せずに拒否する。
    """
    owner = session_registry.owner_process()
    assert owner is not None
    _publish_record_with_processes(monkeypatch, tmp_path, "claude", (exited_process_identity(), owner))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", _ORPHANED_SESSION_ID])
    shown_by_cli = json.loads(capsys.readouterr().out)
    assert shown_by_cli["status"] == "running"
    assert shown_by_cli["ownership"] == "owner_lost"

    async with _manager_with_fake("claude") as (parent_manager, parent_backend):
        parent = state.SessionState("parent-session", str(tmp_path), engine="claude")
        parent.live_child_session_ids.add(_ORPHANED_SESSION_ID)
        resume_waits.begin_auto_resume_wait(parent, {"status": "completed", "agent_message": "保留本文", "error": None})
        parent_manager.sessions[parent.session_id] = parent
        await parent_manager._advance_child_session_wait(parent)  # pylint: disable=protected-access
        assert _ORPHANED_SESSION_ID in parent.live_child_session_ids
        assert not parent_backend.prompts
        await parent_manager.close()

        async with server_manager.AgentsServerManager(None) as manager:
            backend = FakeBackend(manager.sessions, "claude")
            _install_backend(manager, "claude", backend)
            shown = await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
            assert shown["status"] == "running"
            assert shown["ownership"] == "owner_lost"
            with pytest.raises(ValueError, match="error.recovery=owner_lost"):
                await manager.send_message(_ORPHANED_SESSION_ID, "続行")
            assert session_registry.resolve(_ORPHANED_SESSION_ID).state is session_registry.Resolution.RUNNING
            assert not backend.resume_calls
            await manager.close()


_DEAD_SERVER = "20261018T111410000000Z-4242-0a0b0c0d"
_NEXT_SERVER = "20261018T120000000000Z-4343-1a1b1c1d"


def _record_dead_server_diagnostics(tmp_path: pathlib.Path) -> pathlib.Path:
    """停止前にSIGTERMを受けた起動単位の終了診断を作成する。"""
    path = logging_config.instance_path(_DEAD_SERVER, tmp_path)
    logging_config.append_instance_event(path, writer="server", event="started", pid=4242)
    logging_config.append_instance_event(path, writer="server", event="signal_received", signal="SIGTERM")
    return path


def _publish_dead_owner_record(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, engine: str) -> None:
    """起動単位を持つ所有者が終端を公開せずに終わった`running`の記録を残し、照会側を別の起動単位にする。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", _DEAD_SERVER)
    session_registry.publish(
        _ORPHANED_SESSION_ID,
        terminal=False,
        engine=engine,
        cwd=str(tmp_path),
        turn_seq=2,
        status="running",
        processes=(exited_process_identity(), exited_process_identity()),
        launch_info=session_registry.LaunchInfo(label="worker", prompt="作業する"),
    )
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", _NEXT_SERVER)


def _cli_show(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "show", _ORPHANED_SESSION_ID])
    return json.loads(capsys.readouterr().out)


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_interrupted_show_references_server_diagnostics(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有者消失の中断を返すMCPの`show`と`atk agents show`は、中断turnを所有した起動単位の終了診断を同じ参照で返す。"""
    diagnostics_path = _record_dead_server_diagnostics(tmp_path)
    _publish_dead_owner_record(monkeypatch, tmp_path, engine)
    expected = {
        "server_instance": _DEAD_SERVER,
        "turn_seq": 2,
        "path": str(diagnostics_path),
        "status": "collected",
    }

    by_cli = _cli_show(capsys)["error"]
    assert by_cli["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
    assert {key: by_cli["diagnostics"][key] for key in expected} == expected
    assert f"sed -n '1,200p' -- {diagnostics_path}" in by_cli["diagnostics"]["next_action"]

    async with server_manager.AgentsServerManager(None) as manager:
        backend = ThreadReadBackend(manager.sessions, None) if engine == "codex" else FakeBackend(manager.sessions, engine)
        _install_backend(manager, engine, backend)
        by_mcp = (await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID))["error"]
        assert by_mcp["interruptionReason"] == session_registry.OWNER_PROCESSES_EXITED
        assert {key: by_mcp["diagnostics"][key] for key in expected} == expected
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
async def test_interrupted_turn_reference_survives_resume_and_release(
    engine: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """別の起動単位が引き継ぎ・再開・解放した後も、`show`は元の中断turnと元の起動単位の参照を返す。"""
    diagnostics_path = _record_dead_server_diagnostics(tmp_path)
    _publish_dead_owner_record(monkeypatch, tmp_path, engine)
    expected = [{"turn_seq": 2, "interruptionReason": "owner_processes_exited", "server_instance": _DEAD_SERVER}]

    def summary(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "turn_seq": turn["turn_seq"],
                "interruptionReason": turn["interruptionReason"],
                "server_instance": turn["diagnostics"]["server_instance"],
            }
            for turn in turns
        ]

    async with server_manager.AgentsServerManager(None) as manager:
        backend = ThreadReadBackend(manager.sessions, None) if engine == "codex" else FakeBackend(manager.sessions, engine)
        backend.publish_registry = True
        _install_backend(manager, engine, backend)
        await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
        response = await manager.send_message(_ORPHANED_SESSION_ID, "続行")
        assert response["delivery"] == "reply_started"
        resumed = manager.sessions[_ORPHANED_SESSION_ID]
        await resumed.drain_registration()
        assert session_registry.resolve(_ORPHANED_SESSION_ID).server_instance == _NEXT_SERVER

        shown = await _show_through_tool(monkeypatch, manager, _ORPHANED_SESSION_ID)
        assert summary(shown["interrupted_turns"]) == expected
        assert shown["interrupted_turns"][0]["diagnostics"]["path"] == str(diagnostics_path)
        assert summary(_cli_show(capsys)["interrupted_turns"]) == expected

        _complete(resumed, message="再開後の結果")
        resumed.touch()
        await resumed.drain_registration()
        await manager.stop(_ORPHANED_SESSION_ID)
        assert session_registry.resolve(_ORPHANED_SESSION_ID).state is session_registry.Resolution.RELEASED
        assert summary(_cli_show(capsys)["interrupted_turns"]) == expected

        async with server_manager.AgentsServerManager(None) as restarted:
            with pytest.raises(ActionableError) as exc_info:
                await _show_through_tool(monkeypatch, restarted, _ORPHANED_SESSION_ID)
            message = str(exc_info.value)
            assert "interrupted_turns=" in message
            assert _DEAD_SERVER in message and str(diagnostics_path) in message
            await manager.close()
            await restarted.close()


@pytest.mark.asyncio
async def test_legacy_interruption_reports_not_collected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """起動単位を持たない旧記録の中断は`not_collected`を返し、記録が残るが終了を示さない`incomplete`と区別する。"""
    monkeypatch.setattr(session_registry, "_OWNER_SERVER_INSTANCE", None)
    _publish_record_with_processes(monkeypatch, tmp_path, "claude", (exited_process_identity(),))
    legacy = _cli_show(capsys)["error"]["diagnostics"]
    assert legacy["status"] == "not_collected"
    assert legacy["server_instance"] is None and legacy["path"] is None

    logging_config.append_instance_event(
        logging_config.instance_path(_DEAD_SERVER, tmp_path), writer="server", event="started", pid=1
    )
    _publish_dead_owner_record(monkeypatch, tmp_path, "claude")
    assert _cli_show(capsys)["error"]["diagnostics"]["status"] == "incomplete"
