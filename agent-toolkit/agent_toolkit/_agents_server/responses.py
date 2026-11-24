from __future__ import annotations

import asyncio
import datetime
import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from agent_toolkit._agents_server import (
    codex_resume_state,
    handoff_next_action,
    manager_base,
    notice_inbox,
    record_paths,
    result_projection,
    retained_results,
    state,
    status_file,
)
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.manager_base import PendingResume
from agent_toolkit._agents_server.state import (
    TERMINAL_STATUSES,
    ModelCandidate,
    RegistryResumeState,
    SessionResumeState,
    SessionState,
)
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.runtime_identity import distinct_identities

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


def observed_session_identity(session_id: str) -> dict[str, str] | None:
    found = record_paths.find_session_record(session_id)
    if found is None or len(found.paths) != 1:
        return None
    entries: list[dict[str, Any]] = []
    try:
        with found.paths[0].open(encoding="utf-8") as stream:
            for raw in stream:
                if raw.strip():
                    value = json.loads(raw)
                    if isinstance(value, dict):
                        entries.append(value)
    except OSError, UnicodeError, json.JSONDecodeError:
        return None
    identities = distinct_identities(entries, found.engine)
    return identities[-1].public() if identities else None


UNKNOWN_SESSION_NEXT_ACTION = (
    "`list`で保持状態を確かめる。結果が必要なら`atk agents wait`を試し、無ければ検証済みの状態から新規に起動する"
)


EXPIRED_SESSION_NEXT_ACTION = (
    "継続不能とは扱わない。未回収の結果は`atk agents wait`で受領し、"
    "継続は同じ`session_id`へ`send_message`を送って暗黙に再開する"
)


SESSION_ID_NEXT_ACTION = "`start`の応答か`list`が返した`session_id`を指定する"


RUNNING_STOP_NEXT_ACTION = "中断が必要なら先に`kill`を発行し、終了を観測してから`stop`を再発行する"


_START_FAILED_NEXT_ACTION = "`atk agents wait`で終了時の結果の`error`を受領し、その案内に従う"


REPLY_NEXT_ACTIONS = {
    "reply_failed": "新しいturnを開始できなかった。`atk agents wait`で終了時の結果を受領して原因を確かめてから次の操作を選ぶ",
    "reply_ambiguous": (
        "turnの開始を確定できなかった。`atk agents wait`で状態を確認し、turnが始まっていない場合だけ`send_message`を再送する"
    ),
}


EXPIRED_KILL_NEXT_ACTION = "中断対象は無い。未回収の結果は`atk agents wait`で受領する"


def resolve_display_label(label: str | None, fallback: str) -> str:
    normalized = status_file.normalize_label(label) if label else ""
    return normalized or status_file.normalize_label(fallback)


def listed_public_session(session: dict[str, Any]) -> dict[str, Any]:
    public = {"session_id": session["session_id"], "status": session["status"]}
    for key in ("engine", "model", "effort"):
        if key in session:
            public[key] = session[key]
    if "seconds_since_activity" in session:
        public["seconds_since_activity"] = session["seconds_since_activity"]
    if "api_error" in session:
        public["api_error"] = session["api_error"]
    if "owner_server_instance" in session:
        public["owner_server_instance"] = session["owner_server_instance"]
    return public


def public_start_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """起動の応答のうち委譲元へ公開する項目を返す。

    候補を切り替えて成立した起動だけが、除外した候補と採用した候補を加える。
    除外した候補のうち実際に作成したsessionは、その識別子も保持する。
    切り替えが起きない起動は`session_id`、`status`および起動したsessionが保持する担当名の`label`を返す。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    起動直後に失敗で終了した応答は、委譲元が状態値だけで次の行動を決められるよう`next_action`を加える。
    """
    public: dict[str, Any] = {key: response[key] for key in ("session_id", "status", "label")}
    if all(key in response for key in ("engine", "model", "effort")):
        public["launch_identity"] = {
            "engine": response["engine"],
            "model": response["model"],
            "effort": response["effort"],
            "source": "launch_candidate",
        }
        public["observed_identity"] = None
    if "root_session_id" in response:
        public["root_session_id"] = response["root_session_id"]
    if response.get("warnings"):
        public["warnings"] = response["warnings"]
    for key in ("admission_wait_seconds", "admission_limit"):
        # マシン全体の同時稼働数の上限で待たせた起動だけが持つ。依頼側が待ちの発生と時間を知るために公開する。
        if key in response:
            public[key] = response[key]
    if response.get("excluded_candidates"):
        public["excluded_candidates"] = response["excluded_candidates"]
        public["engine"] = response["engine"]
        public["model"] = response["model"]
        public["effort"] = response["effort"]
        public.update(state.fast_mode_fields(response.get("engine"), response.get("fast_mode")))
    if response["status"] == "failed":
        public["next_action"] = _START_FAILED_NEXT_ACTION
    if response.get("persistence_warnings"):
        public["persistence_warnings"] = response["persistence_warnings"]
        public["next_action"] = (
            "sessionは開始済みです。同じ`session_id`の`show`・`send_message`で状態確認と追送を行い、"
            "保存の失敗原因を解消してから`atk agents wait`で終了時の結果を受け取る。"
            "保存失敗だけを理由に新しい委譲先を起動しない"
        )
    return public


def public_send_message_response(response: Mapping[str, Any]) -> dict[str, Any]:
    public: dict[str, Any] = {"delivery": response["delivery"], "label": response["label"]}
    if "root_session_id" in response:
        public["root_session_id"] = response["root_session_id"]
    previous_result = response.get("previous_result")
    if previous_result:
        public["previous_result"] = result_projection.public_result(previous_result)
    if response.get("notices"):
        public["notices"] = [result_projection.public_notice(notice) for notice in response["notices"]]
    next_action = REPLY_NEXT_ACTIONS.get(response["delivery"])
    error = response.get("error")
    if (
        response["delivery"] == "reply_failed"
        and isinstance(error, dict)
        and error.get("codexErrorInfo") == codex_resume_state.WRITER_CONFLICT
    ):
        next_action = codex_resume_state.WRITER_CONFLICT_NEXT_ACTION
    if response["delivery"] in {"reply_started", "steered"} and result_projection.is_finalized_wait_statement(
        public.get("previous_result", {})
    ):
        next_action = "追送は配送済み。同じ入力を再送せず、現在のturnの結果を`atk agents wait`で受け取る"
    if next_action is not None:
        public["next_action"] = next_action
    return public


def excluded_candidate_payload(
    excluded: Mapping[ModelCandidate, str], session_ids: Mapping[ModelCandidate, str] | None = None
) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for (engine, model, effort), reason in sorted(excluded.items()):
        item = {"engine": engine, "model": model, "effort": effort, "reason": reason}
        candidate = (engine, model, effort)
        if session_ids is not None and candidate in session_ids:
            item["session_id"] = session_ids[candidate]
        payload.append(item)
    return payload


def turn_elapsed_seconds(started_at_value: str | None) -> int | None:
    if started_at_value is None:
        return None
    try:
        started_at = datetime.datetime.fromisoformat(started_at_value)
    except ValueError:
        return None
    return max(0, int(datetime.datetime.now(tz=started_at.tzinfo).timestamp() - started_at.timestamp()))


class ManagerResponses(manager_base.ManagerBase):
    @staticmethod
    def _stopped_result_response(resume_state: SessionResumeState) -> dict[str, Any] | None:
        if resume_state.result_delivered or resume_state.status not in TERMINAL_STATUSES or resume_state.finalized_at is None:
            return None
        response: dict[str, Any] = {
            "status": resume_state.status,
            "agent_message": resume_state.agent_message,
        }
        if resume_state.error is not None and resume_state.error != "" and resume_state.error != {}:
            response["error"] = resume_state.error
        next_action = handoff_next_action.result_next_action(
            {"session_id": resume_state.session_id, "engine": resume_state.engine, **response}
        )
        if next_action is not None:
            response = result_projection.append_result_next_action(response, next_action)
        return result_projection.with_result_next_action(response, resume_state.label)

    @staticmethod
    def _recovered_result_response(resume_state: SessionResumeState) -> dict[str, Any]:
        return {"status": resume_state.status, "recovery": "result_unavailable"}

    @staticmethod
    def _listed_session(
        session: SessionState | SessionResumeState,
        *,
        status: str,
        progress: str,
        result_available: bool,
    ) -> dict[str, Any]:
        label = session.label
        if len(label) > 100:
            label = f"{label[:100]}…"
        listed: dict[str, Any] = {
            "session_id": session.session_id,
            "status": status,
            "progress": progress,
            "model_type": session.model_type,
            "launch_kind": session.launch_kind,
            "label": label,
            "result_available": result_available,
            "engine": session.engine,
            "model": session.model,
            "effort": session.effort,
        }
        if session.started_at is not None:
            listed["started_at"] = session.started_at
        listed.update(
            result_projection.activity_projection(
                updated_at=session.updated_at,
                output_updated_at=session.output_updated_at,
                started_at=session.started_at,
                api_error=getattr(session, "api_error", None) if status == "running" else None,
            )
        )
        return listed

    @staticmethod
    async def _unresolved_session_error(
        session_id: str,
        *,
        label: str,
        resolution: session_registry.SessionResolution | None = None,
    ) -> ActionableError:
        try:
            parsed = UUID(session_id)
        except ValueError:
            parsed = None
        if parsed is None or str(parsed) != session_id.lower():
            return ActionableError(
                f"{label} identifier scheme mismatch: {session_id}; expected UUID", next_action=SESSION_ID_NEXT_ACTION
            )
        if resolution is None:
            resolution = await run_file_operation(session_registry.resolve, session_id)
        if resolution.state is session_registry.Resolution.RELEASED:
            reason = "retention expired" if resolution.released_reason == "retention_expired" else "stopped"
            message = (
                f"unknown {label}: {session_id}; released by the owning agents_server ({reason}, {resolution.released_at}); "
                "its result is no longer retained"
            )
            if resolution.interrupted_turns:
                turns = result_projection.interrupted_turns_projection(resolution.interrupted_turns)
                message += "; interrupted_turns=" + json.dumps(turns, ensure_ascii=False)
            return ActionableError(message, next_action=UNKNOWN_SESSION_NEXT_ACTION)
        return ActionableError(
            f"unknown {label}: {session_id}; no agents_server on this host has a record of this session",
            next_action=UNKNOWN_SESSION_NEXT_ACTION,
        )

    async def _read_notices(self, session_id: str) -> list[notice_inbox.PendingNotice]:
        if self._status_writer is None:
            return []
        return await self._status_writer.read_notices_async(session_id)

    async def _result_response_async(
        self, session: SessionState, *, include_progress: bool = True, include_result: bool = True
    ) -> dict[str, Any]:
        identity = (
            await run_file_operation(observed_session_identity, session.session_id)
            if include_result and session.terminal
            else None
        )
        return self._result_response(
            session,
            include_progress=include_progress,
            include_result=include_result,
            observed_identity=identity,
            identity_read=True,
        )

    @staticmethod
    def _deliver_notices(response: dict[str, Any], pending: list[notice_inbox.PendingNotice]) -> dict[str, Any]:
        if notices := notice_inbox.consume_notices(pending):
            response["notices"] = notices
        return response

    @staticmethod
    def _result_response(
        session: SessionState,
        *,
        include_progress: bool = True,
        include_result: bool = True,
        observed_identity: dict[str, str] | None = None,
        identity_read: bool = False,
    ) -> dict[str, Any]:
        response: dict[str, Any] = session.public_status(include_result=include_result and session.result_available)
        if not include_result and session.result_available:
            response = {"status": "expired"} if session.result_delivered else {"status": "running", "progress": ""}
        if response.get("status") in TERMINAL_STATUSES:
            response["launch_identity"] = {
                "engine": session.engine,
                "model": session.model,
                "effort": session.effort,
                "source": "launch_candidate",
            }
            response["observed_identity"] = (
                observed_identity if identity_read else observed_session_identity(session.session_id)
            )
        if response.get("status") == "running":
            elapsed_seconds = turn_elapsed_seconds(session.started_at)
            if elapsed_seconds is not None:
                response["elapsed_seconds"] = elapsed_seconds
        if not include_progress:
            response.pop("progress", None)
            response.pop("elapsed_seconds", None)
        next_action = handoff_next_action.result_next_action(
            {"session_id": session.session_id, "engine": session.engine, **response}
        )
        if next_action is not None:
            response = result_projection.append_result_next_action(response, next_action)
        return response

    async def _claim_resume_result(
        self, resume_state: SessionResumeState, response: dict[str, Any], *, collector: str
    ) -> dict[str, Any] | None:
        """退避元の違いによらず、同じturnの排他回収に成功した本文だけを返す。"""
        session_id = resume_state.session_id
        if isinstance(resume_state, RegistryResumeState) and resume_state.result_root_session_id is not None:
            claimed, error = await retained_results.take_for_response(
                resume_state.result_root_session_id,
                session_id,
                resume_state.result_owner_status_file,
                expected_turn_seq=resume_state.turn_seq,
                expected_finalized_at=resume_state.finalized_at,
                collector=collector,
                state_root=self._status_writer.state_root if self._status_writer is not None else None,
            )
            if error is not None:
                raise ActionableError(
                    f"保存済みの子の結果を回収できない: {session_id}: {error}",
                    next_action="結果ファイルの読み取り権限と内容を確認し、同じwaitを再実行する",
                )
            return None if claimed is None else {**response, **claimed}
        if self._status_writer is not None:
            await self._status_writer.retain_result_async(resume_state)
            claimed, error = await self._status_writer.take_result_async(
                session_id,
                collector=collector,
                expected_turn_seq=resume_state.turn_seq,
                expected_finalized_at=resume_state.finalized_at,
            )
            if error is not None:
                raise ActionableError(
                    f"保存済みの子の結果を回収できない: {session_id}: {error}",
                    next_action="結果ファイルの読み取り権限と内容を確認し、同じwaitを再実行する",
                )
            return None if claimed is None else response
        return response

    async def _resume_response(self, pending: PendingResume, ticket: int) -> dict[str, Any]:
        try:
            session = await asyncio.shield(pending.task)
        except asyncio.CancelledError:
            pending.prompt.cancel(ticket)
            raise
        delivery = pending.delivery
        response: dict[str, Any] = {"delivery": delivery, "label": session.label}
        if (
            delivery == "reply_failed"
            and isinstance(session.error, dict)
            and session.error.get("codexErrorInfo") == codex_resume_state.WRITER_CONFLICT
        ):
            response["error"] = {"codexErrorInfo": codex_resume_state.WRITER_CONFLICT}
        previous_result = pending.take_previous_result()
        if previous_result:
            response["previous_result"] = state.PreviousResult(previous_result, pending.state)
        response.update(vars(self).get("_admission_wait_fields", {}).pop(session.session_id, {}))
        return response
