"""セッション状態の更新を登録簿と通知先へ公開する。"""

from __future__ import annotations

# SessionStateが所有する登録の差分と通知の状態を、このモジュールで更新する。
# pylint: disable=protected-access
import asyncio
import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agent_toolkit._agents_server.file_operations import finish_task, run_file_operation
from agent_toolkit._agents_server.logging_config import redact_diagnostic_values
from agent_toolkit._agents_server.state_resume import utc_now
from agent_toolkit._atk import usage_refresh
from agent_toolkit._common import session_registry
lazy from agent_toolkit._agents_server import state

if TYPE_CHECKING:
    from agent_toolkit._agents_server.state import SessionState


def add_lifecycle_listener(listener: Callable[[SessionState, str], None]) -> None:
    """稼働の開始と結果確定の通知先を登録する。"""
    state._LIFECYCLE_LISTENERS.add(listener)


def remove_lifecycle_listener(listener: Callable[[SessionState, str], None]) -> None:
    """稼働の開始と結果確定の通知先を解除する。"""
    state._LIFECYCLE_LISTENERS.discard(listener)


def add_touch_listener(listener: Callable[[], None], *, sessions: dict[str, SessionState] | None = None) -> None:
    """session状態の更新通知先を登録する。"""
    state._TOUCH_LISTENERS.add(listener)
    if sessions is not None:
        state._TOUCH_OWNERS[listener] = sessions


def remove_touch_listener(listener: Callable[[], None]) -> None:
    """session状態の更新通知先を解除する。"""
    state._TOUCH_LISTENERS.discard(listener)
    state._TOUCH_OWNERS.pop(listener, None)


def add_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終了時の結果が確定したsessionの通知先を登録する。"""
    state._TERMINAL_LISTENERS.add(listener)


def notify_touch_listeners(session: SessionState | None = None) -> None:
    """登録された全ての通知先へ、session状態の更新を通知する。"""
    for listener in tuple(state._TOUCH_LISTENERS):
        owner = state._TOUCH_OWNERS.get(listener)
        if session is None or owner is None or owner.get(session.session_id) is session:
            listener()


def remove_terminal_listener(listener: Callable[[SessionState], None]) -> None:
    """turnの終了時の結果が確定したsessionの通知先を解除する。"""
    state._TERMINAL_LISTENERS.discard(listener)


def attach_executor(self: SessionState, pid: int | None) -> None:
    """turnを実行する委譲先CLIのプロセスを記録する。プロセスを起動し直した場合は次の`touch`で登録簿を更新する。"""
    if pid is None or (self.executor_process is not None and self.executor_process.pid == pid):
        return
    self.executor_process = session_registry.ProcessIdentity.of(pid)


async def attach_executor_async(self: SessionState, pid: int | None) -> None:
    """プロセスの起動時刻の照会をworkerで待ち、loop上で登録情報へ反映する。"""
    if pid is None or (self.executor_process is not None and self.executor_process.pid == pid):
        return
    self.executor_process = await run_file_operation(session_registry.ProcessIdentity.of, pid)


def touch(self: SessionState) -> None:
    """状態の更新時刻を現在時刻へ更新する。

    turnの終了時の結果が確定した時点で、登録済みの終了通知先へこのsessionを1回だけ渡す。
    turnを再開した後の終了では、同じ通知を改めて1回行う。
    """
    self.updated_at = utc_now()
    usage_refresh.track_session(self)
    message = (
        self.agent_message
        if self.result_available
        else (self.pending_result.get("agent_message") if self.pending_result is not None else None)
    )
    if isinstance(message, str):
        responses = dict(self.final_responses)
        responses[self.turn_seq] = message
        self.final_responses = tuple(responses.items())
    if self.result_available:
        self.terminal_error = (
            json.dumps(redact_diagnostic_values(self.error), ensure_ascii=False) if self.error is not None else None
        )
        if self.finalized_at is None:
            self.finalized_at = utc_now()
        if self.retention_deadline is None:
            self.retention_deadline = asyncio.get_running_loop().time() + state.RESULT_RETENTION_SECONDS
    else:
        self.retention_deadline = None
    registry_terminal = self.result_available
    event = None
    if self.status == "running" and not self._lifecycle_running:
        self._lifecycle_running = True
        event = "start"
    elif registry_terminal and self._lifecycle_running:
        self._lifecycle_running = False
        event = "terminal"
    if event is not None:
        for lifecycle_listener in tuple(state._LIFECYCLE_LISTENERS):
            lifecycle_listener(self, event)
    if self.publish_registry:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self.publish_registration()
        else:
            if self._registration_task is None or self._registration_task.done():
                self._registration_task = loop.create_task(_publish_registration_until_current(self))
    if not registry_terminal:
        self._terminal_notified = False
    elif not self._terminal_notified:
        self._terminal_notified = True
        state._LOG.info(
            "session_transition event=terminal session_id=%s writer=state status=%s turn_seq=%d",
            self.session_id,
            self.status,
            self.turn_seq,
        )
        for terminal_listener in tuple(state._TERMINAL_LISTENERS):
            terminal_listener(self)
    notify_touch_listeners(self)


async def _publish_registration_until_current(self: SessionState) -> str | None:
    if not session_registry.owner_process_is_cached():
        await run_file_operation(session_registry.owner_process)
    while _registration_parameters(self) is not None:
        error = await self.publish_registration_async()
        if error is not None:
            return error
    return None


async def drain_registration(self: SessionState) -> None:
    """通知から開始した登録公開を完了させる。"""
    task = self._registration_task
    if task is not None:
        await finish_task(task)


async def publish_registration_async(self: SessionState) -> str | None:
    """確定した登録情報だけをworkerへ渡し、成功したsnapshotをloopで覚える。"""
    async with self._registration_lock:
        return await _publish_registration_async(self)


async def _publish_registration_async(self: SessionState) -> str | None:
    if not session_registry.owner_process_is_cached():
        await run_file_operation(session_registry.owner_process)
    parameters = _registration_parameters(self)
    if parameters is None:
        return None
    executor = self.executor_process

    def remember(_result: None) -> None:
        _remember_registration(self, parameters, executor)

    try:
        await run_file_operation(session_registry.publish, self.session_id, on_success=remember, **parameters)
    except OSError as error:
        state._LOG.exception("session登録の保存に失敗しました: session_id=%s cwd=%s", self.session_id, self.cwd)
        return str(error)
    return None


def publish_registration(self: SessionState) -> str | None:
    """登録簿を更新し、保存不成立でも開始済みのsessionと通知を失わせない。

    成功した値だけを公開済みとして覚えるため、次の状態更新・flushでも保存を再試行できる。
    この操作だけでは活動時刻を進めない。
    """
    parameters = _registration_parameters(self)
    if parameters is None:
        return None
    try:
        session_registry.publish(self.session_id, **parameters)
    except OSError as error:
        state._LOG.exception("session登録の保存に失敗しました: session_id=%s cwd=%s", self.session_id, self.cwd)
        return str(error)
    _remember_registration(self, parameters, self.executor_process)
    return None


def _registration_parameters(self: SessionState) -> dict[str, Any] | None:
    if not self.publish_registry or self.result_delivered:
        return None
    registry_state = session_registry.PublicationState.from_status(self.status, result_available=self.result_available)
    # 再開ではbackendの初回公開の後に起動情報を写すため、その変化でも更新する。
    launch_info = session_registry.LaunchInfo.of(self)
    if not (
        registry_state != self._published_registry_state
        or self.turn_seq != self._published_registry_turn_seq
        or self.launcher_session_id != self._published_registry_launcher
        or self.fast_mode != self._published_registry_fast_mode
        or self.result_contract_recovery_consumed != self._published_registry_result_contract_recovery_consumed
        or launch_info != self._published_registry_launch_info
        or self.effort != self._published_registry_effort
        or self.executor_process != self._published_registry_executor
        or self.owner_root_session_id != self._published_registry_owner_root
    ):
        return None
    owner = session_registry.owner_process()
    return {
        "state": registry_state,
        "engine": self.engine,
        "cwd": self.cwd,
        "model": self.model,
        "effort": self.effort,
        "fast_mode": self.fast_mode,
        "codex_model_provider": self.codex_model_provider,
        "codex_subscription_provider": self.codex_subscription_provider,
        "codex_state_provider": self.codex_state_provider,
        "claude_api_env_file": self.claude_api_env_file,
        "claude_connection": self.claude_connection,
        "claude_role": self.claude_role,
        "model_type": self.model_type,
        "launch_kind": self.launch_kind,
        "turn_seq": self.turn_seq,
        "launch_info": launch_info,
        "result_contract_recovery_consumed": self.result_contract_recovery_consumed,
        "started_at": self.started_at,
        "session_updated_at": self.updated_at,
        "turn_id": self.turn_id or None,
        # 先頭を所有サーバーとする順序契約を保つため、所有サーバーの識別を得られない場合は委譲先CLIも載せない。
        "processes": ()
        if owner is None
        else tuple(process for process in (owner, self.executor_process) if process is not None),
        "owner_root_session_id": self.owner_root_session_id,
        "launcher_session_id": self.launcher_session_id,
    }


def _remember_registration(
    self: SessionState, parameters: dict[str, Any], executor: session_registry.ProcessIdentity | None
) -> None:
    self._published_registry_effort = parameters["effort"]
    self._published_registry_launcher = parameters["launcher_session_id"]
    self._published_registry_state = parameters["state"]
    self._published_registry_turn_seq = parameters["turn_seq"]
    self._published_registry_fast_mode = parameters["fast_mode"]
    self._published_registry_result_contract_recovery_consumed = parameters["result_contract_recovery_consumed"]
    self._published_registry_launch_info = parameters["launch_info"]
    self._published_registry_executor = executor
    self._published_registry_owner_root = parameters["owner_root_session_id"]
