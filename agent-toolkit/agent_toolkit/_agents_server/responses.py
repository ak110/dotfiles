"""MCPツールの応答へ載せるsessionの公開表現と、応答の次の操作の文面。"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from agent_toolkit._agents_server import (
    manager_base,
    record_paths,
    result_projection,
    session_registry,
    state,
    status_file,
)
from agent_toolkit._agents_server.manager_base import PendingResume
from agent_toolkit._agents_server.state import (
    TERMINAL_STATUSES,
    ModelCandidate,
    SessionResumeState,
    SessionState,
)
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.runtime_identity import distinct_identities

_LOG = logging.getLogger("agent-toolkit.agents-server.mcp")


def observed_session_identity(session_id: str) -> dict[str, str] | None:
    """一意に解決した物理記録の最後の観測identityを返す。"""
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
    except (OSError, UnicodeError, json.JSONDecodeError):
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


RUNNING_STOP_NEXT_ACTION = "中断が必要なら先に`kill`を発行し、終端を観測してから`stop`を再発行する"


KILL_NOT_DELIVERED_NEXT_ACTION = "`atk agents wait`で状態を確認し、turnが続いていて中断が必要なら`kill`を再発行する"


_START_FAILED_NEXT_ACTION = "`atk agents wait`で終端結果の`error`を受領し、`model_type`へ別の候補を指定して起動し直す"


REPLY_NEXT_ACTIONS = {
    "reply_failed": "新しいturnを開始できなかった。`atk agents wait`で終端結果を受領して原因を確かめてから次の操作を選ぶ",
    "reply_ambiguous": (
        "turnの開始を確定できなかった。`atk agents wait`で状態を確認し、turnが始まっていない場合だけ`send_message`を再送する"
    ),
}


EXPIRED_KILL_NEXT_ACTION = "中断対象は無い。未回収の結果は`atk agents wait`で受領する"


def resolve_display_label(label: str | None, fallback: str) -> str:
    """委譲元が指定した識別名を正規化し、空になる指定では代替の本文から導く。

    未指定と、空白だけで構成された指定を含む空になる指定を同じ扱いとする。
    識別名の列が空のまま表示されると、そのsessionを名前で見分けられないためである。
    """
    normalized = status_file.normalize_label(label) if label else ""
    return normalized or status_file.normalize_label(fallback)


def listed_public_session(session: dict[str, Any]) -> dict[str, Any]:
    """一覧の公開応答へ返す項目だけを取り出す。

    停滞の判定は`seconds_since_activity`と閾値の比較で委譲元が行うため、判定済みの印を返さない。
    """
    public = {"session_id": session["session_id"], "status": session["status"]}
    if "seconds_since_activity" in session:
        public["seconds_since_activity"] = session["seconds_since_activity"]
    if "api_error" in session:
        public["api_error"] = session["api_error"]
    return public


def public_start_response(response: Mapping[str, Any]) -> dict[str, Any]:
    """起動の応答のうち委譲元へ公開する項目を返す。

    候補を切り替えて成立した起動だけが、除外した候補と採用した候補を加える。
    除外した候補のうち実際に作成したsessionは、その識別子も保持する。
    切り替えが起きない起動は`session_id`、`status`および起動したsessionが保持する担当名の`label`を返す。
    サーバーがroot sessionの識別子を保持する場合は`root_session_id`も加える。
    起動直後に失敗で終端した応答は、委譲元が状態値だけで次の行動を決められるよう`next_action`を加える。
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
    if response.get("excluded_candidates"):
        public["excluded_candidates"] = response["excluded_candidates"]
        public["engine"] = response["engine"]
        public["model"] = response["model"]
        public["effort"] = response["effort"]
        public.update(state.fast_mode_fields(response.get("engine"), response.get("fast_mode")))
    if response["status"] == "failed":
        public["next_action"] = _START_FAILED_NEXT_ACTION
    return public


def excluded_candidate_payload(
    excluded: Mapping[ModelCandidate, str], session_ids: Mapping[ModelCandidate, str] | None = None
) -> list[dict[str, Any]]:
    """除外した候補の理由と、作成済み試行の識別子を公開する。"""
    payload: list[dict[str, Any]] = []
    for (engine, model, effort), reason in sorted(excluded.items()):
        item = {"engine": engine, "model": model, "effort": effort, "reason": reason}
        candidate = (engine, model, effort)
        if session_ids is not None and candidate in session_ids:
            item["session_id"] = session_ids[candidate]
        payload.append(item)
    return payload


def turn_elapsed_seconds(started_at_value: str | None) -> int | None:
    """開始時刻から現在までの経過秒を返す。"""
    if started_at_value is None:
        return None
    try:
        started_at = datetime.datetime.fromisoformat(started_at_value)
    except ValueError:
        return None
    return max(0, int(datetime.datetime.now(tz=started_at.tzinfo).timestamp() - started_at.timestamp()))


class ManagerResponses(manager_base.ManagerBase):
    """sessionの状態と結果をMCPの応答の形へ整える。"""

    @staticmethod
    def _stopped_result_response(resume_state: SessionResumeState) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返す。"""
        if resume_state.result_delivered or resume_state.status not in TERMINAL_STATUSES or resume_state.finalized_at is None:
            return None
        response: dict[str, Any] = {
            "status": resume_state.status,
            "agent_message": resume_state.agent_message,
        }
        if resume_state.error is not None and resume_state.error != "" and resume_state.error != {}:
            response["error"] = resume_state.error
        return result_projection.with_result_next_action(response, resume_state.label)

    @staticmethod
    def _recovered_result_response(resume_state: SessionResumeState) -> dict[str, Any]:
        """再起動前の終端種別と結果本文を回収できない事実を返す。"""
        return {"status": resume_state.status, "recovery": "result_unavailable"}

    @staticmethod
    def _listed_session(
        session: SessionState | SessionResumeState,
        *,
        status: str,
        progress: str,
        result_available: bool,
    ) -> dict[str, Any]:
        """sessionを一覧向けの公開項目へ射影する。

        最終活動時刻からの経過秒数を返し、停滞かどうかの判定は委譲元へ委ねる。
        判定済みの印は同じ応答の値と閾値から再現できるため、公開項目へ加えない。
        """
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
    def _unresolved_session_error(
        session_id: str,
        *,
        label: str,
        resolution: session_registry.SessionResolution | None = None,
    ) -> ActionableError:
        """未解決の識別子を体系相違、所有側による解放済み、または記録無しとして診断する。

        保持状態の照会後だけ呼び、登録済みの非UUID識別子は拒否しない。
        体系相違の本文には、継続不能の判定に使う`unknown session`を含めない。
        UUIDの識別子は、登録簿の解放済みレコードの有無で文面を分け、いずれも`unknown <label>: <id>`で始める。
        登録簿のレコードは再起動では削除されないため、不在の原因として再起動を案内しない。
        記録が無い原因は照会側から確定できないため、理由には原因を書かず、次の操作で確認の手順を示す。
        `resolution`は、呼び出し元が同じ識別子を既に解決している場合に渡す。
        """
        try:
            parsed = UUID(session_id)
        except ValueError:
            parsed = None
        if parsed is None or str(parsed) != session_id.lower():
            return ActionableError(
                f"{label} identifier scheme mismatch: {session_id}; expected UUID", next_action=SESSION_ID_NEXT_ACTION
            )
        if resolution is None:
            resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.RELEASED:
            reason = "retention expired" if resolution.released_reason == "retention_expired" else "stopped"
            return ActionableError(
                f"unknown {label}: {session_id}; released by the owning agents_server ({reason}, {resolution.released_at}); "
                "its result is no longer retained",
                next_action=UNKNOWN_SESSION_NEXT_ACTION,
            )
        return ActionableError(
            f"unknown {label}: {session_id}; no agents_server on this host has a record of this session",
            next_action=UNKNOWN_SESSION_NEXT_ACTION,
        )

    def _take_notices(self, session_id: str) -> list[dict[str, str]]:
        """待機対象sessionの正常な通知を回収し、送信時刻順に返す。"""
        if self._status_writer is None:
            return []
        return self._status_writer.take_notices(session_id)

    @staticmethod
    def _response_with_notices(response: dict[str, Any], notices: list[dict[str, str]]) -> dict[str, Any]:
        """回収した通知がある場合だけ応答へ追加する。"""
        if notices:
            response["notices"] = notices
        return response

    @staticmethod
    def _result_response(session: SessionState, *, include_progress: bool = True) -> dict[str, Any]:
        """waitまたはkillの応答を組み立て、返した終端結果を回収済みにする。

        `elapsed_seconds`はturnの`started_at`起点である。
        最終活動時刻と停滞の印は`list`が返すため、本応答へは載せない。
        """
        response = session.public_status(include_result=session.result_available)
        if response.get("status") in TERMINAL_STATUSES:
            response["launch_identity"] = {
                "engine": session.engine,
                "model": session.model,
                "effort": session.effort,
                "source": "launch_candidate",
            }
            response["observed_identity"] = observed_session_identity(session.session_id)
        if response.get("status") == "running":
            elapsed_seconds = turn_elapsed_seconds(session.started_at)
            if elapsed_seconds is not None:
                response["elapsed_seconds"] = elapsed_seconds
        if not include_progress:
            response.pop("progress", None)
            response.pop("elapsed_seconds", None)
        if "agent_message" in response:
            session.result_delivered = True
            session.touch()
        return response

    def _kill_result_response(self, session: SessionState, *, kill_requested: bool) -> dict[str, Any]:
        """killの応答を組み立て、回収した通知がある場合だけ付ける。"""
        response = self._result_response(session, include_progress=False)
        if "agent_message" in response and self._status_writer is not None:
            self._status_writer.delete_result(session.session_id, collector="kill")
        response["kill_requested"] = kill_requested
        return self._response_with_notices(response, self._take_notices(session.session_id))

    async def _resume_response(self, pending: PendingResume, ticket: int) -> dict[str, Any]:
        """同じ再開taskの配送結果を返し、呼び出し取消時は対応promptだけを外す。"""
        try:
            session = await asyncio.shield(pending.task)
        except asyncio.CancelledError:
            pending.prompt.cancel(ticket)
            raise
        delivery = "reply_failed" if session.result_available else "reply_started"
        response: dict[str, Any] = {"delivery": delivery, "label": session.label}
        previous_result = pending.take_previous_result()
        if previous_result:
            response["previous_result"] = previous_result
        return response
