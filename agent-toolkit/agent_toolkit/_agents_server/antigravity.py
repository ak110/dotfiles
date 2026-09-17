"""Antigravity CLI（`agy`）を1turn1プロセスで所有するバックエンド。

Antigravity CLIは非対話モードに常駐プロトコルを持たず、`-p`で渡した1つの指示を実行して終わる。
このため継続は`--conversation <会話識別子>`を付けた新しい実行とし、turnごとにプロセスを起動する。
`--output-format stream-json`が返す`init`・`step_update`・`result`の各イベントを読み、
`SessionState`へ反映する責務をこのモジュールが持つ。

当該engineはモデルを明示指定したときだけ選ぶ用途限定の経路であり、候補列のフォールバックの対象にしない。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from typing import Any

from agent_toolkit._agents_server import process_tree
from agent_toolkit._agents_server import state as shared_state
from agent_toolkit._agents_server.state import (
    AUTO_RESUME_NOTICE,
    LAUNCH_SYSTEM_PROMPTS,
    LaunchKind,
    ModelCandidate,
    ResumePrompt,
    SessionInitializationTimeoutError,
    SessionOwnerGoneError,
    SessionState,
    _initialize_turn,
    _validate_prompt,
)

_LOG = logging.getLogger("agent-toolkit.agents-server.antigravity")
_COMMAND = "agy"
_ENV_DELEGATED_SESSION = "AGENT_TOOLKIT_DELEGATED_SESSION"
# 非対話実行の既定の上限は5分であり、委譲先の1turnはこれを超える。
_PRINT_TIMEOUT_SECONDS = 3600
_STDERR_LIMIT_CHARS = 4000


def _system_prompt(launch_kind: LaunchKind) -> str:
    """起動条件の種別に応じたシステム指示を返す。

    Antigravity CLIの非対話モードはシステム指示の専用オプションを持たないため、
    当該指示は本文の先頭へ置いて渡す。
    """
    return f"{LAUNCH_SYSTEM_PROMPTS[launch_kind]}\n{AUTO_RESUME_NOTICE}"


def build_command(
    prompt: str,
    model: str | None,
    effort: str | None,
    conversation_id: str | None,
) -> list[str]:
    """`agy`の非対話実行のコマンド列を組む。"""
    command = [_COMMAND, "-p", prompt, "--output-format", "stream-json", "--print-timeout", str(_PRINT_TIMEOUT_SECONDS)]
    if model is not None:
        command += ["--model", model]
    if effort is not None:
        command += ["--effort", effort]
    if conversation_id is not None:
        command += ["--conversation", conversation_id]
    # 非対話モードは対話確認を持たず、原稿の書き換えまでを任せる用途のためツール実行を事前承認する。
    command.append("--dangerously-skip-permissions")
    return command


def _child_env() -> dict[str, str]:
    """委譲先の印を付けた環境を返す。"""
    env = dict(os.environ)
    env[_ENV_DELEGATED_SESSION] = "1"
    return env


def _event_text(payload: dict[str, Any]) -> str:
    """イベントが持つ表示用の本文を返す。"""
    for key in ("response", "text", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


class AntigravityManager:
    """Antigravity CLIのturnを所有するタスクと結果メタデータを管理する。"""

    def __init__(
        self,
        sessions: dict[str, SessionState] | None = None,
        condition: asyncio.Condition | None = None,
        publish_registry: bool = False,
    ) -> None:
        self.sessions = sessions if sessions is not None else {}
        self._condition = condition if condition is not None else asyncio.Condition()
        self._publish_registry = publish_registry
        self._tasks: set[asyncio.Task[Any]] = set()
        self._task_sessions: dict[asyncio.Task[Any], str] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}

    async def start(
        self,
        prompt: str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
    ) -> SessionState:
        """新しい会話を開始し、`init`イベントで会話識別子を確定したsessionを返す。"""
        return await self._start_turn(
            prompt,
            cwd,
            model,
            effort,
            session=None,
            conversation_id=None,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=1,
        )

    async def resume(
        self,
        session_id: str,
        prompt: ResumePrompt | str,
        cwd: str,
        model: str | None = None,
        effort: str | None = None,
        *,
        model_type: str | None = None,
        launch_kind: LaunchKind = "delegate",
        excluded_candidates: frozenset[ModelCandidate] = frozenset(),
        turn_seq: int = 0,
    ) -> SessionState:
        """保存済みの会話識別子へ新しいturnを開始する。"""
        await self._stop_owned_task(session_id)
        resume_text = await _resume_text(prompt)
        return await self._start_turn(
            resume_text,
            cwd,
            model,
            effort,
            session=None,
            conversation_id=session_id,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            turn_seq=turn_seq + 1,
        )

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """終端済みturnの後続として、同じ会話識別子で新しいturnを開始する。"""
        _validate_prompt(prompt)
        async with session.turn_control_lock:
            if not session.terminal:
                raise ValueError("the active Antigravity turn has not finished")
            previous_result = {"status": session.status, "agent_message": session.agent_message, "error": session.error}
            await self._stop_owned_task(session.session_id)
            await self._start_turn(
                prompt,
                session.cwd,
                session.model,
                session.effort,
                session=session,
                conversation_id=session.session_id,
                model_type=session.model_type,
                launch_kind=session.launch_kind,
                excluded_candidates=session.excluded_candidates,
                turn_seq=session.turn_seq + 1,
            )
        return {"delivery": "reply_started", "previous_result": previous_result}

    async def interrupt(self, session: SessionState) -> None:
        """実行中のturnのプロセスを終了して中断を確定する。"""
        if session.terminal:
            return
        session.interrupt_requested = True
        session.touch()
        await self._stop_owned_task(session.session_id)
        if not session.terminal:
            _finalize_turn(session, {"status": "interrupted", "agent_message": session.agent_message, "error": None})
        session.interrupt_requested = False
        await self._notify_waiters()

    async def release_session(self, session_id: str) -> None:
        """当該sessionを所有するタスクとプロセスを終了する。"""
        await self._stop_owned_task(session_id)

    async def close(self) -> None:
        """所有中の全turnを終了する。"""
        tasks = tuple(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_owned_task(self, session_id: str) -> None:
        task = next((item for item in self._tasks if self._task_sessions.get(item) == session_id), None)
        if task is None:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def _forget_task(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        self._task_sessions.pop(task, None)

    async def _notify_waiters(self) -> None:
        async with self._condition:
            self._condition.notify_all()

    async def _start_turn(
        self,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        *,
        session: SessionState | None,
        conversation_id: str | None,
        model_type: str | None,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
        turn_seq: int,
    ) -> SessionState:
        loop = asyncio.get_running_loop()
        initialized: asyncio.Future[SessionState] = loop.create_future()
        task: asyncio.Task[Any] = asyncio.create_task(
            self._run(
                f"{_system_prompt(launch_kind)}\n\n{prompt}",
                cwd,
                model,
                effort,
                initialized,
                session=session,
                conversation_id=conversation_id,
                model_type=model_type,
                launch_kind=launch_kind,
                excluded_candidates=excluded_candidates,
                turn_seq=turn_seq,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._forget_task)
        try:
            return await asyncio.wait_for(initialized, timeout=shared_state.SESSION_INITIALIZATION_TIMEOUT)
        except TimeoutError as exc:
            await self._release_unstarted_task(task)
            raise SessionInitializationTimeoutError(
                f"Antigravity CLI did not reach init within {shared_state.SESSION_INITIALIZATION_TIMEOUT:.0f}s: "
                f"cwd={cwd}, launch_kind={launch_kind}, model={model}"
            ) from exc
        except BaseException:
            await self._release_unstarted_task(task)
            raise

    async def _release_unstarted_task(self, task: asyncio.Task[Any]) -> None:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        self._forget_task(task)

    async def _run(
        self,
        prompt: str,
        cwd: str,
        model: str | None,
        effort: str | None,
        initialized: asyncio.Future[SessionState],
        *,
        session: SessionState | None,
        conversation_id: str | None,
        model_type: str | None,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
        turn_seq: int,
    ) -> None:
        process: asyncio.subprocess.Process | None = None
        stderr_text = ""
        finalized = False
        try:
            process = await asyncio.create_subprocess_exec(
                *build_command(prompt, model, effort, conversation_id),
                cwd=cwd,
                env=_child_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            if session is not None:
                self._attach_session(session, process, turn_seq)
                if not initialized.done():
                    initialized.set_result(session)
            async for line in process.stdout:
                payload = decode_event(line)
                if payload is None:
                    continue
                kind = payload.get("type") or payload.get("event")
                if kind == "init" and session is None:
                    session = self._create_session(
                        payload,
                        cwd=cwd,
                        model=model,
                        effort=effort,
                        model_type=model_type,
                        launch_kind=launch_kind,
                        excluded_candidates=excluded_candidates,
                        turn_seq=turn_seq,
                        conversation_id=conversation_id,
                    )
                    self._attach_session(session, process, turn_seq)
                    if not initialized.done():
                        initialized.set_result(session)
                elif kind == "step_update" and session is not None:
                    text = _event_text(payload)
                    if text:
                        session.set_progress(text)
                        await self._notify_waiters()
                elif kind == "result" and session is not None:
                    _finalize_turn(session, result_values(session, payload))
                    finalized = True
                    await self._notify_waiters()
            stderr_text = await _read_stderr(process)
            await process.wait()
            if session is None:
                raise RuntimeError(f"Antigravity CLI ended before the init event: stderr={stderr_text}")
            if not finalized:
                raise RuntimeError(f"Antigravity CLI ended before the result event: stderr={stderr_text}")
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # pylint: disable=broad-exception-caught
            if session is None:
                if not initialized.done():
                    initialized.set_exception(exc)
            elif not finalized:
                _record_failure(session, exc, stderr_text)
                await self._notify_waiters()
        finally:
            if process is not None:
                await _terminate(process)
            if session is not None:
                self._processes.pop(session.session_id, None)

    def _attach_session(self, session: SessionState, process: asyncio.subprocess.Process, turn_seq: int) -> None:
        self.sessions[session.session_id] = session
        self._processes[session.session_id] = process
        current_task = asyncio.current_task()
        if current_task is not None:
            self._task_sessions[current_task] = session.session_id
        session.turn_seq = turn_seq
        _initialize_turn(session)

    def _create_session(
        self,
        payload: dict[str, Any],
        *,
        cwd: str,
        model: str | None,
        effort: str | None,
        model_type: str | None,
        launch_kind: LaunchKind,
        excluded_candidates: frozenset[ModelCandidate],
        turn_seq: int,
        conversation_id: str | None,
    ) -> SessionState:
        session_id = payload.get("conversation_id") or payload.get("conversationId")
        if not isinstance(session_id, str) or not session_id:
            raise RuntimeError("Antigravity init event did not contain conversation_id")
        if conversation_id is not None and session_id != conversation_id:
            raise RuntimeError("Antigravity resume returned an unexpected conversation_id")
        return SessionState(
            session_id=session_id,
            cwd=cwd,
            model_type=model_type,
            launch_kind=launch_kind,
            excluded_candidates=excluded_candidates,
            model=model,
            effort=effort,
            engine="agy",
            turn_seq=turn_seq,
            publish_registry=self._publish_registry,
        )


async def _resume_text(prompt: ResumePrompt | str) -> str:
    """再開の入力を1つの本文へそろえる。

    `ResumePrompt`は配送先のコルーチンへ1件だけ渡す形を取る。
    Antigravity CLIはプロセスの起動時に本文を確定するため、当該配送で本文だけを受け取る。
    """
    if isinstance(prompt, str):
        return prompt
    captured: list[str] = []

    async def capture(text: str) -> None:
        captured.append(text)

    await prompt.deliver(capture)
    return captured[0]


def decode_event(line: bytes) -> dict[str, Any] | None:
    """`stream-json`の1行をイベントへ変換する。解釈できない行は無視する。"""
    text = line.decode("utf-8", errors="replace").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        _LOG.debug("Antigravity CLIの非JSON出力を無視: %s", text[:200])
        return None
    return payload if isinstance(payload, dict) else None


def result_values(session: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
    """`result`イベントを終端結果へ変換する。"""
    status = payload.get("status")
    agent_message = _event_text(payload) or session.agent_message
    if status in {"completed", "success", "ok"} and agent_message.strip():
        return {"status": "completed", "agent_message": agent_message, "error": None}
    if status in {"interrupted", "cancelled", "canceled"}:
        return {"status": "interrupted", "agent_message": agent_message, "error": None}
    error = payload.get("error")
    message = error if isinstance(error, str) and error else "Antigravity CLI returned no assistant output"
    if isinstance(error, dict):
        message = str(error.get("message") or error)
    return {"status": "failed", "agent_message": agent_message, "error": {"message": message}}


def _finalize_turn(session: SessionState, result: dict[str, Any]) -> None:
    """最新turnの終端結果を確定する。"""
    session.pending_result = None
    session.status = result["status"]
    session.agent_message = result["agent_message"]
    session.error = result["error"]
    session.turn_completed = True
    session.turn_start_ambiguous = False
    session.touch()


def _record_failure(session: SessionState, error: BaseException, stderr_text: str) -> None:
    """失敗を終端結果へ確定し、原因の特定に要する標準エラーを添える。"""
    failure: dict[str, Any] = {
        "message": str(error) or error.__class__.__name__,
        "engine": "agy",
        "model": session.model,
        "stderr": stderr_text.strip()[:_STDERR_LIMIT_CHARS],
    }
    _LOG.error("antigravity_session_failure session_id=%s model=%s stderr=%s", session.session_id, session.model, stderr_text)
    _finalize_turn(session, {"status": "failed", "agent_message": session.agent_message, "error": failure})


async def _read_stderr(process: asyncio.subprocess.Process) -> str:
    """標準エラーを有界で読み取る。"""
    if process.stderr is None:
        return ""
    with contextlib.suppress(Exception):
        data = await process.stderr.read(_STDERR_LIMIT_CHARS)
        return data.decode("utf-8", errors="replace")
    return ""


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """委譲先プロセスとその子孫を終了する。"""
    if process.returncode is not None:
        return
    descendants = process_tree.collect_descendants(process.pid)
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(process.wait(), timeout=5.0)
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
        with contextlib.suppress(Exception):
            await process.wait()
    residual = await asyncio.to_thread(process_tree.reclaim_descendants, descendants)
    process_tree.log_residual(residual, context=f"agy pid={process.pid}")


def check_dependencies() -> None:
    """コマンド列の構築を通し、モジュールの読み込みだけで失敗しないことを確かめる。"""
    build_command("", None, None, None)


__all__ = ["AntigravityManager", "SessionOwnerGoneError", "check_dependencies"]
