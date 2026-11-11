"""`_agents_server/manager_resume.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import datetime
import pathlib

import pytest

from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._agents_server import resume_waits, unavailable_candidates
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import claude_usage_limit
from agent_toolkit._testing.agents_server_support import (
    _OVERLOAD_ERROR,
    AssistantMessage,
    OverloadingBackend,
    ResultMessage,
    SystemMessage,
    UnavailableStartBackend,
    _install_backend,
    _rate_limit_event,
    _rejected_stream,
    _usage_limit_result,
    _usage_limited_manager,
    _wait_until_terminal,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_overloaded_turn_resumes_same_session_and_publishes_resumed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動後の過負荷は失敗を公開せず、待機の後に同じsessionで継続し、継続したturnの結果を終了時の結果として返す。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _m, **_kwargs: [("codex", "gpt-6.1-sol", "high")]
    )
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(resume_waits, "OVERLOAD_RESUME_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    async with server_manager.AgentsServerManager() as manager:
        backend = OverloadingBackend(manager.sessions, manager._condition, overloads=2)
        _install_backend(manager, "codex", backend)

        started = await manager.start("execute", "作業する", str(tmp_path))
        backend.finish_release.set()
        response = await _wait_until_terminal(manager)

        assert started["status"] == "running"
        assert response["session_id"] == started["session_id"]
        assert response["status"] == "completed"
        assert response["agent_message"] == "継続後の結果"
        assert backend.send_calls == 2
        assert all("serverOverloaded" in prompt and "所定の返却形式" in prompt for prompt in backend.prompts[1:])
        assert manager.sessions[started["session_id"]].turn_seq == 3
        assert not unavailable_candidates.load_unavailable_candidates(
            "execute", "delegate", now=datetime.datetime.now(datetime.UTC)
        )
        await manager.close()


@pytest.mark.asyncio
async def test_overload_wait_is_visible_as_running_with_api_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """待機中の`show`と`list`は`running`、`api_error`の種別`serverOverloaded`と結果の保留を返す。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _m, **_kwargs: [("codex", "gpt-6.1-sol", "high")]
    )
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(resume_waits, "OVERLOAD_RESUME_DELAYS_SECONDS", (3600.0, 3600.0, 3600.0))
    async with server_manager.AgentsServerManager() as manager:
        backend = OverloadingBackend(manager.sessions, manager._condition, overloads=1)
        _install_backend(manager, "codex", backend)

        started = await manager.start("execute", "作業する", str(tmp_path))
        backend.finish_release.set()
        await asyncio.gather(*backend.pending)
        shown = await manager.show_session(started["session_id"])
        listed = await manager.list_sessions()

        assert shown["status"] == "running"
        assert shown["result_held"] is True
        assert shown["api_error"]["type"] == "serverOverloaded"
        assert shown["api_error"]["http_status"] is None
        listed_session = next(item for item in listed["sessions"] if item["session_id"] == started["session_id"])
        assert listed_session["status"] == "running"
        assert listed_session["api_error"]["type"] == "serverOverloaded"
        assert backend.send_calls == 0
        await manager.close()


@pytest.mark.asyncio
async def test_overload_limit_publishes_last_failure_and_excludes_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """3回の継続も過負荷で終われば、最後の失敗を現行の形で公開し、候補を次回の除外対象に記録する。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _m, **_kwargs: [("codex", "gpt-6.1-sol", "high")]
    )
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.001)
    monkeypatch.setattr(resume_waits, "OVERLOAD_RESUME_DELAYS_SECONDS", (0.0, 0.0, 0.0))
    async with server_manager.AgentsServerManager() as manager:
        backend = OverloadingBackend(manager.sessions, manager._condition, overloads=4)
        _install_backend(manager, "codex", backend)

        started = await manager.start("execute", "作業する", str(tmp_path))
        backend.finish_release.set()
        response = await _wait_until_terminal(manager)

        assert response["status"] == "failed"
        assert response["error"]["codexErrorInfo"] == "serverOverloaded"
        assert response["error"] == _OVERLOAD_ERROR
        assert backend.send_calls == 3
        assert manager.sessions[started["session_id"]].turn_seq == 4
        assert unavailable_candidates.load_unavailable_candidates(
            "execute", "delegate", now=datetime.datetime.now(datetime.UTC)
        ) == {("codex", "gpt-6.1-sol", "high"): "serverOverloaded"}
        await manager.close()


@pytest.mark.asyncio
async def test_overload_within_availability_check_switches_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動の可用性確認の間に過負荷で終了した候補は、自動継続せず次の候補への切替で扱う。"""
    candidates = [("codex", "gpt-6.1-sol", "high"), ("codex", "gpt-6-sol", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _m, **_kwargs: candidates)
    async with server_manager.AgentsServerManager() as manager:
        _install_backend(manager, "codex", UnavailableStartBackend(manager.sessions, "codex", error=dict(_OVERLOAD_ERROR)))

        response = await manager.start("execute", "作業する", str(tmp_path))

        assert response["status"] == "failed"
        assert [item["reason"] for item in response["excluded_candidates"]] == ["serverOverloaded", "serverOverloaded"]
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit_type", ["seven_day", "seven_day_opus", "seven_day_sonnet", "five_hour"])
@pytest.mark.parametrize("before_init", [True, False])
async def test_claude_usage_limit_waits_and_resumes_same_session(
    limit_type: str,
    before_init: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動直後にWeekly limitか5時間の利用上限で拒否されても失敗を公開せず、解除後に同じsessionで続けた結果を返す。"""
    monkeypatch.setattr(claude_usage_limit, "RECHECK_SECONDS", 0.05)
    streams = [
        _rejected_stream(limit_type, None, before_init=before_init, session_id="claude-limited"),
        [_rate_limit_event("allowed", limit_type), AssistantMessage("再開後"), ResultMessage("継続後の結果")],
    ]
    async with _usage_limited_manager(monkeypatch, streams) as (manager, client, codex):
        started = await manager.start("execute", "作業する", str(tmp_path))
        response = await _wait_until_terminal(manager)

        assert started["status"] == "running"
        assert started["engine"] == "claude"
        assert "excluded_candidates" not in started
        assert response["session_id"] == "claude-limited"
        assert response["status"] == "completed"
        assert response["agent_message"] == "継続後の結果"
        assert codex.start_calls == []
        assert len(client.queries) == 2
        assert limit_type in client.queries[1] and "所定の返却形式" in client.queries[1]
        assert not unavailable_candidates.load_unavailable_candidates(
            "execute", "delegate", now=datetime.datetime.now(datetime.UTC)
        )


@pytest.mark.asyncio
async def test_claude_usage_limit_keeps_waiting_while_rejection_continues(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続のturnも拒否される間は待機へ戻り、回数で打ち切らずに解除後の結果を返す。"""
    monkeypatch.setattr(claude_usage_limit, "RECHECK_SECONDS", 0.02)
    rejected = [_rate_limit_event("rejected", "seven_day"), _usage_limit_result()]
    streams = [
        _rejected_stream("seven_day", None, before_init=False, session_id="claude-repeated"),
        *[list(rejected) for _ in range(4)],
        [AssistantMessage("再開後"), ResultMessage("解除後の結果")],
    ]
    async with _usage_limited_manager(monkeypatch, streams) as (manager, client, codex):
        await manager.start("execute", "作業する", str(tmp_path))
        response = await _wait_until_terminal(manager, attempts=100)

        assert response["status"] == "completed"
        assert response["agent_message"] == "解除後の結果"
        assert len(client.queries) == 6
        assert codex.start_calls == []


@pytest.mark.asyncio
async def test_claude_usage_limit_wait_is_visible_and_kill_returns_held_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解除待ちの種類と解除予定時刻を公開し、`kill`は保留した診断を持つ停止結果を返す。"""
    resets_at = int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600
    streams = [_rejected_stream("seven_day_opus", resets_at, before_init=False, session_id="claude-visible")]
    async with _usage_limited_manager(monkeypatch, streams) as (manager, client, codex):
        started = await manager.start("execute", "作業する", str(tmp_path))
        shown = await manager.show_session(started["session_id"])
        listed = next(
            item for item in (await manager.list_sessions())["sessions"] if item["session_id"] == started["session_id"]
        )

        assert started["status"] == "running"
        assert shown["status"] == "running"
        assert shown["result_held"] is True
        expected_reset = datetime.datetime.fromtimestamp(resets_at, datetime.UTC).isoformat()
        assert shown["api_error"]["type"] == "usage_limit"
        assert shown["api_error"]["limit_type"] == "seven_day_opus"
        assert shown["api_error"]["resets_at"] == expected_reset
        assert listed["status"] == "running"
        assert listed["api_error"]["limit_type"] == "seven_day_opus"
        assert len(client.queries) == 1

        killed = await manager.kill(started["session_id"])

        assert killed["status"] == "interrupted"
        assert killed["error"]["usageLimit"] == {"type": "seven_day_opus", "resetsAt": expected_reset}
        assert client.disconnected
        assert codex.start_calls == []
        assert not unavailable_candidates.load_unavailable_candidates(
            "execute", "delegate", now=datetime.datetime.now(datetime.UTC)
        )


@pytest.mark.asyncio
async def test_claude_usage_limit_wait_accepts_send_message_as_reply(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解除待ちの間に委譲元が`send_message`を送ると、保留を確定してreplyとして配送する。"""
    resets_at = int(datetime.datetime.now(datetime.UTC).timestamp()) + 3600
    streams = [
        _rejected_stream("five_hour", resets_at, before_init=False, session_id="claude-reply"),
        [AssistantMessage("返信"), ResultMessage("返信の結果")],
    ]
    async with _usage_limited_manager(monkeypatch, streams) as (manager, client, _codex):
        started = await manager.start("execute", "作業する", str(tmp_path))
        await manager.send_message(started["session_id"], "続けて")
        response = await _wait_until_terminal(manager)

        assert response["status"] == "completed"
        assert response["agent_message"] == "返信の結果"
        assert "続けて" in client.queries[1]


@pytest.mark.asyncio
async def test_claude_overage_rejection_still_switches_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """待機の対象外（`overage`）の拒否は従来どおり次の候補へ切り替え、新しい形式の理由で記録する。"""
    streams = [_rejected_stream("overage", None, before_init=False, session_id="claude-overage")]
    async with _usage_limited_manager(monkeypatch, streams) as (manager, _client, codex):
        # 切替先のCodex候補はモデル出力を返さないため、可用性確認の上限まで待つ時間を短くする。
        monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.3)
        response = await manager.start("execute", "作業する", str(tmp_path))

        assert response["engine"] == "codex"
        assert codex.start_calls == [("gpt-6.1-sol", "high", "delegate")]
        assert [item["reason"] for item in response["excluded_candidates"]] == ["429:overage"]


@pytest.mark.asyncio
async def test_legacy_claude_rate_limit_record_does_not_skip_candidate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """旧版がClaudeの429へ記録した除外理由`429`では候補を除外せず、その候補で起動し直す。"""
    unavailable_candidates.record_unavailable_candidate(
        "execute", "delegate", ("claude", "opus", "high"), "429", now=datetime.datetime.now(datetime.UTC)
    )
    streams = [[SystemMessage("claude-legacy"), AssistantMessage("進行"), ResultMessage("完了")]]
    async with _usage_limited_manager(monkeypatch, streams) as (manager, _client, codex):
        response = await manager.start("execute", "作業する", str(tmp_path))

        assert response["engine"] == "claude"
        assert "excluded_candidates" not in response
        assert codex.start_calls == []
