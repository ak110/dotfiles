"""委譲先sessionの開始、継続、中断と停止を受け付け、engine別のbackendと共有のsession状態を管理する。"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
from typing import Any

from agent_toolkit._agents_server import (
    backends,
    launch_requests,
    manager_wait,
    result_projection,
    state,
    tool_names,
    unavailable_candidates,
)
from agent_toolkit._agents_server.input_validation import (
    validate_cwd,
    validate_model_effort,
    validate_prompt,
    validate_shell_request,
)
from agent_toolkit._agents_server.launch_requests import (
    MODEL_TYPE_NEXT_ACTION,
    shell_default_label,
    wrap_delivery_body,
)
from agent_toolkit._agents_server.responses import (
    KILL_NOT_DELIVERED_NEXT_ACTION,
    excluded_candidate_payload,
    resolve_display_label,
)
from agent_toolkit._agents_server.session_errors import (
    ActionableRuntimeError,
    ActionableTimeoutError,
    SessionInitializationTimeoutError,
    SessionOwnerGoneError,
)
from agent_toolkit._agents_server.state import (
    LaunchKind,
    ModelCandidate,
    SessionResumeState,
    SessionState,
    remove_lifecycle_listener,
    remove_terminal_listener,
    remove_touch_listener,
)
from agent_toolkit._agents_server.tool_descriptions import shell_prompt
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import codex_models
from agent_toolkit._common.next_action import ActionableError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


DEFAULT_KILL_TIMEOUT = 270.0


DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0


REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})


# 起動直後の可用性失敗を確定するために`start`が終端を待つ上限秒数。
# Codex CLI 0.152.0で利用上限に達した状態のturnは、backendの起動応答から3.84〜4.27秒後に
# `turn/completed`で失敗した（2026-09-02、`explore_fast`候補`gpt-5.6-terra/medium`で3回測定）。
# 再検証は同じ失敗状態で`AppServerManager.start`を呼び、応答から終端までの経過を測る。
# 可用性失敗は最初のモデル出力より前に生じるため、正常起動ではモデル出力の観測で上限を待たずに打ち切る。
# Claudeでは、失敗した要求が始めないAPIの応答開始（`message_start`）をモデル出力の観測として扱う。
START_AVAILABILITY_TIMEOUT = 15.0


class AgentsServerManager(manager_wait.ManagerWait):
    """エンジン別バックエンドと共有session状態を管理する。"""

    def activate(self) -> None:
        """状態ファイル出力を有効化する。"""
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def _monitor_auto_resume(self) -> None:
        """公開待機操作に依存せず、孫session終端後の自動再開を進める。"""
        while True:
            awaiting = [session for session in self.sessions.values() if session.awaiting_auto_resume]
            for session in awaiting:
                await self._advance_child_session_wait(session)
            await asyncio.sleep(0.1 if awaiting else 1.0)

    async def _resolve_start_candidates(
        self,
        model_type: str,
        *,
        launch_kind: LaunchKind,
    ) -> tuple[list[ModelCandidate], dict[ModelCandidate, str]]:
        """起動条件を検証し、除外後の候補列を設定順で返す。

        除外中の候補は状態ディレクトリに保存した記録を読む。
        除外後に候補が残らない場合は、記録を無視して全候補を設定順で返す。
        """
        try:
            candidates = _atk_config.parse_unresolved_model_candidates(model_type)
        except ValueError as error:
            raise ActionableError(str(error), next_action=MODEL_TYPE_NEXT_ACTION) from error
        if codex_models.needs_catalog(candidates):
            catalog = await backends.model_catalog(self._backend(backends.CODEX_ENGINE)).list_models()
            candidates = codex_models.resolve_candidates(candidates, catalog)
        if not candidates:
            raise ActionableError(
                f"no model candidates remain for model_type: {model_type}", next_action=MODEL_TYPE_NEXT_ACTION
            )
        unsupported = {
            candidate: "Antigravityは読み取り専用のexploreに対応していません"
            for candidate in candidates
            if launch_kind == "explore" and candidate[0] == "agy"
        }
        candidates = [candidate for candidate in candidates if candidate not in unsupported]
        if not candidates:
            raise ActionableError(
                "no model candidates remain: " + "; ".join(unsupported.values()), next_action=MODEL_TYPE_NEXT_ACTION
            )
        recorded = unavailable_candidates.load_unavailable_candidates(
            model_type,
            launch_kind,
            now=datetime.datetime.now(datetime.UTC),
        )
        excluded = {
            candidate: recorded[candidate]
            for candidate in candidates
            if candidate in recorded and backends.excludes_with_recorded_reason(candidate[0], recorded[candidate])
        }
        remaining = [item for item in candidates if item not in excluded]
        if not remaining:
            return candidates, unsupported
        return remaining, excluded | unsupported

    async def start(
        self,
        model_type: str,
        prompt: str,
        cwd: str,
        *,
        launch_kind: LaunchKind = "delegate",
        label: str | None = None,
    ) -> dict[str, Any]:
        """工程別モデル設定の候補を先頭から試し、起動できたturnを返す。

        起動直後にengineの可用性を理由として終端した候補とagyの失敗候補を
        除外集合へ加えて次候補へ進む。agy以外のbackend起動例外では候補を進めない。
        Claude/Codexで候補を変えても結果が変わらない失敗は、そのまま委譲元へ返す。
        候補を除外して後続の候補で成立した場合は、除外した候補と除外の根拠を応答へ加える。
        """
        validate_prompt(prompt)
        validate_cwd(cwd)
        await launch_requests.check_plugin_commands(cwd)
        candidates, excluded = await self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
        )
        unavailable_response: dict[str, Any] | None = None
        unavailable_session: SessionState | None = None
        excluded_session_ids: dict[ModelCandidate, str] = {}
        display_label = resolve_display_label(label, prompt)
        delivery_body = wrap_delivery_body(prompt)
        for candidate_index, candidate in enumerate(candidates):
            engine, model, effort = candidate
            backend_type = backends.backend_class(engine)
            if backend_type is None:
                raise ActionableError(f"unsupported engine: {engine}", next_action=MODEL_TYPE_NEXT_ACTION)
            validate_model_effort(model, effort)
            try:
                session = await self._start_until_initialized(
                    engine,
                    delivery_body,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=frozenset(excluded),
                )
            except Exception as exc:
                if not backend_type.START_FAILURE_EXCLUDES_CANDIDATE:
                    raise
                reason = str(exc) or type(exc).__name__
                excluded[candidate] = reason
                unavailable_response = None
                unavailable_session = None
                _LOG.warning("agy_start_failed model_type=%s model=%s reason=%s", model_type, model, reason)
                continue
            session.engine = engine
            # テストや独自のMCPクライアントから起動して親のセッション記録に起動結果が残らない委譲先も、
            # `atk serve`の一覧が親の下へ置けるよう、作成時点の委譲元を登録簿へ記録する。
            session.launcher_session_id = self._launcher_session_id()
            if session.status == "starting":
                session.status = "running"
                session.touch()
                if self._status_writer is not None:
                    self._status_writer.flush()
            else:
                session.touch()
            await self._await_start_outcome(session)
            # 以後に過負荷で終端したturnは候補切替ではなく同じsessionの自動継続で扱う。
            session.availability_checked = True
            response: dict[str, Any] = {
                "session_id": session.session_id,
                "status": session.status,
                "engine": engine,
                "model_type": model_type,
                "model": model,
                "effort": effort,
            }
            response.update(state.fast_mode_fields(session.engine, session.fast_mode))
            if excluded:
                response["excluded_candidates"] = excluded_candidate_payload(excluded, excluded_session_ids)
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
            unavailable_reason = self._unavailable_reason(session)
            if unavailable_reason is None:
                unavailable_candidates.clear_unavailable_candidate(
                    model_type,
                    launch_kind,
                    candidate,
                    now=datetime.datetime.now(datetime.UTC),
                )
                if excluded:
                    _LOG.info(
                        "engine_switch session_id=%s model_type=%s launch_kind=%s excluded=%s selected=%s",
                        session.session_id,
                        model_type,
                        launch_kind,
                        json.dumps(excluded_candidate_payload(excluded, excluded_session_ids), ensure_ascii=False),
                        json.dumps({"engine": engine, "model": model, "effort": effort}, ensure_ascii=False),
                    )
                session.label = display_label
                session.prompt = prompt
                session.announced = True
                session.touch()
                if self._status_writer is not None:
                    self._status_writer.flush()
                _LOG.info(
                    "session_transition event=start session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                    session.session_id,
                    session.status,
                    session.turn_seq,
                )
                response["label"] = session.label
                return response
            unavailable_response, unavailable_session = response, session
            excluded[candidate] = unavailable_reason
            if candidate_index + 1 < len(candidates):
                excluded_session_ids[candidate] = session.session_id
                await self._abandon_unavailable_session(session)
        if unavailable_response is not None:
            assert unavailable_session is not None
            unavailable_response["excluded_candidates"] = excluded_candidate_payload(excluded, excluded_session_ids)
            unavailable_session.label = display_label
            unavailable_session.prompt = prompt
            unavailable_session.announced = True
            unavailable_session.touch()
            if self._status_writer is not None:
                self._status_writer.flush()
            _LOG.info(
                "session_transition event=start session_id=%s writer=mcp-manager status=%s turn_seq=%d",
                unavailable_session.session_id,
                unavailable_session.status,
                unavailable_session.turn_seq,
            )
            unavailable_response["label"] = unavailable_session.label
            return unavailable_response
        raise ActionableRuntimeError(
            "no available model candidates: "
            f"{model_type}; excluded={excluded_candidate_payload(excluded, excluded_session_ids)}",
            next_action=(
                "時間をおいて再試行するか、`model_type`へ別の候補を明示して起動する。"
                "除外した候補は`excluded`の理由（CLIの導入・認証・利用上限など）を解消すると再び使える"
            ),
        )

    async def _start_until_initialized(
        self,
        engine: str,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        model_type: str,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
    ) -> SessionState:
        """初期化の上限超過だけを同じ候補で再試行し、全試行の超過を例外で確定する。

        上限超過はbackendがそのsessionの資源を解放してから返るため、再試行は新しい起動として成立する。
        上限超過が続けば例外を上位へ返し、engineごとの候補切替条件を適用する。
        """
        last_timeout: SessionInitializationTimeoutError | None = None
        timeout_diagnostics: list[str] = []
        for attempt in range(1, state.SESSION_INITIALIZATION_ATTEMPTS + 1):
            _LOG.info(
                "session初期化を開始します: engine=%s attempt=%d/%d model_type=%s launch_kind=%s",
                engine,
                attempt,
                state.SESSION_INITIALIZATION_ATTEMPTS,
                model_type,
                launch_kind,
            )
            try:
                session = await self._backend(engine).start(
                    prompt,
                    cwd,
                    model,
                    effort,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    excluded_candidates=excluded_candidates,
                )
                _LOG.info(
                    "session初期化が完了しました: engine=%s attempt=%d/%d session_id=%s",
                    engine,
                    attempt,
                    state.SESSION_INITIALIZATION_ATTEMPTS,
                    session.session_id,
                )
                return session
            except SessionInitializationTimeoutError as exc:
                last_timeout = exc
                timeout_diagnostics.append(f"attempt={attempt}: {exc}")
                _LOG.warning(
                    "session初期化timeout: engine=%s attempt=%d/%d exception_type=%s exception=%s",
                    engine,
                    attempt,
                    state.SESSION_INITIALIZATION_ATTEMPTS,
                    type(exc).__name__,
                    exc,
                )
                if attempt < state.SESSION_INITIALIZATION_ATTEMPTS:
                    _LOG.warning("session初期化が上限へ達したため同じ候補で再試行します: engine=%s, 試行=%d", engine, attempt)
        assert last_timeout is not None
        raise SessionInitializationTimeoutError(
            f"{engine} session initialization timed out on every attempt: "
            f"attempts={state.SESSION_INITIALIZATION_ATTEMPTS}, cwd={cwd}, "
            f"model_type={model_type}, launch_kind={launch_kind}, "
            f"diagnostics=[{'; '.join(timeout_diagnostics)}]"
        ) from last_timeout

    async def _abandon_unavailable_session(self, session: SessionState) -> None:
        """次候補へ進む前に可用性失敗sessionの全資源を解放する。"""
        await self._backend(session.engine).release_session(session)
        self._cancel_retention_timer(session.session_id)
        self.sessions.pop(session.session_id, None)
        if self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="start-unavailable")
            self._status_writer.schedule()

    async def _await_start_outcome(self, session: SessionState) -> None:
        """起動直後の可用性失敗を確定するため、上限付きで終端を待つ。

        engineの可用性失敗は最初のモデル出力より前に生じるため、モデル出力を観測した時点で待機を打ち切る。
        Claudeでは、APIの応答開始（`message_start`）をモデル出力の観測とする。
        Weekly limitか5時間の利用上限の解除待ちへ入った時点でも打ち切り、`start`は`running`を返す。
        上限内に終端もモデル出力もしないsessionと、打ち切ったsessionは通常の実行中として扱い、
        以降は`atk agents wait`が観測する。
        """
        if self._start_outcome_observed(session):
            return
        with contextlib.suppress(TimeoutError):
            async with self._condition:
                await asyncio.wait_for(
                    self._condition.wait_for(lambda: self._start_outcome_observed(session)),
                    timeout=START_AVAILABILITY_TIMEOUT,
                )

    @staticmethod
    def _start_outcome_observed(session: SessionState) -> bool:
        """起動直後の可用性を確定できたかを返す。

        利用上限の解除待ちへ入ったsessionは、別の候補へ切り替えず同じsessionで待つため、`running`として確定する。
        """
        return session.result_available or session.model_output_observed or session.usage_limit_resume_at is not None

    async def start_explore(
        self,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """探索専用の軽量な起動条件でturnを開始する。"""
        return await self.start(
            model_type or tool_names.START_MODE_MODEL_TYPES["explore"],
            prompt,
            cwd,
            launch_kind="explore",
            label=resolve_display_label(label, "explore"),
        )

    async def start_shell(
        self,
        command: str,
        cwd: str,
        summary_policy: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """コマンド実行専用の軽量な起動条件でturnを開始する。"""
        validate_shell_request(command, summary_policy)
        return await self.start(
            model_type or tool_names.START_MODE_MODEL_TYPES["shell"],
            shell_prompt(command, summary_policy),
            cwd,
            launch_kind="shell",
            label=resolve_display_label(label, shell_default_label(command)),
        )

    async def start_write(
        self,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
        """対象、読者、事実と根拠が確定済みの文章起草・書込turnを開始する。"""
        return await self.start(
            model_type or tool_names.START_MODE_MODEL_TYPES["write"],
            prompt,
            cwd,
            launch_kind="write",
            label=resolve_display_label(label, "write"),
        )

    async def send_message(
        self,
        session_id: str,
        prompt: str,
        timeout: float = DEFAULT_SEND_MESSAGE_TIMEOUT,
    ) -> dict[str, Any]:
        """実行中turnを継続し、終端済みなら同じsessionでreplyを開始する。

        状態ファイルの書込主体を持つ場合は、`start`・`list`と同じく`root_session_id`を応答へ加える。
        再起動した会話が既存sessionへの`send_message`から再開しても、PostToolUseがこの値で別名索引を書けるようにするためである。
        """
        response = await self._deliver_message(session_id, prompt, timeout)
        if self._status_writer is not None:
            response = {**response, "root_session_id": self._status_writer.root_session_id}
        return response

    async def _deliver_message(self, session_id: str, prompt: str, timeout: float) -> dict[str, Any]:
        """`send_message`の配送本体。応答の`root_session_id`は呼び出し元が加える。"""
        validate_prompt(prompt)
        await self.take_over_orphaned_session(session_id)
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
            raise ActionableError(
                "timeout must be positive", next_action="`timeout`を省略して270秒で待つか、正の秒数を指定する"
            )
        prompt = wrap_delivery_body(prompt)
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
                        stopped_state = self._resolve_stopped_session(session_id)
                        if stopped_state is None:
                            stopped_state = self._restore_registry_session(session_id)
                        resume_state = stopped_state or self.expired_sessions.get(session_id)
                        if resume_state is not None:
                            previous_result = None
                            if stopped_state is not None:
                                previous_result = self._take_stopped_result(
                                    session_id,
                                    stopped_state,
                                    collector="send-message",
                                )
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                        session = self._get_session(session_id)
                    backend = self._backend(session.engine)
                    async with session.turn_control_lock:
                        if session.interrupt_requested and not session.terminal:
                            raise ActionableError(
                                f"session is being interrupted: {session_id}",
                                next_action=result_projection.RESEND_AFTER_WAIT_NEXT_ACTION,
                            )
                    try:
                        result = await backend.send_message(session, prompt)
                    except SessionOwnerGoneError:
                        async with self._resume_lock:
                            if self.sessions.get(session_id) is not session:
                                continue
                            previous_result = session.previous_result() if session.result_available else None
                            self._expire_session(session_id)
                            resume_state = self.expired_sessions[session_id]
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                    if self._status_writer is not None:
                        self._status_writer.delete_result(session_id, collector="send-message")
                    delivery = result["delivery"]
                    if delivery in {"reply_started", "reply_ambiguous"}:
                        session.reset_progress()
                    response: dict[str, Any] = {"delivery": delivery, "label": session.label}
                    if delivery in REPLY_DELIVERIES:
                        previous_result = result["previous_result"]
                        if previous_result:
                            response["previous_result"] = previous_result
                    return response
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined",
                next_action="`atk agents wait`で状態を確認し、指示が届いていないと判断した場合だけ`send_message`を再送する",
            ) from exc

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
    ) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ActionableError(
                "timeout must be non-negative", next_action="`timeout`を省略して270秒で待つか、0以上の秒数を指定する"
            )
        stopped_state = self._resolve_stopped_session(session_id)
        recovered_state: SessionResumeState | None = None
        if stopped_state is None:
            recovered_state = self._restore_registry_session(session_id)
            stopped_state = recovered_state
        if recovered_state is not None:
            response = self._recovered_result_response(recovered_state)
            response["kill_requested"] = False
            return response
        if stopped_state is not None:
            stopped_response = self._take_stopped_result(session_id, stopped_state, collector="kill")
            if stopped_response is not None:
                stopped_response["kill_requested"] = False
                notices = self._take_notices(session_id)
                return self._response_with_notices(stopped_response, notices)
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
                raise ActionableTimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was not delivered",
                    next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
                ) from exc
            if interrupt_requested:
                response = self._kill_result_response(session, kill_requested=True)
                return await self._stop_after_terminal_response(session_id, response, stop)
        else:
            expired_response = self._expired_kill_response(session_id)
            if expired_response is not None:
                return await self._stop_after_terminal_response(session_id, expired_response, stop)
            session = self._get_session(session_id)
        started_terminal = session.terminal
        requested_before_call = session.interrupt_requested
        if started_terminal:
            response = self._kill_result_response(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)

        requested = requested_before_call
        backend = self._backend(session.engine)
        try:
            await asyncio.wait_for(
                session.turn_control_lock.acquire(),
                timeout=max(0.0, delivery_deadline - loop.time()),
            )
        except TimeoutError as exc:
            raise ActionableTimeoutError(
                f"kill timed out: {session_id}; the interrupt request was not delivered",
                next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
            ) from exc
        try:
            if session.terminal:
                requested = requested or requested_before_call or session.interrupt_requested
            elif session.interrupt_requested:
                requested = True
            else:
                if backends.interrupt_requires_turn_id(session.engine) and not session.turn_id:
                    if timeout == 0:
                        raise ActionableError(
                            "the active Codex turn has no turn_id",
                            next_action="`timeout`へ正の秒数を指定して`kill`を再発行するか、`atk agents wait`で終端を観測する",
                        )
                    assert deadline is not None
                    try:
                        async with self._condition:
                            await asyncio.wait_for(
                                self._condition.wait_for(lambda: bool(session.turn_id) or session.terminal),
                                timeout=max(0.0, deadline - loop.time()),
                            )
                    except TimeoutError as exc:
                        raise ActionableTimeoutError(
                            f"kill timed out: {session_id}; the interrupt request was not delivered",
                            next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
                        ) from exc
                    if session.terminal:
                        response = self._kill_result_response(session, kill_requested=False)
                        return await self._stop_after_terminal_response(session_id, response, stop)
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
                    raise ActionableTimeoutError(
                        f"kill timed out: {session_id}; interrupt delivery is undetermined",
                        next_action=KILL_NOT_DELIVERED_NEXT_ACTION,
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
            response = self._kill_result_response(session, kill_requested=False)
            return await self._stop_after_terminal_response(session_id, response, stop)
        if timeout > 0:
            assert deadline is not None
            try:
                async with self._condition:
                    await asyncio.wait_for(
                        self._condition.wait_for(lambda: session.result_available),
                        timeout=max(0.0, deadline - loop.time()),
                    )
            except TimeoutError as exc:
                raise ActionableTimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was delivered but the turn did not terminate",
                    next_action="中断要求は配送済みのため`kill`を再発行しない。`atk agents wait`で終端を観測する",
                ) from exc
        response = self._kill_result_response(session, kill_requested=True)
        return await self._stop_after_terminal_response(session_id, response, stop)

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
        remove_terminal_listener(self._schedule_session_expiry)
        remove_lifecycle_listener(self._cancel_expiry_on_start)
        for timer in self._retention_timers.values():
            timer.cancel()
        self._retention_timers.clear()
        if self._resource_release_tasks:
            await asyncio.gather(*self._resource_release_tasks.values(), return_exceptions=True)
            self._resource_release_tasks.clear()
        if self._auto_resume_task is not None:
            self._auto_resume_task.cancel()
            await asyncio.gather(self._auto_resume_task, return_exceptions=True)
            self._auto_resume_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        pending_resumes = tuple(self._pending_resumes.values())
        for pending in pending_resumes:
            pending.discard_previous_result()
        resume_tasks = tuple(pending.task for pending in pending_resumes)
        for task in resume_tasks:
            if not task.done():
                task.cancel()
        if resume_tasks:
            await asyncio.gather(*resume_tasks, return_exceptions=True)
        for backend in tuple(self._backends.values()):
            await backend.close()
        self._publish_closed_sessions()
        remove_terminal_listener(self._carry_over_unavailable_candidate)
        remove_terminal_listener(self._record_pending_unobserved_child_sessions)
        remove_lifecycle_listener(self._record_resources)
        if self._status_writer is not None:
            remove_touch_listener(self._status_writer.schedule)
            self._status_writer.deactivate()
