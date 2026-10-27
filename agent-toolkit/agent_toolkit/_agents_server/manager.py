"""委譲先sessionの開始、継続、中断と停止を受け付け、engine別のbackendと共有のsession状態を管理する。"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import pathlib
from collections.abc import Mapping
from typing import Any, Self

from agent_toolkit._agents_server import (
    backends,
    launch_requests,
    lifetime,
    logging_config,
    manager_wait,
    result_projection,
    session_handoff,
    session_identity,
    state,
    status_file,
    tool_names,
    unavailable_candidates,
    wait_targets,
)
from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
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
from agent_toolkit._common import codex_models, read_triggers, session_release
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.plugin_roots import PluginRootError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


DEFAULT_KILL_TIMEOUT = 270.0


DEFAULT_SEND_MESSAGE_TIMEOUT = 270.0


REPLY_DELIVERIES = frozenset({"reply_started", "reply_failed", "reply_ambiguous"})


START_AVAILABILITY_TIMEOUT = state.START_AVAILABILITY_TIMEOUT


class AgentsServerManager(manager_wait.ManagerWait):
    """エンジン別バックエンドと共有session状態を管理する。"""

    _close_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exception: object) -> None:
        await self.close()

    def activate(self) -> None:
        """状態ファイル出力を有効化する。"""
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def activate_async(self) -> None:
        """初回保存を待ってから常駐監視を開始する。"""
        if self._status_writer is not None:
            await self._status_writer.activate_async()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())
        self._auto_resume_task = asyncio.create_task(self._monitor_auto_resume())

    async def _monitor_auto_resume(self) -> None:
        """公開待機操作が呼ばれるかによらず、孫session終了後の自動再開を進める。"""
        while True:
            await session_release.process_requests(
                self.sessions,
                self.stop,
                state_root=None if self._status_writer is None else self._status_writer.state_root,
            )
            awaiting = [session for session in self.sessions.values() if session.awaiting_auto_resume]
            for session in awaiting:
                await self._advance_child_session_wait(session)
            await asyncio.sleep(0.1 if awaiting else 1.0)

    async def _resolve_start_candidates(
        self,
        model_type: str,
        *,
        launch_kind: LaunchKind,
        excluded_candidates: Mapping[ModelCandidate, str] | None = None,
    ) -> tuple[list[ModelCandidate], dict[ModelCandidate, str]]:
        """起動条件を検証し、除外後の候補列を設定順で返す。

        除外中の候補は状態ディレクトリに保存した記録を読む。
        除外後に候補が残らない場合は、記録を無視して全候補を設定順で返す。
        """
        await self._drain_candidate_records()
        try:
            candidates = await run_file_operation(_atk_config.parse_unresolved_model_candidates, model_type)
        except ValueError as error:
            raise ActionableError(str(error), next_action=MODEL_TYPE_NEXT_ACTION) from error
        if codex_models.needs_catalog(candidates):
            catalog = await backends.model_catalog(self._backend(backends.CODEX_ENGINE)).list_models()
            candidates = codex_models.resolve_candidates(candidates, catalog)
        if not candidates:
            raise ActionableError(
                f"no model candidates remain for model_type: {model_type}", next_action=MODEL_TYPE_NEXT_ACTION
            )
        unsupported: dict[ModelCandidate, str] = {}
        forced = dict(excluded_candidates or {})
        unsupported.update(forced)
        candidates = [candidate for candidate in candidates if candidate not in unsupported]
        if not candidates:
            raise ActionableError(
                "no model candidates remain: " + "; ".join(unsupported.values()), next_action=MODEL_TYPE_NEXT_ACTION
            )
        recorded = await run_file_operation(
            unavailable_candidates.load_unavailable_candidates,
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
        excluded_candidates: Mapping[ModelCandidate, str] | None = None,
        startup_document: pathlib.Path | None = None,
        startup_inputs: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """モデル設定の候補を先頭から試し、起動できたturnを返す。

        `model_type`は工程別設定または直接指定された候補列を表す。

        起動直後にengineの可用性を理由として終了した候補とagyの失敗候補を
        除外集合へ加えて次候補へ進む。Claudeの依存読込失敗も型付きの候補不成立として扱う。
        engine共通能力の不成立は同じ要求内の同engine候補を全て除外し、永続記録へ渡さない。
        Claude/Codexで候補を変えても結果が変わらない失敗は、そのまま委譲元へ返す。
        候補を除外して後続の候補で成立した場合は、除外した候補と除外の根拠を応答へ加える。
        """
        validate_prompt(prompt)
        await self._drain_diagnostics()
        validate_cwd(cwd)
        await launch_requests.check_plugin_commands(cwd)
        candidates, excluded = await self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
        )
        unavailable_response: dict[str, Any] | None = None
        unavailable_session: SessionState | None = None
        excluded_session_ids: dict[ModelCandidate, str] = {}
        display_label = resolve_display_label(label, prompt)
        for candidate_index, candidate in enumerate(candidates):
            if candidate in excluded:
                continue
            engine, model, effort = candidate
            backend_type = backends.backend_class(engine)
            if backend_type is None:
                raise ActionableError(f"unsupported engine: {engine}", next_action=MODEL_TYPE_NEXT_ACTION)
            validate_model_effort(model, effort)
            # 候補ごとに子のhost/cwdで資料を解決する。前候補の資料を別engineへ再利用しない。
            try:
                initial_documents = (
                    await run_file_operation(
                        read_triggers.startup_context,
                        startup_document,
                        startup_document.parent.parent,
                        dict(startup_inputs or {}),
                        host=engine,
                        project=cwd,
                    )
                    if startup_document is not None and launch_kind == "delegate"
                    else ""
                )
                delivery_body = wrap_delivery_body(launch_requests.with_startup_documents(prompt, initial_documents))
            except PluginRootError as exc:
                reason = str(exc)
                excluded.update({item: reason for item in candidates if item[0] == engine})
                _LOG.warning("startup_material_unavailable engine=%s reason=%s", engine, reason)
                continue
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
            except backends.StartCandidateUnavailable as exc:
                if exc.scope == "launch_engine":
                    excluded.update({item: exc.reason for item in candidates if item[0] == engine})
                else:
                    excluded[candidate] = exc.reason
                    await run_file_operation(
                        unavailable_candidates.record_unavailable_candidate,
                        model_type,
                        launch_kind,
                        candidate,
                        exc.reason,
                        now=datetime.datetime.now(datetime.UTC),
                    )
                _LOG.warning(
                    "モデル開始前の起動環境が成立しません: engine=%s model_type=%s launch_kind=%s reason=%s",
                    engine,
                    model_type,
                    launch_kind,
                    exc.reason,
                )
                continue
            except session_identity.DuplicateSessionIdentityError:
                raise
            except Exception as exc:
                if not backend_type.START_FAILURE_EXCLUDES_CANDIDATE:
                    raise
                reason = str(exc) or type(exc).__name__
                excluded[candidate] = reason
                unavailable_response = None
                unavailable_session = None
                _LOG.warning("cli_start_failed engine=%s model_type=%s model=%s reason=%s", engine, model_type, model, reason)
                continue
            session.engine = engine
            # テストや独自のMCPクライアントから起動して親のセッション記録に起動結果が残らない委譲先も、
            # `atk serve`の一覧が親の下へ置けるよう、作成時点の委譲元を登録簿へ記録する。
            if self._status_writer is not None:
                await self._status_writer.launcher_session_id_async()
            session.launcher_session_id = self.launcher_session_id()
            if session.status == "starting":
                session.status = "running"
            await self._save_started_session(session)
            await self._await_start_outcome(session)
            # 以後に過負荷で終了したturnは同じsessionの自動継続で扱う。
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
            diagnostics = session.claude_connection_diagnostics + session.claude_mcp_diagnostics
            if diagnostics:
                response["warnings"] = list(diagnostics)
            if excluded:
                response["excluded_candidates"] = excluded_candidate_payload(excluded, excluded_session_ids)
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
            unavailable_reason = self._unavailable_reason(session)
            if unavailable_reason is None:
                await run_file_operation(
                    unavailable_candidates.clear_unavailable_candidate,
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
                persistence_warnings = await self._save_started_session(session)
                if persistence_warnings:
                    response["persistence_warnings"] = persistence_warnings
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
            persistence_warnings = await self._save_started_session(unavailable_session)
            if persistence_warnings:
                unavailable_response["persistence_warnings"] = persistence_warnings
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

    async def start_handoff(self, session_id: str) -> dict[str, Any]:
        """終了した前任の起動条件と進捗を使い、明示操作1回で後任を開始する。"""
        current = await self.resolve_handoff_predecessor(session_id)
        predecessor = SessionResumeState.from_session(current) if isinstance(current, SessionState) else current
        reason = session_handoff.availability_reason(
            predecessor.engine,
            predecessor.status,
            session_handoff.predecessor_error(predecessor),
        )
        candidate = state.selected_candidate(predecessor)
        excluded = {candidate: reason} if candidate is not None and reason is not None else {}
        prompt = await run_file_operation(session_handoff.prepare_prompt, predecessor)
        assert predecessor.model_type is not None
        response = await self.start(
            predecessor.model_type,
            prompt,
            predecessor.cwd,
            launch_kind=predecessor.launch_kind,
            label=predecessor.label,
            excluded_candidates=excluded,
        )
        response["cwd"] = predecessor.cwd
        return response

    async def _save_started_session(self, session: SessionState) -> list[dict[str, str]]:
        """開始済みの情報を公開し、共有状態などの補助的な保存に失敗してもsessionが存在しないとは扱わない。"""
        warnings: list[dict[str, str]] = []
        if self._status_writer is not None:
            # 委譲先の通知と結果が届くルートを登録簿へ残し、所有サーバーを失った後の案内に使う。
            session.owner_root_session_id = self._status_writer.root_session_id
        try:
            session.touch()
        except OSError as error:
            _LOG.exception("開始済みsessionの共有状態更新に失敗: session_id=%s", session.session_id)
            warnings.append({"scope": "shared-state", "message": str(error)})
        await session.drain_registration()
        await self._drain_diagnostics()
        registration_error = await session.publish_registration_async()
        if registration_error is not None:
            warnings.append({"scope": "registration", "message": registration_error})
        if session.announced:
            try:
                await self._drain_candidate_records()
            except OSError as error:
                warnings.append({"scope": "shared-state", "message": str(error)})
        writer = self._status_writer
        if writer is None:
            return warnings
        saved = False
        try:
            await writer.flush_async()
            saved = True
        except status_file.ProjectionWriteError as error:
            warnings.append({"scope": "projection", "message": str(error)})
        except OSError as error:
            _LOG.exception(
                "開始済みsessionの結果・共有状態保存に失敗: session_id=%s writer=%s", session.session_id, writer.path
            )
            warnings.append({"scope": "shared-state", "message": str(error)})
        if session.announced and not saved:
            # 正常時は既存の表示・hook登録を使い、表示へ届かない起動だけを同じ待機登録へ補う。
            try:
                await run_file_operation(
                    wait_targets.retain_wait_targets,
                    writer.root_session_id,
                    writer.path.name,
                    [session.session_id],
                    writer.state_root,
                )
            except OSError as error:
                _LOG.exception("開始済みsessionの待機登録に失敗: session_id=%s writer=%s", session.session_id, writer.path)
                warnings.append({"scope": "wait-target", "message": str(error)})
        return warnings

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
            except ImportError as exc:
                reason = backends.start_dependency_failure(engine, exc)
                if reason is None:
                    raise
                raise backends.StartCandidateUnavailable(reason) from exc
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
        """次候補へ進む前に、可用性の失敗で終了したsessionの全資源を解放する。"""
        await self._backend(session.engine).release_session(session)
        await session.drain_registration()
        self._cancel_retention_timer(session.session_id)
        self.sessions.pop(session.session_id, None)
        if self._status_writer is not None:
            await self._status_writer.delete_result_async(session.session_id, collector="start-unavailable")
            self._status_writer.schedule()

    async def _await_start_outcome(self, session: SessionState) -> None:
        """起動直後の可用性失敗を確定するため、上限付きで終了を待つ。

        engineの可用性失敗は最初のモデル出力より前に生じるため、モデル出力を観測した時点で待機を打ち切る。
        Claudeでは、APIの応答開始（`message_start`）をモデル出力の観測とする。
        Weekly limitか5時間の利用上限の解除待ちへ入った時点でも打ち切り、`start`は`running`を返す。
        上限内に終了もモデル出力もしないsessionと、打ち切ったsessionは通常の実行中として扱い、
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
        resolved_type = model_type or tool_names.START_MODE_MODEL_TYPES["explore"]
        return await self.start(
            resolved_type,
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
        resolved_type = model_type or tool_names.START_MODE_MODEL_TYPES["shell"]
        return await self.start(
            resolved_type,
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
        resolved_type = model_type or tool_names.START_MODE_MODEL_TYPES["write"]
        return await self.start(
            resolved_type,
            prompt,
            cwd,
            launch_kind="write",
            label=resolve_display_label(label, "write"),
        )

    async def send_message(
        self,
        session_id: str,
        prompt: str | None = None,
        timeout: float = DEFAULT_SEND_MESSAGE_TIMEOUT,
        *,
        instruction: str | None = None,
        extra_params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """実行中turnを継続し、終了済みなら同じsessionでreplyを開始する。

        状態ファイルの書込主体を持つ場合は、`start`・`list`と同じく`root_session_id`を応答へ加える。
        再起動した会話が既存sessionへの`send_message`から再開しても、PostToolUseがこの値で別名索引を書けるようにするためである。
        """
        await self._drain_diagnostics()
        pending = self._pending_resumes.get(session_id)
        current = (
            pending.state if pending is not None else (self.sessions.get(session_id) or self.expired_sessions.get(session_id))
        )
        if current is None:
            current = await self._resolve_stopped_session(session_id)
        if current is None:
            await self.take_over_orphaned_session(session_id)
            await self._raise_if_owner_lost(session_id)
            current = await self._restore_registry_session(session_id)
        registry_resolved = self._is_registry_resume_state(current)
        prompt = await run_file_operation(
            launch_requests.continuation_prompt,
            current.prompt if current is not None else "",
            current.followup_prompts if current is not None else (),
            prompt=prompt,
            instruction=instruction,
            extra_params=extra_params,
        )
        response = await self._deliver_message(session_id, prompt, timeout)
        session = self.sessions.get(session_id)
        if session is not None and response["delivery"] != "reply_failed":
            session.followup_prompts += (prompt,)
            session.touch()
            await session.drain_registration()
        if self._status_writer is not None:
            if session is not None:
                # 開始時と同じbest-effort公開で、配送結果と同じsessionの待機登録を保つ。
                await self._save_started_session(session)
            response = {**response, "root_session_id": self._status_writer.root_session_id}
        if registry_resolved:
            response = self._response_with_notices(dict(response), await self._take_registry_notices(session_id))
        return response

    async def _deliver_message(self, session_id: str, prompt: str, timeout: float) -> dict[str, Any]:
        """`send_message`の配送本体。応答の`root_session_id`は呼び出し元が加える。"""
        validate_prompt(prompt)
        await self._wait_for_resource_release(session_id)
        if self._status_writer is not None:
            await self._status_writer.drain()
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
                        stopped_state = await self._resolve_stopped_session(session_id)
                        if stopped_state is None:
                            stopped_state = await self._restore_registry_session(session_id)
                        resume_state = stopped_state or self.expired_sessions.get(session_id)
                        if resume_state is not None:
                            previous_result = None
                            if stopped_state is not None:
                                previous_result = await self._take_stopped_result(
                                    session_id,
                                    stopped_state,
                                    collector="send-message",
                                )
                            elif (
                                self._status_writer is None
                                or (await self._status_writer.result_state_async(session_id)) != "consumed"
                            ):
                                previous_result = self._stopped_result_response(resume_state)
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                        session = await self._get_session(session_id)
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
                        await self._status_writer.delete_result_async(session_id, collector="send-message")
                    delivery = result["delivery"]
                    await session.drain_registration()
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

    async def _kill_result_response_async(self, session: SessionState, *, kill_requested: bool) -> dict[str, Any]:
        """結果の回収をイベントループ上で確定し、競合する保存処理の終了と結果ファイルの削除を待ってから、killの応答本文を返す。"""
        response = await self._result_response_async(session, include_progress=False)
        response["kill_requested"] = kill_requested
        response = self._response_with_notices(response, await self._take_notices_async(session.session_id))
        if "agent_message" in response and self._status_writer is not None:
            await self._status_writer.delete_result_async(session.session_id, collector="kill")
        return response

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
    ) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終了を待つ。"""
        await self._drain_diagnostics()
        observed_session = self.sessions.get(session_id)
        observed_turn = (
            None
            if observed_session is None
            else (observed_session.turn_seq, observed_session.terminal, observed_session.interrupt_requested)
        )
        await self._wait_for_resource_release(session_id)
        session = self.sessions.get(session_id)
        if session is not None:
            await session.drain_registration()
        if self._status_writer is not None:
            await self._status_writer.drain()
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ActionableError(
                "timeout must be non-negative", next_action="`timeout`を省略して270秒で待つか、0以上の秒数を指定する"
            )
        stopped_state = await self._resolve_stopped_session(session_id)
        recovered_state: SessionResumeState | None = None
        if stopped_state is None and session_id not in self.sessions and session_id not in self._pending_resumes:
            await self.take_over_orphaned_session(session_id)
            owner_lost_response = await self._kill_owner_lost(session_id)
            if owner_lost_response is not None:
                return await self._stop_after_terminal_response(session_id, owner_lost_response, stop)
        if stopped_state is None:
            recovered_state = await self._restore_registry_session(session_id)
            stopped_state = recovered_state
        if recovered_state is not None:
            response = self._recovered_result_response(recovered_state)
            response["kill_requested"] = False
            return self._response_with_notices(response, await self._take_registry_notices(session_id))
        if stopped_state is not None:
            stopped_response = await self._take_stopped_result(session_id, stopped_state, collector="kill")
            if stopped_response is not None:
                stopped_response["kill_requested"] = False
                notices = await self._take_notices_async(session_id)
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
                await session.drain_registration()
                response = await self._kill_result_response_async(session, kill_requested=True)
                return await self._stop_after_terminal_response(session_id, response, stop)
        else:
            expired_response = await self._expired_kill_response(session_id)
            if expired_response is not None:
                return await self._stop_after_terminal_response(session_id, expired_response, stop)
            session = await self._get_session(session_id)
        if observed_session is session and observed_turn is not None and observed_turn[0] == session.turn_seq:
            _, started_terminal, requested_before_call = observed_turn
        else:
            started_terminal = session.terminal
            requested_before_call = session.interrupt_requested
        if started_terminal:
            response = await self._kill_result_response_async(session, kill_requested=False)
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
                            next_action="`timeout`へ正の秒数を指定して`kill`を再発行するか、`atk agents wait`で終了を観測する",
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
                        await session.drain_registration()
                        response = await self._kill_result_response_async(session, kill_requested=False)
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
            await session.drain_registration()
            response = await self._kill_result_response_async(session, kill_requested=False)
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
                    next_action="中断要求は配送済みのため`kill`を再発行しない。`atk agents wait`で終了を観測する",
                ) from exc
        await session.drain_registration()
        response = await self._kill_result_response_async(session, kill_requested=True)
        return await self._stop_after_terminal_response(session_id, response, stop)

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
        task = self._close_task
        if task is None or task.done() and (task.cancelled() or task.exception() is not None):
            task = self._close_task = asyncio.create_task(self._close())
        await finish_task(task)

    async def _close(self) -> None:
        remove_terminal_listener(self._schedule_session_expiry)
        remove_lifecycle_listener(self._cancel_expiry_on_start)
        for timer in self._retention_timers.values():
            timer.cancel()
        self._retention_timers.clear()
        # 監視・記録taskや他backendの待機が停止要求の配送を妨げないよう、先に全backendを開始する。
        backend_tasks = [asyncio.create_task(backend.close()) for backend in tuple(self._backends.values())]
        errors: list[Exception] = []
        if self._resource_release_tasks:
            outcomes = await lifetime.wait_for_shutdown(self._resource_release_tasks.values())
            errors.extend(outcome for outcome in outcomes if isinstance(outcome, Exception))
            self._resource_release_tasks.clear()
        if self._auto_resume_task is not None:
            self._auto_resume_task.cancel()
            await lifetime.wait_for_shutdown((self._auto_resume_task,))
            self._auto_resume_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await lifetime.wait_for_shutdown((self._heartbeat_task,))
            self._heartbeat_task = None
        pending_resumes = tuple(self._pending_resumes.values())
        for pending in pending_resumes:
            pending.discard_previous_result()
        resume_tasks = tuple(pending.task for pending in pending_resumes)
        for task in resume_tasks:
            if not task.done():
                task.cancel()
        if resume_tasks:
            await lifetime.wait_for_shutdown(resume_tasks)
        try:
            # 1つのbackendの停止失敗で、他のbackendの停止と後段の結果公開・listener解除・writer停止を止めない。
            if backend_tasks:
                outcomes = await lifetime.wait_for_shutdown(backend_tasks)
                errors.extend(outcome for outcome in outcomes if isinstance(outcome, Exception))
            try:
                await self._publish_closed_sessions()
            except status_file.ProjectionWriteError:
                _LOG.exception("停止時の表示用状態保存に失敗しました。終了時の結果を保持して後始末を続けます")
            except Exception as error:
                errors.append(error)
        finally:
            remove_terminal_listener(self._carry_over_unavailable_candidate)
            remove_terminal_listener(self._record_pending_unobserved_child_sessions)
            remove_lifecycle_listener(self._record_resources)
            try:
                await self._drain_diagnostics()
            except Exception as error:
                errors.append(error)
            try:
                await self._drain_candidate_records()
            except Exception as error:
                if errors:
                    errors[0].add_note(f"候補除外記録の保存にも失敗しました: {error}")
                else:
                    errors.append(error)
            finally:
                if self._status_writer is not None:
                    remove_touch_listener(self._status_writer.schedule)
                    try:
                        await self._status_writer.drain(cancel_scheduled=True)
                    except Exception as error:
                        errors.append(error)
                    finally:
                        # 保存taskの回収に失敗しても予約を解除し、所有する状態ファイルを残さない。
                        try:
                            await self._status_writer.deactivate_async()
                        except Exception as error:
                            errors.append(error)
        if errors:
            for error in errors[1:]:
                errors[0].add_note(f"停止時の別の失敗: {error!r}")
            raise errors[0]
        await run_file_operation(logging_config.flush_logging)
