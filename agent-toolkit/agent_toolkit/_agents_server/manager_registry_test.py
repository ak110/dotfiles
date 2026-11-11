"""`_agents_server/manager_registry.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any, cast

import pytest

import agent_toolkit._agents_server.commands as atk_agents
from agent_toolkit._agents_server import agents_wait, manager_base, responses, shared_layout, shared_roots, state, status_file
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    UnavailableStartBackend,
    _complete,
    _install_backend,
    _manager_with_fake,
    _publish_recovered_session,
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
            session_registry.publish(
                session_id, state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(tmp_path), turn_seq=3
            )
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
                session_id,
                state=session_registry.PublicationState.from_status(cast(Any, terminal_status), result_available=True),
                cwd=str(tmp_path),
                turn_seq=3,
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
    """表示範囲を指定しない場合は未回収結果を持たない終了sessionだけを除く。"""
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
    """保持中・期限切れ・破棄後の終了sessionが空でない`error`を持つ場合だけ、`show`が`error`を返す。

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
