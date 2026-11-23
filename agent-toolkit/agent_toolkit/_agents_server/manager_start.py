from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import logging
import pathlib
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import (
    admission,
    advice_request,
    backends,
    launch_prompts,
    launch_requests,
    manager_wait,
    plugin_root,
    session_handoff,
    session_identity,
    state,
    status_file,
    tool_names,
    unavailable_candidates,
    wait_targets,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
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
from agent_toolkit._agents_server.plugin_root import SERVER_PLUGIN_ROOT
from agent_toolkit._agents_server.responses import excluded_candidate_payload, resolve_display_label
from agent_toolkit._agents_server.session_errors import ActionableRuntimeError, SessionInitializationTimeoutError
from agent_toolkit._agents_server.state import LaunchKind, ModelCandidate, SessionResumeState, SessionState
from agent_toolkit._agents_server.tool_descriptions import shell_prompt
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import codex_models, session_launchers, startup_documents, startup_features, task_documents
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.plugin_roots import PluginRootError

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


def model_override_warnings(
    model_type: str | None, *, default_type: str | None = None, inherited: bool = False
) -> list[dict[str, str]]:
    if model_type is None or (model_type in _atk_config.MODEL_TYPES and (default_type is None or model_type == default_type)):
        return []
    restore = (
        f"model_typeへ元の工程名（{default_type or '前任の役割に定めた工程'}）を明示する"
        if inherited
        else "taskではmodel_typeを省略し、自由本文では該当する工程名を指定する"
    )
    return [
        {
            "scope": "model-override",
            "message": (
                f"モデル設定の{'直接候補継承' if inherited else '上書き'}: "
                f"適用工程={default_type or '候補列直接指定（工程設定を参照しない）'}; 指定値={model_type}。"
                f"現在の工程設定を使うには{restore}。"
                "ユーザーが指定した値、試行、同じ要求に限った個別の引き上げは受理し、"
                "以後の初回起動には工程設定を使う。"
            ),
        }
    ]


class ManagerStart(manager_wait.ManagerWait):  # pylint: disable=too-many-ancestors  # 責務別のmixinへ分割したため。
    async def _resolve_start_candidates(
        self,
        model_type: str,
        *,
        launch_kind: LaunchKind,
        excluded_candidates: Mapping[ModelCandidate, str] | None = None,
        exec_lane: bool = False,
    ) -> tuple[list[ModelCandidate], dict[ModelCandidate, str]]:
        await self._drain_candidate_records()
        try:
            candidates = await run_file_operation(
                _atk_config.parse_unresolved_model_candidates,
                model_type,
                exec_lane=exec_lane,
            )
        except ValueError as error:
            raise ActionableError(str(error), next_action=MODEL_TYPE_NEXT_ACTION) from error
        if codex_models.needs_catalog(candidates):
            catalog = await backends.model_catalog(self._backend(backends.CODEX_ENGINE)).list_models()
            candidates = codex_models.resolve_candidates(candidates, catalog)
        unsupported = dict(excluded_candidates or {})
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
        """マシン全体の同時稼働数の上限に空きが出るまで待ってから起動する。

        待った場合は待機秒数と上限の由来を応答へ加え、依頼側が既存の起動結果から待ちを知れるようにする。
        """
        validate_prompt(prompt)
        validate_cwd(cwd)
        ticket = await admission.acquire(await run_file_operation(self.launcher_session_id), state_root=self._state_root())
        try:
            response = await self._start_admitted(
                model_type,
                prompt,
                cwd,
                launch_kind=launch_kind,
                label=label,
                excluded_candidates=excluded_candidates,
                startup_document=startup_document,
                startup_inputs=startup_inputs,
            )
        finally:
            ticket.release()
        response.update(ticket.response_fields())
        return response

    @staticmethod
    def _startup_documents(
        role_document: pathlib.Path | None,
        inputs: Mapping[str, str] | None,
        engine: str,
        cwd: str,
    ) -> str:
        """起動の本文へ入れる資料を、候補のhostとcwdで解決する。

        役割の起動の失敗は、役割の版と対応付けて起動前に返す。役割文書の無い起動は資料を入れない。
        """
        if role_document is None:
            return ""
        return plugin_root.SERVER_RUNTIME_CODE.check_role(
            "起動時資料の検査",
            role_document,
            lambda: startup_documents.compose(
                role_document,
                inputs or {},
                host=engine,
                project=cwd,
            ),
        )

    async def _start_admitted(
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
        startup_features.effective(launch_kind)
        await launch_requests.check_plugin_commands(cwd)
        role_document = startup_document or task_documents.launched_task_document(prompt)
        exec_lane = (
            role_document is not None
            and role_document.name == "exec.subagent.md"
            and (startup_inputs or {}).get("担当種別") == "レーン担当"
        )
        candidates, excluded = await self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            exec_lane=exec_lane,
        )
        unavailable_response: dict[str, Any] | None = None
        unavailable_session: SessionState | None = None
        excluded_session_ids: dict[ModelCandidate, str] = {}
        display_label = resolve_display_label(label, prompt)
        parent_model = await run_file_operation(self.launcher_model) if launch_kind == "delegate" else None
        for candidate_index, candidate in enumerate(candidates):
            if candidate in excluded:
                continue
            engine, model, effort = candidate
            backend_type = backends.backend_class(engine)
            if backend_type is None:
                raise ActionableError(f"unsupported engine: {engine}", next_action=MODEL_TYPE_NEXT_ACTION)
            validate_model_effort(model, effort)

            try:
                resource_instructions = await run_file_operation(
                    launch_prompts.plugin_resource_instructions, SERVER_PLUGIN_ROOT, engine, cwd, starting=True
                )
                initial_documents = await run_file_operation(
                    self._startup_documents, startup_document, startup_inputs, engine, cwd
                )
                delivery_body = (
                    resource_instructions
                    + "\n\n"
                    + wrap_delivery_body(launch_requests.with_startup_documents(prompt, initial_documents))
                )
                if await run_file_operation(
                    advice_request.advice_request_eligible,
                    parent_model,
                    engine,
                    model,
                    model_type=model_type,
                    launch_kind=launch_kind,
                    startup_document=role_document,
                ):
                    delivery_body += "\n\n" + launch_prompts.ADVICE_REQUEST_NOTICE
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
                    request_prompt=prompt,
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

            if self._status_writer is not None:
                await self._status_writer.launcher_session_id_async()
            session.launcher_session_id = self.launcher_session_id()
            session.start_observation = session_launchers.START_OBSERVATION.get()
            if session.status == "starting":
                session.status = "running"
            await self._save_started_session(session)
            await self._await_start_outcome(session)

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
            if isinstance(session.error, dict) and session.error.get("configuredModelChanged") is True:
                session.label = display_label
                session.prompt = prompt
                session.announced = True
                persistence_warnings = await self._save_started_session(session)
                if persistence_warnings:
                    response["persistence_warnings"] = persistence_warnings
                response["label"] = session.label
                return response
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

    async def start_handoff(self, session_id: str, *, model_type: str | None = None) -> dict[str, Any]:
        current = await self.resolve_handoff_predecessor(session_id)
        if isinstance(current, SessionState):
            # 終了通知がCLIの後始末より先に届いても、後任と前任が同じworktreeへ同時に作用しない。
            await self._backend(current.engine).release_session(current)
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
        warnings = model_override_warnings(
            predecessor.model_type if model_type is None else model_type, inherited=model_type is None
        )
        with startup_features.selection(predecessor.startup_profile):
            response = await self.start(
                predecessor.model_type if model_type is None else model_type,
                prompt,
                predecessor.cwd,
                launch_kind=predecessor.launch_kind,
                label=predecessor.label,
                excluded_candidates=excluded,
            )
        response["cwd"] = predecessor.cwd
        if warnings:
            response.setdefault("warnings", []).extend(warnings)
        return response

    async def _save_started_session(self, session: SessionState) -> list[dict[str, str]]:
        """開始済みの情報を公開し、共有状態などの補助的な保存に失敗してもsessionが存在しないとは扱わない。"""
        warnings: list[dict[str, str]] = []
        if self._status_writer is not None:
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
        request_prompt: str,
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
                    request_prompt=request_prompt,
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
        if self._status_writer is not None and session.finalized_at is not None:
            await self._status_writer.delete_result_async(
                session.session_id, collector="start-unavailable", expected_end=(session.turn_seq, session.finalized_at)
            )
            self._status_writer.schedule()

    async def _await_start_outcome(self, session: SessionState, timeout: float = state.START_AVAILABILITY_TIMEOUT) -> None:
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
                    timeout=timeout,
                )

    @staticmethod
    def _start_outcome_observed(session: SessionState) -> bool:
        if session.codex_auto_reset_pending:
            return False
        return session.result_available or session.model_output_observed or session.usage_limit_resume_at is not None

    async def start_explore(
        self,
        prompt: str,
        cwd: str,
        *,
        label: str | None = None,
        model_type: str | None = None,
    ) -> dict[str, Any]:
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
        resolved_type = model_type or tool_names.START_MODE_MODEL_TYPES["write"]
        return await self.start(
            resolved_type,
            prompt,
            cwd,
            launch_kind="write",
            label=resolve_display_label(label, "write"),
        )
