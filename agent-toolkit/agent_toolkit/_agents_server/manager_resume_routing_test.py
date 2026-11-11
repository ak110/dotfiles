"""`_agents_server/manager_resume.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import asyncio
import pathlib
import time
from typing import Any

import pytest

from agent_toolkit._agents_server import api_credits, child_environment, launch_requests, state
from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import manager as server_manager
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    FakeClaudeClient,
    ResultMessage,
    SystemMessage,
    _complete,
    _manager_with_fake,
    _resume_test_manager,
    _wait_until_terminal,
    install_backend,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("order,api_first", [("subscription,credits", False), ("credits,subscription", True)])
@pytest.mark.parametrize("unavailable", [False, True])
@pytest.mark.asyncio
async def test_delegate_candidate_order_follows_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    order: str,
    api_first: bool,
    unavailable: bool,
) -> None:
    """新規起動は使用順に従い、使用順の並びを変えても継続・冷再開は選択済み接続を維持する。"""
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=test-api-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", order)
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        if unavailable and len(options_seen) == 1:
            raise ConnectionError("起動前認証を利用できない")
        return FakeClaudeClient(
            [
                [SystemMessage(options.resume or "candidate-order"), AssistantMessage("応答"), ResultMessage("完了")],
                [AssistantMessage("追送"), ResultMessage("継続完了")],
            ]
        )

    async with (
        server_manager.AgentsServerManager() as manager,
        claude_backend.ClaudeServerManager(manager.sessions, client_factory=factory, publish_registry=True) as backend,
    ):
        install_backend(manager, "claude", backend)
        try:
            started = await manager.start("claude:sonnet/high", "作業", str(tmp_path))
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert bool(options_seen[0].env.get("ANTHROPIC_API_KEY")) is api_first
            monkeypatch.setenv(
                "AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS",
                "subscription,credits" if api_first else "credits,subscription",
            )
            await manager.send_message(started["session_id"], "継続")
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            await manager.stop(started["session_id"])
            await manager.send_message(started["session_id"], "再開")
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert bool(options_seen[-1].env.get("ANTHROPIC_API_KEY")) is (not api_first if unavailable else api_first)
            assert options_seen[-1].resume == started["session_id"]
            assert options_seen[-1].model == "sonnet" and options_seen[-1].effort == "high"
        finally:
            await manager.close()


@pytest.mark.parametrize("content", [None, "", "# 未記入\n"])
@pytest.mark.asyncio
async def test_invalid_env_file_warning_reaches_start_response(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    content: str | None,
) -> None:
    """無効なAPI環境ファイルでは委譲先がサブスクリプションで始まり、`start`応答の`warnings`へパスと修正操作を示す。"""
    path = tmp_path / "invalid.env"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", str(path))
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        return FakeClaudeClient([[SystemMessage("invalid-env"), AssistantMessage("応答"), ResultMessage("完了")]])

    async with (
        server_manager.AgentsServerManager() as manager,
        claude_backend.ClaudeServerManager(manager.sessions, client_factory=factory, publish_registry=True) as backend,
    ):
        install_backend(manager, "claude", backend)
        try:
            started = await manager.start("claude:sonnet/high", "作業", str(tmp_path))
            assert any(str(path) in item and "atk config set claude_credits_env_file" in item for item in started["warnings"])
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert not options_seen[0].env.get("ANTHROPIC_API_KEY")
        finally:
            await manager.close()


@pytest.mark.parametrize("kind", ["credits", "payg"])
@pytest.mark.asyncio
async def test_single_api_connection_keeps_usage_limit_wait(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """API認証だけを持つ会話は残高不足の解除を待ち、接続先を変えない。"""
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=test-api-key\n", encoding="utf-8")
    monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_CLAUDE_{kind.upper()}_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", f"subscription,{kind}")
    # 起動時に保持したOAuthも除き、他の試験でprepareした接続値を持ち越さない。
    monkeypatch.setattr(child_environment, "_INHERITED", {})
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        return FakeClaudeClient([[SystemMessage("single-api"), ResultMessage("Credit balance is too low", is_error=True)]])

    async with (
        server_manager.AgentsServerManager() as manager,
        claude_backend.ClaudeServerManager(manager.sessions, client_factory=factory) as backend,
    ):
        install_backend(manager, "claude", backend)
        try:
            started = await manager.start("claude:sonnet/high", "作業", str(tmp_path))
            assert manager.sessions[started["session_id"]].claude_connection == kind
            assert started["status"] == "running"
            shown = await manager.show_session(started["session_id"])
            assert shown["status"] == "running" and shown["result_held"]
            assert len(options_seen) == 1 and options_seen[0].env["ANTHROPIC_API_KEY"] == "test-api-key"
        finally:
            await manager.close()


@pytest.mark.asyncio
async def test_api_credit_exhaustion_moves_to_subscription(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """API残高不足の同一会話をサブスクリプションで続け、停止後もその接続を保持する。"""
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=test-api-key\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_CREDITS_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_WORKER_CONNECTIONS", "credits,subscription")
    monkeypatch.setattr(server_manager, "START_AVAILABILITY_TIMEOUT", 0.01)
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        identifier = options.resume or f"credits-{len(options_seen)}"
        if len(options_seen) == 1:
            return FakeClaudeClient([[SystemMessage(identifier), ResultMessage("Credit balance is too low", is_error=True)]])
        return FakeClaudeClient([[SystemMessage(identifier), AssistantMessage("再開"), ResultMessage("完了")]])

    async with (
        server_manager.AgentsServerManager() as manager,
        claude_backend.ClaudeServerManager(manager.sessions, client_factory=factory, publish_registry=True) as backend,
    ):
        install_backend(manager, "claude", backend)
        try:
            started = await manager.start("claude:sonnet/high", "作業", str(tmp_path))
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert options_seen[0].env["ANTHROPIC_API_KEY"] == "test-api-key"
            assert not options_seen[1].env.get("ANTHROPIC_API_KEY")
            assert options_seen[1].resume == started["session_id"]
            assert options_seen[1].model == "sonnet" and options_seen[1].effort == "high"
            await manager.stop(started["session_id"])
            await manager.send_message(started["session_id"], "残作業")
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert not options_seen[-1].env.get("ANTHROPIC_API_KEY")
            assert options_seen[-1].resume == started["session_id"]
            # 残高不足の観測から一定時間内に起動する新しい会話は、APIで失敗する往復を省いてサブスクリプションから始める。
            launches = len(options_seen)
            fresh = await manager.start("claude:sonnet/high", "新規", str(tmp_path))
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert fresh["session_id"] != started["session_id"]
            assert len(options_seen) == launches + 1
            assert not options_seen[-1].env.get("ANTHROPIC_API_KEY")
            # 一定時間を過ぎた後に起動する新しい会話は、通常の接続順どおりAPIを再び試す。
            api_credits.record_shortage("claude", now=time.monotonic() - api_credits.CREDIT_SHORTAGE_TTL_SECONDS - 1)
            later = await manager.start("claude:sonnet/high", "新規2", str(tmp_path))
            assert (await _wait_until_terminal(manager))["status"] == "completed"
            assert later["session_id"] != fresh["session_id"]
            assert options_seen[-1].env["ANTHROPIC_API_KEY"] == "test-api-key"
        finally:
            await manager.close()


@pytest.mark.asyncio
async def test_expired_explore_session_resumes_with_original_route_conditions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """結果保持期限後の再開でも探索フラグと除外集合を維持する。"""
    candidates = [("codex", "first", "high"), ("codex", "second", "high")]
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: candidates)
    async with _manager_with_fake("codex") as (manager, backend):
        original_start = backend.start

        async def fail_first_start(
            prompt: str,
            cwd: str,
            model: str | None,
            effort: str | None,
            **kwargs: Any,
        ) -> state.SessionState:
            session = await original_start(prompt, cwd, model, effort, **kwargs)
            if model == "first":
                _complete(session, message="", error={"codexErrorInfo": "usageLimitExceeded"})
            return session

        monkeypatch.setattr(backend, "start", fail_first_start)
        second = await manager.start_explore("探索", str(tmp_path), model_type="review")
        session = manager.sessions[second["session_id"]]
        _complete(session)
        session.retention_deadline = asyncio.get_running_loop().time() - 1

        response = await manager.send_message(session.session_id, "続行")

        resumed = manager.sessions[session.session_id]
        assert response["delivery"] == "reply_started"
        assert resumed.model_type == "review"
        assert resumed.launch_kind == "explore"
        assert resumed.excluded_candidates == frozenset({candidates[0]})


@pytest.mark.asyncio
async def test_minimal_followup_uses_restored_launch_history(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """登録簿から戻した役割でも、余分な継続入力を配送前に拒否し必要なroundを保持する。"""
    (tmp_path / "計画.md").write_text("# 計画\n## 概要\n### 計画メタ情報\n- 関連WI: なし\n", encoding="utf-8")
    async with _resume_test_manager(monkeypatch, tmp_path) as (manager, _):
        _, prompt, _, _ = launch_requests.task_document_request(
            "exec-review",
            {
                "レビュー基準": "計画",
                "計画": str(tmp_path / "計画.md"),
                "引き継ぎ記録先": str(tmp_path / "引継ぎ.md"),
            },
        )
        session = state.SessionState("restored-review", str(tmp_path), engine="claude", prompt=prompt, publish_registry=True)
        manager.sessions[session.session_id] = session
        _complete(session)
        await session.publish_registration_async()
        await manager.close()
        async with _resume_test_manager(monkeypatch, tmp_path) as (resumed, _):
            with pytest.raises(ValueError, match="検証結果"):
                await resumed.send_message(session.session_id, "再レビューせよ\nround: 2\n検証結果: 成功")
            response = await resumed.send_message(session.session_id, instruction="再レビューせよ", extra_params={"round": "2"})
            assert response["delivery"] == "reply_started"
            current = resumed.sessions[session.session_id]
            assert current.prompt == prompt
            assert current.followup_prompts == ("再レビューせよ\nround: 2",)
