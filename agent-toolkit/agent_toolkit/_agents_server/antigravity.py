"""Antigravity CLIのコマンドとstream-jsonを、共通のturn実行処理へ接続する。"""

from __future__ import annotations

import logging
from typing import Any

from agent_toolkit._agents_server import context_usage
from agent_toolkit._agents_server.cli_turn import CliTurnManager, decode_event, finalize_turn
from agent_toolkit._agents_server.launch_prompts import LAUNCH_SYSTEM_PROMPTS
from agent_toolkit._agents_server.session_errors import SessionOwnerGoneError
from agent_toolkit._agents_server.state import LaunchKind, SessionState

_COMMAND = "agy"
_PRINT_TIMEOUT_SECONDS = 3600
_MODEL_OUTPUT_STEP_TYPES = frozenset({"agent_response", "tool"})


def _system_prompt(launch_kind: LaunchKind) -> str:
    """起動条件の種別に応じたシステム指示を返す。

    Antigravity CLIの非対話モードはシステム指示の専用オプションを持たないため、
    システム指示は本文の先頭へ置いて渡す。1つのメッセージへ配送本文と同居するため、
    委譲先が両者を区別できるよう、システム指示側は`launch_prompts.py`が付ける境界を保ったまま渡す。
    同じsessionの自動再開はこのbackendで確かめていないため、自動再開の通知（`AUTO_RESUME_NOTICE`）は加えない。
    """
    return LAUNCH_SYSTEM_PROMPTS[launch_kind]


def build_command(
    prompt: str,
    model: str | None,
    effort: str | None,
    conversation_id: str | None,
) -> list[str]:
    """`agy`の非対話実行のコマンド列を組む。"""
    command = [_COMMAND, "-p", prompt, "--output-format", "stream-json", "--print-timeout", f"{_PRINT_TIMEOUT_SECONDS}s"]
    if model is not None:
        command += ["--model", model]
    if effort is not None:
        command += ["--effort", effort]
    if conversation_id is not None:
        command += ["--conversation", conversation_id]
    # 非対話モードは実行中に承認を求められないため、ファイルの書き換えを含む作業を委譲できるようツール実行を事前承認する。
    command.append("--dangerously-skip-permissions")
    return command


def _event_text(payload: dict[str, Any]) -> str:
    """イベントが持つ表示用の本文を返す。"""
    for key in ("response", "text", "text_delta", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def result_values(session: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
    """`result`イベントを終端結果へ変換する。"""
    raw_status = payload.get("status")
    status = raw_status.lower() if isinstance(raw_status, str) else raw_status
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


class AntigravityManager(CliTurnManager):
    """Antigravityのイベント解釈とCLI引数を所有する。"""

    ENGINE = "agy"
    NAME = "Antigravity CLI"
    LOG = logging.getLogger("agent-toolkit.agents-server.antigravity")
    build_command = staticmethod(build_command)
    system_prompt = staticmethod(_system_prompt)

    @staticmethod
    def event_session_id(payload: dict[str, Any]) -> str | None:
        """initイベントが確定した会話識別子を返す。"""
        if (payload.get("type") or payload.get("event")) != "init":
            return None
        value = payload.get("conversation_id") or payload.get("conversationId")
        return value if isinstance(value, str) and value else None

    async def process_event(self, session: SessionState, payload: dict[str, Any]) -> bool:
        """進捗・使用量・resultを共有状態へ反映する。"""
        kind = payload.get("type") or payload.get("event")
        body = payload.get(kind) if isinstance(kind, str) else None
        body = body if isinstance(body, dict) else payload
        if kind == "step_update":
            if body.get("step_type") == "agent_response":
                usage = body.get("usage")
                usage = usage if isinstance(usage, dict) else {}
                total = context_usage.valid_number(usage.get("total_tokens"))
                cached = context_usage.valid_number(usage.get("cache_read_tokens"))
                context_usage.record_context_usage(
                    session, total + cached if total is not None and cached is not None else None, None
                )
            text = _event_text(body)
            if text:
                session.set_progress(text)
            output_step = body.get("step_type") in _MODEL_OUTPUT_STEP_TYPES
            if output_step:
                session.model_output_observed = True
            if text or output_step:
                await self._notify_waiters()
        elif kind == "result":
            finalize_turn(session, result_values(session, body))
            await self._notify_waiters()
            return True
        return False


def check_dependencies() -> None:
    """コマンド列の構築を通し、モジュールの読み込みだけで失敗しないことを確かめる。"""
    build_command("", None, None, None)


__all__ = ["AntigravityManager", "SessionOwnerGoneError", "check_dependencies", "decode_event"]
