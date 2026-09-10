"""CodexとClaudeの委譲先を非同期MCPとして公開する。"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import json
import logging
import os
import pathlib
import re
import warnings
from collections.abc import AsyncIterator, Sequence
from typing import Annotated, Any
from uuid import UUID

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from agent_toolkit._agents_server import claude as claude_backend
from agent_toolkit._agents_server import codex as codex_backend
from agent_toolkit._agents_server import session_registry, state, status_file
from agent_toolkit._agents_server.state import (
    TERMINAL_STATUSES,
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
    add_terminal_listener,
    add_touch_listener,
    finalize_pending_result,
    has_pending_auto_resume_targets,
    has_uncollected_result,
    record_unobserved_sessions,
    remove_terminal_listener,
    remove_touch_listener,
    selected_candidate,
)
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import inherited_venv as _inherited_venv
from agent_toolkit._common import wait_schedule as _wait_schedule

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
_TASK_DOCUMENT_PATTERN = re.compile(r"^(?P<path>/\S+\.subagent\.md)")
_REQUIRED_INPUT_PREFIX = "必須入力名: "
_REQUIRED_INPUT_NAME_PATTERN = re.compile(r"^[^`\s:，、](?:[^`\s，、]*[^`\s:，、])?$")
_SHARE_DIRECTORY = pathlib.Path(__file__).resolve().parent.parent / "share"


def _is_agent_toolkit_task_document(path: pathlib.Path) -> bool:
    """agent-toolkit pluginのshare直下にあるタスク文書だけを受理する。"""
    if path.parent.name != "share":
        return False
    try:
        manifest = json.loads((path.parent.parent / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "agent-toolkit"


@dataclasses.dataclass
class _PendingResume:
    """timeout後も同じsessionから観測・共有する再開操作。"""

    state: SessionResumeState
    task: asyncio.Task[SessionState]
    prompt: ResumePrompt
    previous_result: dict[str, Any] | None = None

    def discard_previous_result(self) -> None:
        """配送又は明示的な破棄の後に退避済み結果本文を除く。"""
        self.previous_result = None

    def take_previous_result(self) -> dict[str, Any] | None:
        """退避済み結果を返し、進行中再開から本文を取り除く。"""
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


def _elapsed_seconds(started_at_value: str) -> int | None:
    """開始時刻から現在までの経過秒を返す。"""
    try:
        started_at = datetime.datetime.fromisoformat(started_at_value)
    except (TypeError, ValueError):
        return None
    return max(0, int(datetime.datetime.now(tz=started_at.tzinfo).timestamp() - started_at.timestamp()))


def _shell_prompt(command: str, summary_policy: str) -> str:
    """コマンドと要約方針を、シェル実行委譲先への指示本文へ組み立てる。"""
    return f"次のコマンドを実行し、結果を報告せよ。\n\n実行するコマンド:\n{command}\n\n要約方針:\n{summary_policy}"


def _validate_required_prompt_inputs(prompt: str) -> str | None:
    """通常委譲の起動文をタスク文書の必須入力名と照合する。"""
    lines = prompt.splitlines()
    match = _TASK_DOCUMENT_PATTERN.match(lines[0] if lines else "")
    if match is None:
        return "必須入力検査を実施できません: 起動文の1行目からタスク文書の絶対パスを取得できません。"
    task_document = pathlib.Path(match.group("path")).resolve()
    if not _is_agent_toolkit_task_document(task_document):
        return f"必須入力検査を実施できません: タスク文書がshare配下ではありません: {task_document}"
    try:
        document_lines = task_document.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        return f"必須入力検査を実施できません: タスク文書をUTF-8で読めません: {task_document}: {error}"
    try:
        input_heading = document_lines.index("## 入力")
    except ValueError:
        return f"必須入力検査を実施できません: タスク文書に## 入力がありません: {task_document}"
    section = document_lines[input_heading + 1 :]
    next_heading = next((index for index, line in enumerate(section) if line.startswith("## ")), len(section))
    section = section[:next_heading]
    try:
        fence = section.index("```text")
        marker = section[fence + 1]
    except (ValueError, IndexError):
        return f"必須入力検査を実施できません: ## 入力にtextコードブロックがありません: {task_document}"
    if not marker.startswith(_REQUIRED_INPUT_PREFIX):
        return f"必須入力検査を実施できません: 必須入力名を取得できません: {task_document}"
    required_names = marker.removeprefix(_REQUIRED_INPUT_PREFIX).split(",")
    if not required_names or any(not _REQUIRED_INPUT_NAME_PATTERN.fullmatch(name) for name in required_names):
        return f"必須入力検査を実施できません: 必須入力名の書式が不正です: {task_document}"
    prompt_lines = lines[1:]
    missing = [name for name in required_names if not any(line.startswith(f"{name}:") for line in prompt_lines)]
    if missing:
        raise ValueError(f"必須入力が欠けています: {', '.join(missing)}; タスク文書: {task_document}")
    return None


_DEFAULT_STATUS_WRITER = object()


class AgentsServerManager:
    """エンジン別バックエンドと共有session状態を管理する。"""

    def __init__(
        self,
        status_writer: status_file.StatusFileWriter | None | object = _DEFAULT_STATUS_WRITER,
    ) -> None:
        self.sessions: dict[str, SessionState] = (
            status_writer.sessions if isinstance(status_writer, status_file.StatusFileWriter) else {}
        )
        self.expired_sessions: dict[str, SessionResumeState] = {}
        self.stopped_sessions: dict[str, SessionResumeState] = {}
        self._pending_resumes: dict[str, _PendingResume] = {}
        self._condition = asyncio.Condition()
        self._resume_lock = asyncio.Lock()
        self._codex: Any = None
        self._claude: Any = None
        self._wait_timeouts: dict[str, float] = {}
        self._carried_unavailable_candidates: dict[tuple[str, LaunchKind], ModelCandidate] = {}
        self._pending_unobserved_child_sessions: dict[str, tuple[int, set[str]]] = {}
        self._heartbeat_task: asyncio.Task[None] | None = None
        if status_writer is _DEFAULT_STATUS_WRITER:
            identity = status_file.resolve_status_file_identity(os.environ)
            self._status_writer = status_file.StatusFileWriter(self.sessions, identity) if identity is not None else None
        else:
            assert status_writer is None or isinstance(status_writer, status_file.StatusFileWriter)
            self._status_writer = status_writer
        if self._status_writer is not None:
            add_touch_listener(self._status_writer.schedule)
        add_terminal_listener(self._carry_over_unavailable_candidate)
        add_terminal_listener(self._record_pending_unobserved_child_sessions)

    def activate(self) -> None:
        """状態ファイル出力を有効化する。"""
        if self._status_writer is not None:
            self._status_writer.activate()
            self._heartbeat_task = asyncio.create_task(self._refresh_heartbeat())

    async def _refresh_heartbeat(self) -> None:
        """MCPサーバーの生存中に状態ファイルの生存の印を更新する。"""
        assert self._status_writer is not None
        while True:
            await asyncio.sleep(status_file.HEARTBEAT_INTERVAL_SECONDS)
            self._status_writer.flush()

    def _backend(self, engine: str) -> Any:
        if engine == "codex":
            if self._codex is None:
                self._codex = codex_backend.AppServerManager(
                    self.sessions,
                    self._condition,
                    publish_registry=True,
                )
            return self._codex
        if engine == "claude":
            if self._claude is None:
                self._claude = claude_backend.ClaudeServerManager(
                    self.sessions,
                    self._condition,
                    expire_session=self._expire_session,
                    publish_registry=True,
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
        """期限に到達したsession本体を解放し、再開状態と未回収結果を保持する。"""
        self._pending_unobserved_child_sessions.pop(session_id, None)
        session = self.sessions.pop(session_id, None)
        if session is not None:
            resume_state = SessionResumeState.from_session(session)
            self.expired_sessions[session_id] = resume_state
            if self._status_writer is not None:
                self._status_writer.retain_result(resume_state)
                self._status_writer.schedule()

    def _resolve_expired_session(self, session_id: str) -> SessionResumeState | None:
        """保持期限を反映し、期限切れsessionの再開状態を返す。"""
        if not isinstance(session_id, str) or not session_id:
            return None
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
        return self.expired_sessions.get(session_id)

    def _expired_result_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionの未回収結果を1回だけ返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        if self._status_writer is not None and self._status_writer.result_state(session_id) == "consumed":
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.expired_sessions[session_id] = resume_state
        response = self._stopped_result_response(resume_state)
        if response is None:
            return {"status": "expired"}
        self.expired_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id)
            self._status_writer.schedule()
        return response

    def _resolve_stopped_session(self, session_id: str) -> SessionResumeState | None:
        """破棄済みsessionを返し、外部経路による結果回収を反映する。"""
        resume_state = self.stopped_sessions.get(session_id)
        if resume_state is None:
            return None
        if (
            not resume_state.result_delivered
            and resume_state.finalized_at is not None
            and self._status_writer is not None
            and self._status_writer.result_state(session_id) == "consumed"
        ):
            resume_state = dataclasses.replace(resume_state, result_delivered=True)
            self.stopped_sessions[session_id] = resume_state
        return resume_state

    def _restore_registry_session(self, session_id: str) -> SessionResumeState | None:
        """再起動前の終端sessionを登録簿から最小の再開状態へ復元する。"""
        if session_id in self.sessions or session_id in self.stopped_sessions or session_id in self.expired_sessions:
            return None
        resolution = session_registry.resolve(session_id)
        if resolution.state is session_registry.Resolution.RUNNING:
            raise ValueError(f"error.recovery=turn_unobserved: {session_id}")
        if resolution.state is session_registry.Resolution.MISSING:
            return None
        if resolution.state is session_registry.Resolution.UNREADABLE:
            raise ValueError(f"error.recovery=unreadable: {session_id}")
        info = resolution.resume_info
        if info is None:
            raise ValueError(f"error.recovery=no_resume_info: {session_id}")
        persisted_result = self._status_writer.read_result(session_id) if self._status_writer is not None else None
        resume_state = SessionResumeState(
            session_id=session_id,
            cwd=info.cwd,
            model=info.model,
            effort=info.effort,
            engine=info.engine,
            model_type=info.model_type,
            launch_kind=info.launch_kind,
            turn_seq=persisted_result["turn_seq"] if persisted_result is not None else info.turn_seq,
            status=persisted_result["status"] if persisted_result is not None else info.status,
            agent_message=persisted_result["agent_message"] if persisted_result is not None else "",
            error=persisted_result.get("error") if persisted_result is not None else None,
            finalized_at=persisted_result["finalized_at"] if persisted_result is not None else None,
            result_delivered=persisted_result is None,
        )
        self.stopped_sessions[session_id] = resume_state
        return resume_state

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
        return response

    def _take_stopped_result(self, session_id: str, resume_state: SessionResumeState) -> dict[str, Any] | None:
        """破棄済みsessionの未回収結果を返し、配送済みとして記録する。"""
        response = self._stopped_result_response(resume_state)
        if response is None:
            return None
        self.stopped_sessions[session_id] = dataclasses.replace(resume_state, result_delivered=True)
        if self._status_writer is not None:
            self._status_writer.delete_result(session_id)
            self._status_writer.schedule()
        return response

    @staticmethod
    def _recovered_result_response(resume_state: SessionResumeState) -> dict[str, Any]:
        """再起動前の終端種別と結果本文を回収できない事実を返す。"""
        return {"status": resume_state.status, "recovery": "result_unavailable"}

    def _expired_kill_response(self, session_id: str) -> dict[str, Any] | None:
        """期限切れsessionなら中断対象が無いことを示す成功応答を返す。"""
        resume_state = self._resolve_expired_session(session_id)
        if resume_state is None:
            return None
        return {"status": "expired", "kill_requested": False}

    @staticmethod
    def _listed_session(
        session: SessionState | SessionResumeState,
        *,
        status: str,
        progress: str,
        result_available: bool,
    ) -> dict[str, Any]:
        """sessionを一覧向けの公開項目へ射影する。

        最終活動時刻からの経過が閾値を超えたsessionへ`stalled`を付す。
        停滞の判定は待機せずに行えるよう、非終端の待機応答ではなく本項目で返す。
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
        seconds_since_update = _elapsed_seconds(session.updated_at)
        if seconds_since_update is not None:
            listed["updated_at"] = session.updated_at
            listed["seconds_since_update"] = seconds_since_update
            if seconds_since_update >= state.STALL_NOTICE_SECONDS:
                listed["stalled"] = True
        return listed

    def list_sessions(self, *, include_terminated: bool = False) -> dict[str, Any]:
        """保持中のsessionを開始時刻順の公開項目へ射影する。

        未回収結果を持つsessionは、終端済み又は期限切れでも既定の一覧へ残す。
        """
        loop_time = asyncio.get_running_loop().time()
        for session_id, session in tuple(self.sessions.items()):
            if session.retention_deadline is not None and loop_time >= session.retention_deadline:
                self._expire_session(session_id)

        listed: dict[str, tuple[str, dict[str, Any]]] = {}
        for session in self.sessions.values():
            listed[session.session_id] = (
                session.started_at,
                self._listed_session(
                    session,
                    status=session.status,
                    progress=session.progress,
                    result_available=has_uncollected_result(
                        session,
                        None
                        if self._status_writer is None
                        else self._status_writer.result_state(session.session_id) == "consumed",
                    ),
                ),
            )
        for pending in self._pending_resumes.values():
            session = pending.state
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(session, status="running", progress="", result_available=False),
                ),
            )
        for session in self.expired_sessions.values():
            listed.setdefault(
                session.session_id,
                (
                    session.started_at,
                    self._listed_session(
                        session,
                        status="expired",
                        progress="",
                        result_available=has_uncollected_result(
                            session,
                            None
                            if self._status_writer is None
                            else self._status_writer.result_state(session.session_id) == "consumed",
                        ),
                    ),
                ),
            )
        sessions = [entry for _, entry in sorted(listed.values(), key=lambda item: item[0])]
        if include_terminated:
            return {"sessions": sessions, "omitted": 0}
        visible = [
            session
            for session in sessions
            if session["result_available"] or session["status"] not in TERMINAL_STATUSES | {"expired"}
        ]
        return {"sessions": visible, "omitted": len(sessions) - len(visible)}

    async def stop(self, session_id: str, *, retain_result: bool = False) -> dict[str, Any]:
        """終端済みsessionを破棄し、会話再開用の最小状態だけを保持する。

        `stopped_sessions`への在籍は、このプロセスが解放すべきbackend資源を
        持たないことを表す。解放後の同期失敗では再発行が同期だけを再試行する。
        """
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id must be a non-empty string")
        if session_id in self._pending_resumes:
            raise ValueError(f"session is running: {session_id}; issue kill before stop if interruption is required")
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is None:
            stopped_state = self._restore_registry_session(session_id)
        already_stopped = session_id in self.stopped_sessions
        session = self.sessions.get(session_id)
        if (
            session is not None
            and session.retention_deadline is not None
            and asyncio.get_running_loop().time() >= session.retention_deadline
        ):
            self._expire_session(session_id)
            session = None
        if session is not None:
            if not session.terminal:
                raise ValueError(f"session is running: {session_id}; issue kill before stop if interruption is required")
            resume_state = SessionResumeState.from_session(session)
        else:
            resume_state = stopped_state or self.expired_sessions.get(session_id)
            if resume_state is None:
                raise self._unresolved_session_error(session_id, label="session")
        result_state = None if self._status_writer is None else self._status_writer.result_state(session_id)
        keep_result = retain_result and resume_state.finalized_at is not None and result_state != "consumed"
        resume_state = dataclasses.replace(resume_state, result_delivered=not keep_result)
        if session is not None:
            session.result_delivered = not keep_result
        if not already_stopped:
            await self._backend(resume_state.engine).release_session(session_id)
        self._pending_unobserved_child_sessions.pop(session_id, None)
        self.sessions.pop(session_id, None)
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions[session_id] = resume_state
        if self._status_writer is not None:
            try:
                if keep_result and result_state == "unpublished" and not self._status_writer.result_exists(session_id):
                    self._status_writer.retain_result(resume_state)
                elif not keep_result and (result_state == "published" or self._status_writer.result_exists(session_id)):
                    self._status_writer.delete_result(session_id)
                self._status_writer.schedule()
            except Exception as exc:
                raise RuntimeError(
                    f"backend resources released; state synchronization incomplete for session {session_id}: {exc}"
                ) from exc
        return {}

    async def _stop_after_terminal_response(
        self,
        session_id: str,
        response: dict[str, Any],
        stop: bool,
    ) -> dict[str, Any]:
        """要求された終端応答に限りsessionを破棄し、元の応答を維持する。"""
        if stop and response.get("status") in {*TERMINAL_STATUSES, "expired"} and session_id not in self.stopped_sessions:
            await self.stop(session_id, retain_result=True)
        return response

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
    ) -> tuple[list[ModelCandidate], frozenset[ModelCandidate]]:
        """起動条件を検証し、除外後の候補列を設定順で返す。"""
        candidates = _atk_config.resolve_model_candidates(model_type)
        if not candidates:
            raise ValueError(f"no model candidates remain for model_type: {model_type}")
        key = (model_type, launch_kind)
        carried = self._carried_unavailable_candidates.get(key)
        excluded = frozenset({carried}) if carried is not None else frozenset()
        remaining = [item for item in candidates if item not in excluded]
        if not remaining:
            self._carried_unavailable_candidates.pop(key, None)
            return candidates, frozenset()
        return remaining, excluded

    def _carry_over_unavailable_candidate(self, session: SessionState) -> None:
        """可用性を理由に終端した候補を、同じ起動条件の次回へ引き継ぐ。

        終端結果が確定した時点の通知として`SessionState.touch`から呼ぶ。
        結果本文の受領、`stop`による破棄、保持期限切れのいずれを経ても記録が漏れないよう、
        記録の契機を終端の確定点だけに置く。共有の通知先は全managerへ届くため、
        自身が保持するsessionだけを記録の対象とする。
        """
        if self.sessions.get(session.session_id) is not session:
            return
        candidate = selected_candidate(session)
        if not _engine_unavailable(session) or candidate is None or session.model_type is None:
            return
        self._carried_unavailable_candidates[(session.model_type, session.launch_kind)] = candidate

    def _record_pending_unobserved_child_sessions(self, session: SessionState) -> None:
        """自動再開をまたいで保持した未観測の孫sessionを終端結果へ併合する。"""
        if self.sessions.get(session.session_id) is not session:
            return
        pending = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending is None or pending[0] != session.turn_seq:
            return
        _, unobserved = self._pending_unobserved_child_sessions.pop(session.session_id)
        if unobserved:
            record_unobserved_sessions(session, unobserved)

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

        起動直後にengineの可用性を理由として終端した候補だけを
        除外集合へ加えて次候補へ進む。backendの起動例外では候補を進めない。
        候補を変えても結果が変わらない失敗では候補を進めず、そのまま呼び出し元へ返す。
        """
        candidates, excluded = self._resolve_start_candidates(
            model_type,
            launch_kind=launch_kind,
        )
        _validate_prompt(prompt)
        _validate_cwd(cwd)
        unavailable_response: dict[str, Any] | None = None
        unavailable_session: SessionState | None = None
        display_label = status_file.normalize_label(prompt if label is None else label)
        for candidate_index, candidate in enumerate(candidates):
            engine, model, effort = candidate
            if engine not in SUPPORTED_ENGINES:
                raise ValueError(f"unsupported engine: {engine}")
            _validate_model_effort(model, effort)
            # backendが資源を作成した後に失敗することもあるため、例外では候補を進めない。
            session = await self._backend(engine).start(
                prompt,
                cwd,
                model,
                effort,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded,
            )
            session.engine = engine
            await self._await_start_outcome(session)
            response = {
                "session_id": session.session_id,
                "status": session.status,
                "engine": engine,
                "model_type": model_type,
                "model": model,
                "effort": effort,
            }
            if self._status_writer is not None:
                response["root_session_id"] = self._status_writer.root_session_id
            if not _engine_unavailable(session):
                self._carried_unavailable_candidates.pop((model_type, launch_kind), None)
                session.label = display_label
                session.announced = True
                session.touch()
                return response
            unavailable_response, unavailable_session = response, session
            excluded |= {candidate}
            if candidate_index + 1 < len(candidates):
                await self._abandon_unavailable_session(session)
        if unavailable_response is not None:
            assert unavailable_session is not None
            unavailable_session.label = display_label
            unavailable_session.announced = True
            unavailable_session.touch()
            return unavailable_response
        raise RuntimeError(f"no available model candidates: {model_type}")

    async def _abandon_unavailable_session(self, session: SessionState) -> None:
        """次候補へ進む前に可用性失敗sessionの全資源を解放する。"""
        await self._backend(session.engine).release_session(session.session_id)
        self.sessions.pop(session.session_id, None)
        if self._status_writer is not None:
            self._status_writer.delete_result(session.session_id)
            self._status_writer.schedule()

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
    ) -> dict[str, Any]:
        """探索専用の軽量な起動条件でturnを開始する。"""
        model_type = "explore_fast" if fast else "explore"
        return await self.start(
            model_type,
            prompt,
            cwd,
            launch_kind="explore",
        )

    async def start_shell(
        self,
        command: str,
        cwd: str,
        summary_policy: str,
    ) -> dict[str, Any]:
        """コマンド実行専用の軽量な起動条件でturnを開始する。"""
        _validate_shell_request(command, summary_policy)
        return await self.start(
            "explore_fast",
            _shell_prompt(command, summary_policy),
            cwd,
            launch_kind="shell",
            label=command,
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

    def _wait_target_ids(self) -> list[str]:
        """待機の対象となる保持中sessionを識別子順に返す。

        未回収の終端結果を持つ破棄済み又は期限切れsessionも対象へ含め、
        呼び出し元が結果本文を回収できないまま失う経路を残さない。
        """
        targets = set(self.sessions) | set(self._pending_resumes)
        for session_id, resume_state in (*self.expired_sessions.items(), *self.stopped_sessions.items()):
            if self._stopped_result_response(resume_state) is not None:
                targets.add(session_id)
        return sorted(targets)

    def _retained_result_response(self, session_id: str) -> dict[str, Any] | None:
        """破棄済み又は期限切れsessionの未回収の終端結果だけを返す。"""
        stopped_state = self._resolve_stopped_session(session_id)
        if stopped_state is not None:
            response = self._take_stopped_result(session_id, stopped_state)
            if response is None:
                return None
            return self._response_with_notices(response, self._take_notices(session_id))
        if self._resolve_expired_session(session_id) is None:
            return None
        response = self._expired_result_response(session_id)
        if response is None or "agent_message" not in response:
            return None
        return self._response_with_notices(response, self._take_notices(session_id))

    async def wait(self) -> dict[str, Any]:
        """委譲先の終端を待ち、終端時だけ結果本文を返す。

        引数を受け取らない。対象は当該MCPサーバープロセスが保持する起動中のsession全体とし、最初に終端した1件の結果を返す。
        残るsessionの終端結果は次の呼び出しまで保持する。
        待機上限はプロンプトキャッシュの保持期間から導出した値とし、委譲先として起動されたセッションでは240秒を上限とする。
        当該上限へ達した応答は`status`と`elapsed_seconds`を返す。
        保持中のsessionの最終活動時刻と停滞の印は`list`が返す。待機せずに現状態を確認する場合は`list`を発行する。
        以下の`/goal`の条件に該当しない場合は、本ツールを前景で発行する。
        呼び出し元のセッションに`/goal`が設定され、未完了の背景タスクが本ツールの背景移行だけになる場合は、
        本ツールの背景移行で待たず、`atk agents-wait`を実行ホストの背景ジョブとして起動して待機表明でターンを終える。
        当該背景ジョブの完了通知を受領した後に本ツールを1回発行し、結果本文の配送を確定させる。
        委譲先が背景作業を残してturnを終えた場合は、同じsessionを一度だけ自動的に再開し、再開したturnの終端まで待つ。
        呼び出し元は背景作業の完了後に`send_message`で再開を指示しない。
        終端前に`status: running`が返った場合は、本ツールを再発行して待機を継続する。
        終端結果は呼び出し元が最初の呼び出しで受領するまで保持し、経過時間では解放しない。
        受領した終端結果のsessionを破棄する場合は`stop`を発行する。
        終端結果を残さずにsessionが失われた場合だけ、`status`が`expired`の応答を返す。
        委譲先が実行中に`atk agents-notify`で送った通知が未回収である場合は、終端前でも当該通知を`notices`へ載せて復帰する。
        再待機の要否は`notices`の有無ではなく`status`で判定する。
        `status`が`completed`、`failed`、`interrupted`のいずれかである応答は終端であり、`notices`を含む場合も結果本文とともに受領して本ツールを再発行しない。
        応答へ載せた通知は回収済みとして再び返さない。
        """
        timeout = await self._resolve_wait_timeout("main")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        ordered_ids = self._wait_target_ids()
        # 保留中の結果を進める判定は待機の刻みごとに1回だけ行う。
        # backendは背景作業の完了通知で受け取った再開turnの結果へ保留中の結果を差し替えるため、
        # 通知のたびに判定すると当該差し替えの前に保留中の結果を確定してしまう。
        advance_pending = True

        while True:
            for session_id in ordered_ids:
                retained_response = self._retained_result_response(session_id)
                if retained_response is not None:
                    return {"session_id": session_id, **retained_response}
                session = self.sessions.get(session_id)
                if session is not None and advance_pending:
                    await self._advance_child_session_wait(session)
            advance_pending = False
            async with self._condition:
                terminal: list[SessionState] = []
                for session_id in ordered_ids:
                    candidate = self.sessions.get(session_id)
                    if candidate is not None and candidate.result_available and not candidate.result_delivered:
                        terminal.append(candidate)
                terminal.sort(key=lambda candidate: (candidate.finalized_at or "", candidate.session_id))
                if terminal:
                    session = terminal[0]
                    response = self._response_with_notices(
                        self._result_response(session), self._take_notices(session.session_id)
                    )
                    return {"session_id": session.session_id, **response}

                for session_id in ordered_ids:
                    session = self.sessions.get(session_id)
                    if session is None:
                        continue
                    notices = self._take_notices(session_id)
                    if notices:
                        response = self._response_with_notices(self._result_response(session), notices)
                        return {"session_id": session_id, **response}

                retained = [self.sessions[session_id] for session_id in ordered_ids if session_id in self.sessions]
                pending_ids = [session_id for session_id in ordered_ids if session_id in self._pending_resumes]
                if not retained and not pending_ids:
                    if not ordered_ids:
                        return {"status": "expired"}
                    session_id = ordered_ids[0]
                    return {"session_id": session_id, "status": "expired"}

                remaining = deadline - loop.time()
                if remaining <= 0:
                    if not retained:
                        session_id = pending_ids[0]
                        response = self._pending_resume_status(self._pending_resumes[session_id])
                        return {"session_id": session_id, **response}
                    session = next((candidate for candidate in retained if not candidate.result_delivered), retained[0])
                    return {"session_id": session.session_id, **self._result_response(session)}
                interval = 0.1 if any(candidate.awaiting_auto_resume for candidate in retained) else 1.0
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=min(interval, remaining))
                except TimeoutError:
                    advance_pending = True

    async def _advance_child_session_wait(self, session: SessionState) -> None:
        """保留中の結果を、孫sessionの終端又は保持期限に応じて進める。"""
        if not session.awaiting_auto_resume or session.pending_result is None:
            return
        resolutions = {session_id: session_registry.resolve(session_id) for session_id in session.live_child_session_ids}
        terminal = {
            session_id
            for session_id, resolution in resolutions.items()
            if resolution.state is session_registry.Resolution.TERMINAL
        }
        unobserved = {
            session_id
            for session_id, resolution in resolutions.items()
            if resolution.state in {session_registry.Resolution.MISSING, session_registry.Resolution.UNREADABLE}
        }
        pending_unobserved = self._pending_unobserved_child_sessions.get(session.session_id)
        if pending_unobserved is not None:
            unobserved.update(pending_unobserved[1])
        for session_id in terminal:
            session.live_child_session_ids.discard(session_id)
            session.terminal_child_session_ids.add(session_id)
            session_registry.remove(session_id)
        session.live_child_session_ids.difference_update(unobserved)

        if not has_pending_auto_resume_targets(session) and session.terminal_child_session_ids:
            identifiers = sorted(session.terminal_child_session_ids)
            prompt = (
                "あなたが`agents_server`で起動した次のsessionは終端した。\n"
                f"終端したsession: {', '.join(identifiers)}\n"
                "各sessionの結果を確認し、所定の返却形式を返せ。"
            )
            session.auto_resume_consumed = True
            pending_result = session.pending_result
            assert pending_result is not None
            session.status = pending_result["status"]
            session.agent_message = pending_result["agent_message"]
            session.error = pending_result["error"]
            if unobserved:
                self._pending_unobserved_child_sessions[session.session_id] = (session.turn_seq + 1, unobserved)
            try:
                await self._backend(session.engine).send_message(session, prompt)
            except Exception as exc:
                self._pending_unobserved_child_sessions.pop(session.session_id, None)
                session.awaiting_auto_resume = False
                session.auto_resume_deadline = None
                session.pending_result = None
                session.live_child_session_ids.clear()
                session.status = "failed"
                session.agent_message = pending_result["agent_message"]
                session.error = {
                    "message": f"{type(exc).__name__}: {exc}",
                    "unobservedSessions": sorted(set(identifiers) | unobserved),
                }
                session.turn_completed = True
                session.turn_start_ambiguous = False
                session.touch()
                return
            if session.pending_result is not None:
                finalize_pending_result(session, touch=False)
            if self._status_writer is not None:
                self._status_writer.delete_result(session.session_id)
            return

        if not has_pending_auto_resume_targets(session):
            finalize_pending_result(session)
            if unobserved:
                record_unobserved_sessions(session, unobserved)
            return

        deadline = session.auto_resume_deadline
        if deadline is not None and asyncio.get_running_loop().time() >= deadline:
            unobserved = set(session.live_child_session_ids)
            finalize_pending_result(session)
            if unobserved:
                record_unobserved_sessions(session, unobserved)

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
        """wait又はkillの応答を組み立て、返した終端結果を回収済みにする。

        `elapsed_seconds`はturnの`started_at`起点である。
        最終活動時刻と停滞の印は`list`が返すため、本応答へは載せない。
        """
        response = session.public_status(include_result=session.result_available)
        if response.get("status") == "running":
            elapsed_seconds = _elapsed_seconds(session.started_at)
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
        response["kill_requested"] = kill_requested
        return self._response_with_notices(response, self._take_notices(session.session_id))

    def _pending_resume_status(self, pending: _PendingResume) -> dict[str, Any]:
        """進行中の再開操作を通常のrunning状態として射影する。"""
        session = self.sessions.get(pending.state.session_id)
        if session is not None:
            return self._result_response(session)
        response: dict[str, Any] = {"status": "running", "progress": ""}
        elapsed_seconds = _elapsed_seconds(pending.state.started_at)
        if elapsed_seconds is not None:
            response["elapsed_seconds"] = elapsed_seconds
        return response

    async def _run_resume(self, resume_state: SessionResumeState, prompt: ResumePrompt) -> SessionState:
        """backendの再開を完了し、失敗時だけ再試行用状態を復元する。"""
        session_id = resume_state.session_id
        backend = self._backend(resume_state.engine)
        try:
            session = await backend.resume(
                session_id,
                prompt,
                resume_state.cwd,
                resume_state.model,
                resume_state.effort,
                model_type=resume_state.model_type,
                launch_kind=resume_state.launch_kind,
                excluded_candidates=resume_state.excluded_candidates,
                turn_seq=resume_state.turn_seq,
            )
            if self._status_writer is not None:
                self._status_writer.delete_result(session_id)
            session.announced = True
            session.touch()
            return session
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
    ) -> tuple[_PendingResume, int]:
        """期限切れ状態を一意な進行中再開操作へ原子的に移す。"""
        session_id = resume_state.session_id
        self.expired_sessions.pop(session_id, None)
        self.stopped_sessions.pop(session_id, None)
        resume_prompt = ResumePrompt(prompt)
        task = asyncio.create_task(self._run_resume(resume_state, resume_prompt))
        pending = _PendingResume(
            state=resume_state,
            task=task,
            prompt=resume_prompt,
            previous_result=previous_result,
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
        response: dict[str, Any] = {"delivery": delivery}
        previous_result = pending.take_previous_result()
        if previous_result:
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
                announced=True,
                turn_seq=resume_state.turn_seq + 1,
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
    ) -> dict[str, Any]:
        """保存済みの最小状態から同じ会話を再開し、公開契約の応答を返す。

        再開は結果保持期限の経過と所有主体の終了の双方を契機とする。
        結果本文を保持したまま所有主体だけが終了した場合は、直前結果を応答へ含める。
        """
        pending, ticket = self._start_resume(
            resume_state,
            prompt,
            previous_result,
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
                                previous_result = self._take_stopped_result(session_id, stopped_state)
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
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
                            self._expire_session(session_id)
                            resume_state = self.expired_sessions[session_id]
                            return await self._resume_and_reply(
                                resume_state,
                                prompt,
                                previous_result,
                            )
                    if self._status_writer is not None:
                        self._status_writer.delete_result(session_id)
                    delivery = result["delivery"]
                    if delivery in {"reply_started", "reply_ambiguous"}:
                        session.reset_progress()
                    response: dict[str, Any] = {"delivery": delivery}
                    if delivery in REPLY_DELIVERIES:
                        previous_result = result["previous_result"]
                        if previous_result:
                            response["previous_result"] = previous_result
                    return response
        except TimeoutError as exc:
            raise TimeoutError(
                f"send_message timed out: {session_id}; delivery is undetermined, observe the session with wait"
            ) from exc

    async def kill(
        self,
        session_id: str,
        timeout: float = DEFAULT_KILL_TIMEOUT,
        stop: bool = False,
    ) -> dict[str, Any]:
        """実行中turnへ中断を要求し、指定時間まで終端を待つ。"""
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout < 0:
            raise ValueError("timeout must be non-negative")
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
            stopped_response = self._take_stopped_result(session_id, stopped_state)
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
                raise TimeoutError(f"kill timed out: {session_id}; the interrupt request was not delivered") from exc
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
                raise TimeoutError(
                    f"kill timed out: {session_id}; the interrupt request was delivered but the turn did not terminate"
                ) from exc
        response = self._kill_result_response(session, kill_requested=True)
        return await self._stop_after_terminal_response(session_id, response, stop)

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def close(self) -> None:
        """初期化済みバックエンドを停止する。"""
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
        backends = tuple(backend for backend in (self._codex, self._claude) if backend is not None)
        for backend in backends:
            await backend.close()
        remove_terminal_listener(self._carry_over_unavailable_candidate)
        remove_terminal_listener(self._record_pending_unobserved_child_sessions)
        if self._status_writer is not None:
            remove_touch_listener(self._status_writer.schedule)
            self._status_writer.deactivate()


_MANAGER = AgentsServerManager()


@contextlib.asynccontextmanager
async def _mcp_lifespan(_server: FastMCP[Any]) -> AsyncIterator[None]:
    _MANAGER.activate()
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
            "CodexまたはClaudeへの非同期委譲。承認操作は公開しない。\n"
            "`start`と`start_explore`でsessionを開始し、`start_shell`でコマンドの実行と要約を委譲する。"
            "`wait`で終端と結果本文を受け取る。`list`は保持中のsessionの状態をまとめて返す。"
            "継続は`send_message`、実行中turnの中断は`kill`、終端済みsessionの明示的な破棄は`stop`で行う。\n"
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
) -> dict[str, Any]:
    """工程別モデル設定の候補から委譲先turnを開始する。

    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    返した`session_id`は同じ応答の中で`wait`を発行して観測するか、結果が不要なら`kill`で破棄する。
    応答は`session_id`、`status`と、採用した`model_type`、`engine`、`model`及び`effort`を含む。
    状態ファイルの書込先を解決できる場合は、PostToolUseフックが索引へ使う`root_session_id`も含む。
    全候補がengineの可用性を理由として終端した場合は、最後の候補の終端応答を返す。
    全候補のbackend開始が例外で失敗した場合は、最後の例外を送出する。
    """
    input_validation_warning = _validate_required_prompt_inputs(prompt)
    response = await _MANAGER.start(model_type, prompt, cwd)
    if input_validation_warning is not None:
        response["input_validation_warning"] = input_validation_warning
    return response


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
) -> dict[str, Any]:
    """探索専用の軽量な起動条件で委譲先turnを開始する。

    engineの利用上限などで起動できない候補はサーバーが自動的に除外し、残る候補で起動する。
    返した`session_id`は同じ応答の中で`wait`を発行して観測するか、結果が不要なら`kill`で破棄する。
    プロジェクト指示の読込を減らした軽量な起動条件で開始する。
    起動時のシステム指示でファイルを作成、変更及び削除しない契約を委譲先へ課すため、成果ファイルの出力を依頼しない。
    委譲と直接実行の採算は、追加のツール呼び出しが2回以上必要か、読む対象の合計が4,000トークンを超えるかで判定する。
    いずれかに当たる調査は本ツールへ委譲し、1回の検索または1ファイルの部分読み取りで確定する調査は自ら実行する。
    この目安は、呼び出し元の1リクエストの文脈量147,000トークンと、セッションの残りリクエスト数47を前提とする。
    文脈量が小さいセッションの初期では直接実行が相対的に有利になる。
    応答と、候補が尽きた場合の扱いは`start`と同じである。
    """
    return await _MANAGER.start_explore(fast, prompt, cwd)


@mcp.tool(name="start_shell", structured_output=True)
async def start_shell(
    command: Annotated[str, Field(description="実行するコマンド。委譲先がシェルで実行する。")],
    cwd: Annotated[str, Field(description="実行時の作業ディレクトリ。既存ディレクトリの絶対パスとする。")],
    summary_policy: Annotated[str, Field(description="結果の要約方針。報告へ含める値と粒度を書く。")],
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
    return await _MANAGER.start_shell(command, cwd, summary_policy)


@mcp.tool(name="wait", structured_output=True)
async def wait() -> dict[str, Any]:
    """委譲先の終端を待ち、終端時だけ結果本文を返す。

    引数を受け取らない。対象は当該MCPサーバープロセスが保持する起動中のsession全体とし、最初に終端した1件の結果を返す。
    残るsessionの終端結果は次の呼び出しまで保持する。
    待機上限はプロンプトキャッシュの保持期間から導出した値とし、委譲先として起動されたセッションでは240秒を上限とする。
    当該上限へ達した応答は`status`と`elapsed_seconds`を返す。
    保持中のsessionの最終活動時刻と停滞の印は`list`が返す。待機せずに現状態を確認する場合は`list`を発行する。
    以下の`/goal`の条件に該当しない場合は、本ツールを前景で発行する。
    呼び出し元のセッションに`/goal`が設定され、未完了の背景タスクが本ツールの背景移行だけになる場合は、
    本ツールの背景移行で待たず、`atk agents-wait`を実行ホストの背景ジョブとして起動して待機表明でターンを終える。
    当該背景ジョブの完了通知を受領した後に本ツールを1回発行し、結果本文の配送を確定させる。
    委譲先が背景作業を残してturnを終えた場合は、同じsessionを一度だけ自動的に再開し、再開したturnの終端まで待つ。
    呼び出し元は背景作業の完了後に`send_message`で再開を指示しない。
    終端前に`status: running`が返った場合は、本ツールを再発行して待機を継続する。
    終端結果は呼び出し元が最初の呼び出しで受領するまで保持し、経過時間では解放しない。
    受領した終端結果のsessionを破棄する場合は`stop`を発行する。
    終端結果を残さずにsessionが失われた場合だけ、`status`が`expired`の応答を返す。
    委譲先が実行中に`atk agents-notify`で送った通知が未回収である場合は、終端前でも当該通知を`notices`へ載せて復帰する。
    再待機の要否は`notices`の有無ではなく`status`で判定する。
    `status`が`completed`、`failed`、`interrupted`のいずれかである応答は終端であり、`notices`を含む場合も結果本文とともに受領して本ツールを再発行しない。
    応答へ載せた通知は回収済みとして再び返さない。
    """
    return await _MANAGER.wait()


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
    応答は`delivery`と、未回収の終端結果がある場合の`previous_result`だけを含む。
    直前結果は、`wait`又は`kill`が当該結果本文を返していない場合だけ`previous_result`へ含める。返済みの場合は`previous_result`のキーを応答へ追加しない。
    sessionの起動後に工程別モデル設定の候補列が変わっても、起動時に確定したengine・model・effortで継続する。
    採用済みのengineが実際に利用不能で継続できない場合は、backendが返す理由に従って回復手段を選ぶ。
    保持済みsessionを失って継続できない場合は`unknown session: <session_id>`を返す。
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
    stop: Annotated[
        bool,
        Field(description="終端結果を返した応答に限り、同じsessionを応答後に破棄する。"),
    ] = False,
) -> dict[str, Any]:
    """実行中turnへ中断を要求し、指定時間まで終端を待つ。

    停止は最終手段とする。実行中の委譲先には`send_message`で訂正を配送できるため、
    そちらで意図を満たせる場合は、停止によって失われる作業と再起動の費用の方が大きい。
    本ツールを選ぶ前に、`send_message`による訂正では足りないことと、当該作業の継続自体が不要であることを確認する。
    通常の既定は270秒である。固有のtimeout要件がなければ引数を省略して通常既定を使う。
    `timeout=0`は中断要求配送後の現状態を返す。
    timeoutに達した場合もsessionとbackend processは破棄しないため、`wait`で状態を確認してから次の操作を選ぶ。
    終端結果の保持期限を過ぎたsessionでは中断する実行中turnが無いため、`status`へ`expired`、`kill_requested`へ`false`を設定した応答を返す。
    """
    return await _MANAGER.kill(session_id, timeout, stop)


@mcp.tool(name="stop", structured_output=True)
async def stop_session(session_id: str) -> dict[str, Any]:
    """再開する予定の無い終端済みsessionを明示的に破棄する。

    statusLineの表示対象と`list`の応答から除き、backendがsession専用に保持する資源を解放する。
    実行中turnを持つsessionは破棄しない。中断が必要な場合は先に`kill`を発行する。
    破棄後も同じ`session_id`への`send_message`で会話を暗黙再開できる。
    成功時は空のオブジェクトを返し、失敗は例外で示す。
    """
    return await _MANAGER.stop(session_id)


@mcp.tool(name="list", structured_output=True)
async def list_sessions(include_terminated: bool = False) -> dict[str, Any]:
    """保持中のsessionの状態を開始順に返す。

    各sessionの`session_id`、`status`、`progress`、`model_type`、`launch_kind`、`label`及び`result_available`を返す。
    あわせて各sessionの最終活動時刻を`updated_at`、そこからの経過秒数を`seconds_since_update`として返す。
    経過が閾値を超えたsessionには`stalled`を付す。
    待機せずに停滞を判定する場合は本ツールを発行する。
    `label`は起動文又はコマンドの先頭100文字までとし、切り詰めた場合は末尾へ`…`を付す。
    結果本文は返さないため、終端の観測と結果の受領は`wait`で行う。
    既定では未回収結果を持たない終端済み又は`expired`のsessionを除き、除いた件数を`omitted`へ返す。
    全件が必要な場合は`include_terminated`へ真を渡す。このとき`omitted`は0となる。
    保持していた`session_id`を失った場合の回復と、並行する委譲先の残作業の把握へ用いる。
    """
    return _MANAGER.list_sessions(include_terminated=include_terminated)


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
