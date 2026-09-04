#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.28.1,<2", "claude-agent-sdk>=0.2.144,<0.3", "pydantic>=2", "platformdirs>=4.0", "pytilpack>=1.47.0"]
# ///
"""CodexとClaudeの委譲先を非同期MCPとして公開する。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import logging
import os
import warnings
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any
from uuid import UUID

import _agents_server_claude as claude_backend
import _agents_server_codex as codex_backend
import _atk_config
import _inherited_venv
import _wait_schedule
from _agents_server_state import (
    LaunchKind,
    ModelCandidate,
    ResumePrompt,
    SessionOwnerGoneError,
    SessionResumeState,
    SessionState,
    _validate_cwd,
    _validate_model_effort,
    _validate_prompt,
    _validate_shell_request,
)
from mcp.server.fastmcp import FastMCP
from pydantic import Field

try:
    from pydantic_settings.exceptions import IncompleteFieldDefinitionWarning
except ImportError:  # pragma: no cover - mcpの依存版が警告型を公開しない場合
    IncompleteFieldDefinitionWarning = None  # type: ignore[assignment,misc]

DEFAULT_KILL_TIMEOUT = 270.0
DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0
SUPPORTED_ENGINES = frozenset({"claude", "codex"})
REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})
# 起動直後の可用性失敗を確定するために`start`が終端を待つ上限秒数。
# Codex CLI 0.152.0で利用上限に達した状態のturnは、backendの起動応答から3.84〜4.27秒後に
# `turn/completed`で失敗した（2026-09-02、`explore_fast`候補`gpt-5.6-terra/medium`で3回測定）。
# 再検証は同じ失敗状態で`AppServerManager.start`を呼び、応答から終端までの経過を測る。
START_AVAILABILITY_TIMEOUT = 15.0
# engineの可用性に起因し、別候補なら結果が変わり得る失敗の識別子。
# `codex app-server generate-json-schema`が出力する`CodexErrorInfo`列挙のうち、
# 利用枠超過、流量制限及びサーバー側過負荷に該当する区分へ限定する。
ENGINE_UNAVAILABLE_ERROR_INFO = frozenset({"usageLimitExceeded", "rateLimitExceeded", "serverOverloaded"})
# engineの可用性に起因し、別候補なら結果が変わり得るClaude APIのHTTPステータス。
# Claude APIの公式なエラーコード表が再試行可能とする429（rate_limit_error）と
# 529（overloaded_error）へ限定する。500（api_error）はサービス内部の失敗であり、
# 候補の変更で解決するとは限らないため含めない。
ENGINE_UNAVAILABLE_API_ERROR_STATUS = frozenset({429, 529})


@dataclasses.dataclass
class _PendingResume:
    """timeout後も同じsessionから観測・共有する再開操作。"""

    state: SessionResumeState
    task: asyncio.Task[SessionState]
    prompt: ResumePrompt
    previous_result: dict[str, Any] | None = None
    previous_result_deadline: float | None = None
    previous_result_expiration: asyncio.TimerHandle | None = dataclasses.field(default=None, repr=False)

    def discard_previous_result(self) -> None:
        """元sessionの保持期限に従って退避済み結果本文を破棄する。"""
        self.previous_result = None
        self.previous_result_deadline = None
        if self.previous_result_expiration is not None:
            self.previous_result_expiration.cancel()
            self.previous_result_expiration = None

    def take_previous_result(self) -> dict[str, Any] | None:
        """期限内の退避済み結果を返し、進行中再開から本文を取り除く。"""
        if (
            self.previous_result is not None
            and self.previous_result_deadline is not None
            and asyncio.get_running_loop().time() >= self.previous_result_deadline
        ):
            self.discard_previous_result()
            return None
        result = self.previous_result
        self.discard_previous_result()
        return result


def _engine_unavailable(session: SessionState) -> bool:
    """終端したsessionが、engineの可用性を理由に失敗したかを返す。"""
    if session.status != "failed" or not isinstance(session.error, dict):
        return False
    if session.error.get("codexErrorInfo") in ENGINE_UNAVAILABLE_ERROR_INFO:
        return True
    return session.error.get("apiErrorStatus") in ENGINE_UNAVAILABLE_API_ERROR_STATUS


def _shell_prompt(command: str, summary_policy: str) -> str:
    """コマンドと要約方針を、シェル実行委譲先への指示本文へ組み立てる。"""
    return f"次のコマンドを実行し、結果を報告せよ。\n\n実行するコマンド:\n{command}\n\n要約方針:\n{summary_policy}"


class AgentsServerManager:
    """エンジン別バックエンドと共有session状態を管理する。"""

    def __init__(self) -> None:
        self.sessions: dict[str, SessionState] = {}
        self.expired_sessions: dict[str, SessionResumeState] = {}
        self._pending_resumes: dict[str, _PendingResume] = {}
        self._condition = asyncio.Condition()
        self._resume_lock = asyncio.Lock()
        self._codex: Any = None
        self._claude: Any = None
        self._wait_timeouts: dict[str, float] = {}

    def _backend(self, engine: str) -> Any:
        if engine == "codex":
            if self._codex is None:
                self._codex = codex_backend.AppServerManager(self.sessions, self._condition)
            return self._codex
        if engine == "claude":
            if self._claude is None:
                self._claude = claude_backend.ClaudeServerManager(
                    self.sessions,
                    self._condition,
                    expire_session=self._expire_session,
                )
            return self._claude
        raise ValueError(f"unsupported engine: {engine}")

    def _get_session(self, session_id: str) -> SessionState:
        """保持中のsessionを返し、未解決値は識別子体系と喪失に分けて診断する。"""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        try:
            session = self.sessions[session_id]
        except KeyError as exc:
            if session_id in self.expired_sessions:
                raise ValueError(f"session retention expired: {session_id}") from exc
            raise self._unresolved_session_error(session_id, label="session") from exc
        if session.retention_deadline is not None and asyncio.get_running_loop().time() >= session.retention_deadline:
            self._expire_session(session_id)
            raise ValueError(f"session retention expired: {session_id}")
        return session

    def _expire_session(self, session_id: str) -> None:
        """期限切れ結果本体を破棄し、会話再開用の最小状態だけを保持する。"""
        session = self.sessions.pop(session_id, None)
        if session is not None:
            self.expired_sessions[session_id] = SessionResumeState.from_session(session)

    def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        if not isinstance(session_id, str) or not session_id:
            return None
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
        resume_state = self.expired_sessions.get(session_id)
        if resume_state is None:
            return None
        response: dict[str, Any] = {
            "session_id": session_id,
            "engine": resume_state.engine,
            "status": "expired",
            "progress": "",
            "kill_requested": False,
        }
        if resume_state.model_type is not None:
            response["model_type"] = resume_state.model_type
        return response

    def _route_state(
        self,
        session_id: str,
        *,
        unknown_label: str = "exclude_session_id",
    ) -> SessionState | SessionResumeState:
        """全保持状態からsessionを返し、未解決値だけを体系と喪失に分ける。"""
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("exclude_session_id must be a non-empty string")
        session = self.sessions.get(session_id)
        if session is not None:
            return session
        resume_state = self.expired_sessions.get(session_id)
        if resume_state is not None:
            return resume_state
        pending = self._pending_resumes.get(session_id)
        if pending is not None:
            return pending.state
        raise self._unresolved_session_error(session_id, label=unknown_label)

    @staticmethod
    def _unresolved_session_error(session_id: str, *, label: str) -> ValueError:
        """未解決の識別子を体系相違または失われたsessionとして診断する。

        保持状態の照会後だけ呼び、登録済みの非UUID識別子は拒否しない。
        体系相違の本文には、継続不能の判定に使う`unknown session`を含めない。
        """
        try:
            parsed = UUID(session_id)
        except ValueError:
            parsed = None
        if parsed is None or str(parsed) != session_id.lower():
            return ValueError(f"{label} identifier scheme mismatch: {session_id}; expected UUID")
        return ValueError(
            f"unknown {label}: {session_id}; agents_server may have restarted and lost this session; "
            "start a new session with the verified state"
        )

    def _resolve_start_candidates(
        self,
        model_type: str,
        *,
        launch_kind: LaunchKind,
        exclude_session_id: str | None,
    ) -> tuple[list[ModelCandidate], frozenset[ModelCandidate]]:
        """起動条件を検証し、除外後の候補列を設定順で返す。"""
        candidates = _atk_config.resolve_model_candidates(model_type)
        excluded: frozenset[ModelCandidate] = frozenset()
        if exclude_session_id is not None:
            source = self._route_state(exclude_session_id)
            if source.model_type != model_type or source.launch_kind != launch_kind:
                raise ValueError(
                    "exclude_session_id start conditions differ: "
                    f"source model_type={source.model_type}, launch_kind={source.launch_kind}; "
                    f"requested model_type={model_type}, launch_kind={launch_kind}"
                )
            if source.model is None or source.effort is None:
                raise ValueError(f"exclude_session_id has no selected candidate: {exclude_session_id}")
            excluded = source.excluded_candidates | frozenset({(source.engine, source.model, source.effort)})
        remaining = [item for item in candidates if item not in excluded]
        if not remaining:
            raise ValueError(f"no model candidates remain for model_type: {model_type}")
        return remaining, excluded

    async def start(
        self,
        model_type: str,
        prompt: str,
        cwd: str,
        exclude_session_id: str | None = None,
        *,
        launch_kind: LaunchKind = "delegate",
    ) -> dict[str, Any]:
        """工程別モデル設定の候補を先頭から試し、起動できたturnを返す。

        engineが起動できない候補は除外集合へ加えて次候補へ進む。
        該当するのは、backendの起動が例外で失敗した候補と、
        起動直後にengineの可用性を理由として終端した候補である。
        候補を変えても結果が変わらない失敗では候補を進めず、そのまま呼び出し元へ返す。
        """
        candidates, excluded = self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
            exclude_session_id=exclude_session_id,
        )
        _validate_prompt(prompt)
        _validate_cwd(cwd)
        unavailable_response: dict[str, Any] | None = None
        unavailable_error: Exception | None = None
        for candidate in candidates:
            engine, model, effort = candidate
            if engine not in SUPPORTED_ENGINES:
                raise ValueError(f"unsupported engine: {engine}")
            _validate_model_effort(model, effort)
            try:
                session = await self._backend(engine).start(
                    prompt,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=excluded,
                )
            except Exception as exc:
                # backendの起動自体が失敗した場合、成否は選んだ候補に依存する。
                unavailable_response, unavailable_error = None, exc
                excluded |= {candidate}
                continue
            session.engine = engine
            await self._await_start_outcome(session)
            response = {
                **session.public_status(),
                "model_type": model_type,
                "model": model,
                "effort": effort,
            }
            if not _engine_unavailable(session):
                return response
            unavailable_response, unavailable_error = response, None
            excluded |= {candidate}
        if unavailable_response is not None:
            return unavailable_response
        assert unavailable_error is not None
        raise unavailable_error

    async def _await_start_outcome(self, session: SessionState) -> None:
        """起動直後の可用性失敗を確定するため、上限付きで終端を待つ。

        上限内に終端しないsessionは通常の実行中として扱い、以降は`wait`が観測する。
        """
        if session.result_available:
            return
        with contextlib.suppress(TimeoutError):
            async with self._condition:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: session.result_available),
                    timeout=START_AVAILABILITY_TIMEOUT,
                )

    async def start_explore(
        self,
        fast: bool,
        prompt: str,
        cwd: str,
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """探索専用の軽量な起動条件でturnを開始する。"""
        model_type = "explore_fast" if fast else "explore"
        return await self.start(
            model_type,
            prompt,
            cwd,
            exclude_session_id,
            launch_kind="explore",
        )

    async def start_shell(
        self,
        command: str,
        cwd: str,
        summary_policy: str,
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """コマンド実行専用の軽量な起動条件でturnを開始する。"""
        _validate_shell_request(command, summary_policy)
        return await self.start(
            "explore_fast",
            _shell_prompt(command, summary_policy),
            cwd,
            exclude_session_id,
            launch_kind="shell",
        )

    async def _resolve_wait_timeout(self, request_bucket: str) -> float:
        """bucket別の既定待機上限を導出し、同じbucketの以降の呼び出しへ再利用する。

        導出は`claude auth status`の実行を伴い得るため、イベントループ上で直接実行しない。
        """
        cached = self._wait_timeouts.get(request_bucket)
        if cached is not None:
            return cached
        resolved = await asyncio.to_thread(_wait_schedule.get_wait_timeout, request_bucket)
        self._wait_timeouts[request_bucket] = resolved
        return resolved

    async def wait(self, session_id: str, timeout: float | None = None, request_bucket: str = "main") -> dict[str, Any]:
        """sessionの終端を待ち、登録簿の現在値から結果本文を返す。

        `timeout`が`None`の場合は、プロンプトキャッシュの保持期間から導出した上限を使う。
        """
        if timeout is None:
            timeout = await self._resolve_wait_timeout(request_bucket)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be non-negative")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        pending = self._pending_resumes.get(session_id)
        if pending is not None:
            if timeout == 0:
                return self._pending_resume_status(pending)
            try:
                await asyncio.wait_for(
                    asyncio.shield(pending.task),
                    timeout=max(0.0, deadline - loop.time()),
                )
            except TimeoutError:
                current = self._pending_resumes.get(session_id)
                if current is pending:
                    return self._pending_resume_status(pending)
                session = self.sessions.get(session_id)
                if session is not None:
                    return session.public_status(include_result=session.result_available)
                return self._get_session(session_id).public_status()
        session = self._get_session(session_id)
        if not session.result_available:
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(
                            lambda: (current := self.sessions.get(session_id)) is None or current.result_available
                        ),
                        timeout=max(0.0, deadline - loop.time()),
                    )
            except TimeoutError:
                pass
        session = self._get_session(session_id)
        return session.public_status(include_result=session.result_available)

    def _pending_resume_status(self, pending: _PendingResume) -> dict[str, Any]:
        """進行中の再開操作を通常のrunning状態として射影する。"""
        session = self.sessions.get(pending.state.session_id)
        if session is not None:
            return session.public_status(include_result=session.result_available)
        result: dict[str, Any] = {
            "session_id": pending.state.session_id,
            "engine": pending.state.engine,
            "status": "running",
            "progress": "",
        }
        if pending.state.model_type is not None:
            result["model_type"] = pending.state.model_type
        return result

    async def _run_resume(self, resume_state: SessionResumeState, prompt: ResumePrompt) -> SessionState:
        """backendの再開を完了し、失敗時だけ再試行用状態を復元する。"""
        session_id = resume_state.session_id
        backend = self._backend(resume_state.engine)
        try:
            return await backend.resume(
                session_id,
                prompt,
                resume_state.cwd,
                resume_state.model,
                resume_state.effort,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
            )
        except BaseException:
            if session_id not in self.sessions:
                self.expired_sessions[session_id] = resume_state
            raise
        finally:
            prompt.close()
            pending = self._pending_resumes.get(session_id)
            if pending is not None and pending.task is asyncio.current_task():
                self._pending_resumes.pop(session_id, None)

    @staticmethod
    def _consume_resume_exception(task: asyncio.Task[SessionState]) -> None:
        """timeout後に完了した管理対象taskの例外を回収する。"""
        with contextlib.suppress(asyncio.CancelledError):
            task.exception()

    def _start_resume(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None,
        previous_result_deadline: float | None,
    ) -> tuple[_PendingResume, int]:
        """期限切れ状態を一意な進行中再開操作へ原子的に移す。"""
        session_id = resume_state.session_id
        self.expired_sessions.pop(session_id, None)
        resume_prompt = ResumePrompt(prompt)
        task = asyncio.create_task(self._run_resume(resume_state, resume_prompt))
        pending = _PendingResume(
            state=resume_state,
            task=task,
            prompt=resume_prompt,
            previous_result=previous_result,
            previous_result_deadline=previous_result_deadline,
        )
        if previous_result is not None and previous_result_deadline is not None:
            loop = asyncio.get_running_loop()
            if loop.time() >= previous_result_deadline:
                pending.discard_previous_result()
            else:
                pending.previous_result_expiration = loop.call_at(
                    previous_result_deadline,
                    pending.discard_previous_result,
                )
        self._pending_resumes[session_id] = pending
        task.add_done_callback(self._consume_resume_exception)
        return pending, resume_prompt.initial_ticket

    async def _resume_response(self, pending: _PendingResume, ticket: int) -> dict[str, Any]:
        """同じ再開taskの配送結果を返し、呼び出し取消時は対応promptだけを外す。"""
        try:
            session = await asyncio.shield(pending.task)
        except asyncio.CancelledError:
            pending.prompt.cancel(ticket)
            raise
        delivery = "reply_failed" if session.result_available else "reply_started"
        response: dict[str, Any] = {
            "delivery": delivery,
            **session.public_status(include_result=session.result_available),
        }
        previous_result = pending.take_previous_result()
        if previous_result is not None:
            response["previous_result"] = previous_result
        return response

    async def _cancel_pending_resume(
        self,
        pending: _PendingResume,
        timeout: float,
    ) -> tuple[SessionState, bool]:
        """保留中の再開と配下作業を回収し、中断要求の受理有無を返す。"""
        pending.discard_previous_result()
        pending.prompt.close()
        cancellation_requested = not pending.task.done()
        if cancellation_requested:
            pending.task.cancel()
        outcomes = await asyncio.wait_for(
            asyncio.gather(pending.task, return_exceptions=True),
            timeout=timeout,
        )
        outcome = outcomes[0]
        if not isinstance(outcome, asyncio.CancelledError):
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome, outcome.interrupt_requested

        resume_state = pending.state
        session = self.sessions.get(resume_state.session_id)
        if session is None:
            session = SessionState(
                session_id=resume_state.session_id,
                cwd=resume_state.cwd,
                model=resume_state.model,
                effort=resume_state.effort,
                engine=resume_state.engine,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
            )
            self.sessions[session.session_id] = session
        self.expired_sessions.pop(session.session_id, None)
        if self._pending_resumes.get(session.session_id) is pending:
            self._pending_resumes.pop(session.session_id, None)
        session.status = "interrupted"
        session.turn_completed = True
        session.turn_start_ambiguous = False
        session.interrupt_requested = False
        session.touch()
        await self._notify_waiters()
        return session, False

    async def _resume_and_reply(
        self,
        resume_state: SessionResumeState,
        prompt: str,
        previous_result: dict[str, Any] | None = None,
        previous_result_deadline: float | None = None,
    ) -> dict[str, Any]:
        """保存済みの最小状態から同じ会話を再開し、公開契約の応答を返す。

        再開は結果保持期限の経過と所有主体の終了の双方を契機とする。
        結果本文を保持したまま所有主体だけが終了した場合は、直前結果を応答へ含める。
        """
        pending, ticket = self._start_resume(
            resume_state,
            prompt,
            previous_result,
            previous_result_deadline,
        )
        return await self._resume_response(pending, ticket)

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        timeout: float = DEFAULT_SEND_MESSAGE_TIMEOUT,
    ) -> dict[str, Any]:
        """実行中turnを継続し、終端済みなら同じsessionでreplyを開始する。"""
        _validate_prompt(prompt)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ValueError("timeout must be positive")
        route_state = self._route_state(session_id, unknown_label="session")
        if route_state.model_type is not None:
            candidates = _atk_config.resolve_model_candidates(route_state.model_type)
            expected = next((item for item in candidates if item not in route_state.excluded_candidates), None)
            actual = (route_state.engine, route_state.model, route_state.effort)
            if expected != actual:
                raise ValueError(f"configuration changed: {session_id}")
        try:
            async with asyncio.timeout(float(timeout)):
                while True:
                    async with self._resume_lock:
                        pending = self._pending_resumes.get(session_id)
                        if pending is not None:
                            ticket, changed = pending.prompt.submit_or_observe(prompt)
                            if ticket is not None:
                                return await self._resume_response(pending, ticket)
                            if changed is not None:
                                await changed.wait()
                            else:
                                await asyncio.shield(pending.task)
                            continue
                        retained = self.sessions.get(session_id)
                        if (
                            retained is not None
                            and retained.retention_deadline is not None
                            and asyncio.get_running_loop().time() >= retained.retention_deadline
                        ):
                            self._expire_session(session_id)
                        resume_state = self.expired_sessions.get(session_id)
                        if resume_state is not None:
                            return await self._resume_and_reply(resume_state, prompt)
                        session = self._get_session(session_id)
                    backend = self._backend(session.engine)
                    async with session.turn_control_lock:
                        if session.interrupt_requested and not session.terminal:
                            raise ValueError(f"session is being interrupted: {session_id}")
                    try:
                        result = await backend.send_message(session, prompt)
                    except SessionOwnerGoneError:
                        async with self._resume_lock:
                            if self.sessions.get(session_id) is not session:
                                continue
                            previous_result = session.previous_result() if session.result_available else None
                            previous_result_deadline = session.retention_deadline
                            self._expire_session(session_id)
                            resume_state = self.expired_sessions[session_id]
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                                previous_result_deadline,
                            )
                    delivery = result["delivery"]
                    if delivery in {"reply_started", "reply_ambiguous"}:
                        session.reset_progress()
                    response: dict[str, Any] = {"delivery": delivery, **session.public_status()}
                    if delivery in REPLY_DELIVERIES:
                        response["previous_result"] = result["previous_result"]
                    return response
        except TimeoutError as exc:
            raise TimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined, observe the session with wait"
            ) from exc

    async def kill(self, session_id: str, timeout: float = DEFAULT_KILL_TIMEOUT) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be non-negative")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout) if timeout > 0 else None
        delivery_deadline = deadline if deadline is not None else loop.time() + DEFAULT_SEND_MESSAGE_TIMEOUT
        pending = self._pending_resumes.get(session_id)
        if pending is not None:
            try:
                session, interrupt_requested = await self._cancel_pending_resume(
                    pending,
                    max(0.0, delivery_deadline - loop.time()),
                )
            except TimeoutError as exc:
                raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
            if interrupt_requested:
                response = session.public_status(include_result=True)
                response["kill_requested"] = True
                return response
        else:
            expired_response = self._expired_kill_response(session_id)
            if expired_response is not None:
                return expired_response
            session = self._get_session(session_id)
        started_terminal = session.terminal
        requested_before_call = session.interrupt_requested
        if started_terminal:
            response = session.public_status(include_result=True)
            response["kill_requested"] = False
            return response

        requested = requested_before_call
        backend = self._backend(session.engine)
        try:
            await asyncio.wait_for(
                session.turn_control_lock.acquire(),
                timeout=max(0.0, delivery_deadline - loop.time()),
            )
        except TimeoutError as exc:
            raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
        try:
            if session.terminal:
                requested = requested or requested_before_call or session.interrupt_requested
            elif session.interrupt_requested:
                requested = True
            else:
                if session.engine == "codex" and not session.turn_id:
                    if timeout == 0:
                        raise ValueError("the active Codex turn has no turn_id")
                    assert deadline is not None
                    try:
                        async with self._condition:
                            await asyncio.wait_for(
                                self._condition.wait_for(lambda: bool(session.turn_id) or session.terminal),
                                timeout=max(0.0, deadline - loop.time()),
                            )
                    except TimeoutError as exc:
                        raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
                    if session.terminal:
                        response = session.public_status(include_result=True)
                        response["kill_requested"] = False
                        return response
                session.interrupt_requested = True
                session.touch()
                try:
                    await asyncio.wait_for(
                        backend.interrupt(session),
                        timeout=max(0.0, delivery_deadline - loop.time()),
                    )
                except TimeoutError:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise TimeoutError(
                        f"kill timed out: {session_id}; interrupt delivery is undetermined, observe the session with wait"
                    ) from None
                except Exception:
                    session.interrupt_requested = False
                    session.touch()
                    await self._notify_waiters()
                    raise
                requested = True
        finally:
            session.turn_control_lock.release()

        if not requested:
            response = session.public_status(include_result=True)
            response["kill_requested"] = False
            return response
        if timeout > 0:
            assert deadline is not None
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=max(0.0, deadline - loop.time()),
                    )
            except TimeoutError as exc:
                raise TimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was delivered but the turn did not terminate"
                ) from exc
        response = session.public_status(include_result=session.result_available)
        response["kill_requested"] = True
        return response

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
        pending_resumes = tuple(self._pending_resumes.values())
        for pending in pending_resumes:
            pending.discard_previous_result()
        resume_tasks = tuple(pending.task for pending in pending_resumes)
        for task in resume_tasks:
            if not task.done():
                task.cancel()
        if resume_tasks:
            await asyncio.gather(*resume_tasks, return_exceptions=True)
        backends = tuple(backend for backend in (self._codex, self._claude) if backend is not None)
        for backend in backends:
            await backend.close()


_MANAGER = AgentsServerManager()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    try:
        yield
    finally:
        await _MANAGER.close()


with warnings.catch_warnings():
    if IncompleteFieldDefinitionWarning is not None:
        warnings.simplefilter("ignore", IncompleteFieldDefinitionWarning)
    mcp = FastMCP(
        "agents_server",
        instructions=(
            "CodexまたはClaudeへの非同期委譲。承認・停止・一覧操作は公開しない。\n"
            "`start`と`start_explore`でsessionを開始し、`start_shell`でコマンドの実行と要約を委譲する。"
            "`wait`で終端と結果本文を受け取る。継続は`send_message`、実行中turnの中断は`kill`で行う。\n"
            "`start`・`start_explore`・`start_shell`が返した`session_id`と、`send_message`で新しい指示を配送したsessionは、"
            "同じ応答の中で`wait`を発行して観測するか、結果が不要なら`kill`で破棄する。"
            "観測を試みていない作業を残したままターンを終えると、当該作業を観測する主体が残らない。\n"
            "engine、model及びeffortは`model_type`と`fast`から本サーバーが工程別モデル設定を解決して決める。"
            "呼び出し側は指定しない。"
        ),
        lifespan=_mcp_lifespan,
    )


@mcp.tool(name="start", structured_output=True)
async def start(
    model_type: Annotated[
        str,
        Field(
            description=(
                "工程別モデル設定の種別、又は設定値と同じ書式の候補列。"
                "候補列を直接渡した場合は設定を読まず、渡した候補をそのまま使う。"
            )
        ),
    ],
    prompt: str,
    cwd: str,
    exclude_session_id: Annotated[
        str | None,
        Field(
            description=(
                "既に起動したsessionのID。渡したsessionが選択した候補を除外集合へ加え、残る候補の先頭で起動する。"
                "同じ`model_type`で開始した通常起動のsessionだけを渡す。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """工程別モデル設定の候補から委譲先turnを開始する。

    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    返した`session_id`は同じ応答の中で`wait`を発行して観測するか、結果が不要なら`kill`で破棄する。
    応答は`session_id`と、採用した`model_type`、`engine`、`model`及び`effort`を含む。
    全候補が起動できない場合は`no model candidates remain for model_type: <model_type>`を返す。
    これは候補が尽きた状態であり設定の不備ではないため、同じ起動条件で再発行しない。
    """
    return await _MANAGER.start(model_type, prompt, cwd, exclude_session_id)


@mcp.tool(name="start_explore", structured_output=True)
async def start_explore(
    prompt: str,
    cwd: str,
    fast: Annotated[
        bool,
        Field(
            description=(
                "`false`は`explore_model`、`true`は`explore_fast_model`の設定を候補列として使う。"
                "既定の`true`のまま使い、軽量側の候補では判断材料が不足する調査だけ`false`を指定する。"
            )
        ),
    ] = True,
    exclude_session_id: Annotated[
        str | None,
        Field(
            description=(
                "既に起動したsessionのID。渡したsessionが選択した候補を除外集合へ加え、残る候補の先頭で起動する。"
                "同じ`fast`の値で開始した探索起動のsessionだけを渡す。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """探索専用の軽量な起動条件で委譲先turnを開始する。

    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    返した`session_id`は同じ応答の中で`wait`を発行して観測するか、結果が不要なら`kill`で破棄する。
    プロジェクト指示の読込を減らした軽量な起動条件で開始する。
    書込は機械的に禁止しないため、対象ファイルを変更しない旨を`prompt`へ明示する。
    委譲と直接実行の採算は、追加のツール呼び出しが2回以上必要か、読む対象の合計が4,000トークンを超えるかで判定する。
    いずれかに当たる調査は本ツールへ委譲し、1回の検索または1ファイルの部分読み取りで確定する調査は自ら実行する。
    この目安は、呼び出し元の1リクエストの文脈量147,000トークンと、セッションの残りリクエスト数47を前提とする。
    文脈量が小さいセッションの初期では直接実行が相対的に有利になる。
    応答と、候補が尽きた場合の扱いは`start`と同じである。
    """
    return await _MANAGER.start_explore(fast, prompt, cwd, exclude_session_id)


@mcp.tool(name="start_shell", structured_output=True)
async def start_shell(
    command: Annotated[str, Field(description="実行するコマンド。委譲先がシェルで実行する。")],
    cwd: Annotated[str, Field(description="実行時の作業ディレクトリ。既存ディレクトリの絶対パスとする。")],
    summary_policy: Annotated[str, Field(description="結果の要約方針。報告へ含める値と粒度を書く。")],
    exclude_session_id: Annotated[
        str | None,
        Field(
            description=(
                "既に起動したsessionのID。渡したsessionが選択した候補を除外集合へ加え、残る候補の先頭で起動する。"
                "シェル実行起動のsessionだけを渡す。"
            )
        ),
    ] = None,
) -> dict[str, Any]:
    """コマンドを実行して結果を要約する委譲先turnを開始する。

    `start_explore`と同じ軽量な起動条件で開始し、呼び出し元へは終了状態と要約だけを返す。
    読み取り専用の制約は課さないため、検査コマンドなど対象を変更する実行を渡せる。
    返した`session_id`は同じ応答の中で`wait`を発行して観測するか、結果が不要なら`kill`で破棄する。
    委譲と直接実行の採算は、コマンドの出力量で判定する。
    出力が4,000トークン（英数字主体で約16,000バイト、300行程度）を超える見込みのコマンドは本ツールへ委譲し、
    1,000トークン未満に収まる見込みのコマンドは自ら実行する。
    この目安は、呼び出し元の1リクエストの文脈量147,000トークンと、セッションの残りリクエスト数47を前提とする。
    文脈量が小さいセッションの初期では直接実行が相対的に有利になる。
    応答と、候補が尽きた場合の扱いは`start`と同じである。
    """
    return await _MANAGER.start_shell(command, cwd, summary_policy, exclude_session_id)


@mcp.tool(name="wait", structured_output=True)
async def wait(
    session_id: str,
    timeout: Annotated[
        float | None,
        Field(
            description="待機上限秒数。省略するとプロンプトキャッシュの保持期間から導出した上限を使う。0は待機せず現状態を返す。"
        ),
    ] = None,
    request_bucket: Annotated[
        str,
        Field(description="既定timeoutの導出に使うrequest bucket。呼び出し元がサブエージェントの場合だけ`subagent`を渡す。"),
    ] = "main",
) -> dict[str, Any]:
    """委譲先の終端を待ち、終端時だけ結果本文を返す。

    `timeout`を省略した場合の既定は、プロンプトキャッシュの保持期間から導出した上限とする。
    固有のtimeout要件がなければ`timeout`を省略する。`timeout=0`は待機せず現状態を返す。
    委譲先が背景作業を残してturnを終えた場合は、同じsessionを一度だけ自動的に再開し、再開したturnの終端まで待つ。
    呼び出し元は背景作業の完了後に`send_message`で再開を指示しない。
    終端前に`status: running`が返った場合は、同じ`session_id`へ`wait`を再発行して待機を継続する。
    `session retention expired: <session_id>`は終端結果の保持期限が過ぎたことだけを示し、会話再開用の最小状態は保持されている。
    """
    return await _MANAGER.wait(session_id, timeout, request_bucket)


@mcp.tool(name="send_message", structured_output=True)
async def send_message(
    session_id: str,
    prompt: str,
    timeout: Annotated[
        float,
        Field(
            description="継続要求の配送結果が確定するまでの待機上限秒数。固有のtimeout要件がなければ引数を省略して通常既定を使う。委譲先の応答生成の完了は待たない。0以下は受理しない。"
        ),
    ] = DEFAULT_SEND_MESSAGE_TIMEOUT,
) -> dict[str, Any]:
    """実行中turnへ追加指示を送り、終端済みなら同じsessionでreplyを開始する。

    通常の既定は270秒である。固有のtimeout要件がなければ引数を省略して通常既定を使う。
    待つのは継続要求の配送結果が確定するまでであり、委譲先の応答生成の完了ではない。
    上限に達した場合は配送の成否が確定しないため、`wait`で状態を確認する。
    実行中turnにはsteerし、終端済みturnでは結果回収を前提にせず同じsessionのreplyを開始する。
    保持期限を過ぎた場合と、sessionを所有する実行主体が終了している場合も、保持済みの最小状態から会話を暗黙に再開する。
    応答は`delivery`で配送結果を示し、保持中のreply開始時は直前結果を`previous_result`へ含める。
    `configuration changed: <session_id>`は工程別モデル設定の候補列が変わったことを示すため、検収済み状態を渡して新規起動する。
    `unknown session: <session_id>`だけが継続不能を示す。
    """
    return await _MANAGER.send_message(session_id, prompt, timeout)


@mcp.tool(name="kill", structured_output=True)
async def kill(
    session_id: str,
    timeout: Annotated[
        float,
        Field(
            description="中断要求後に終端を待つ上限秒数。固有のtimeout要件がなければ引数を省略して通常既定を使う。0は中断要求配送後の現状態を返す。"
        ),
    ] = DEFAULT_KILL_TIMEOUT,
) -> dict[str, Any]:
    """実行中turnへ中断を要求し、指定時間まで終端を待つ。

    停止は最終手段とする。実行中の委譲先には`send_message`で訂正を配送できるため、
    そちらで意図を満たせる場合は、停止によって失われる作業と再起動の費用の方が大きい。
    本ツールを選ぶ前に、`send_message`による訂正では足りないことと、当該作業の継続自体が不要であることを確認する。
    通常の既定は270秒である。固有のtimeout要件がなければ引数を省略して通常既定を使う。
    `timeout=0`は中断要求配送後の現状態を返す。
    timeoutに達した場合もsessionとbackend processは破棄しないため、`wait`で状態を確認してから次の操作を選ぶ。
    終端結果の保持期限を過ぎたsessionでは中断する実行中turnが無いため、`status`へ`expired`、`progress`へ空文字列、`kill_requested`へ`false`を設定した応答を返す。応答の項目は他の成功応答と同じとする。
    """
    return await _MANAGER.kill(session_id, timeout)


def _prepare_child_environment() -> None:
    """起動元ツールのエフェメラル仮想環境を、以降に起動する委譲先から取り除く。

    Claude backendが渡す`ClaudeAgentOptions.env`は継承環境へ重なる仕様であり、
    キーの削除を表現できない。Codex backendのApp Server子プロセスも本プロセスの環境を継承する。
    このため両経路の起点である本プロセスの環境を、起動時に1回だけ整える。
    """
    _inherited_venv.strip_inherited_venv(os.environ)


def main(argv: Sequence[str] | None = None) -> int:
    """引数に応じて依存検査またはMCP stdio transportを起動する。"""
    _prepare_child_environment()
    logging.basicConfig(level=os.environ.get("AGENT_TOOLKIT_AGENTS_LOG_LEVEL", "WARNING"))
    parser = argparse.ArgumentParser(description="CodexとClaudeの委譲先を非同期MCPとして公開する。")
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Claude Agent SDKの依存を読み込み、options構築まで検査する。",
    )
    args = parser.parse_args(argv)
    if args.check_dependencies:
        claude_backend.check_dependencies()
        return 0
    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
