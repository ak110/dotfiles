"""Antigravity CLIのコマンドとstream-jsonを、共通のturn実行処理へ接続する。"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import shutil
import subprocess
from typing import Any

from agent_toolkit._agents_server import context_usage, resume_waits
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server.cli_turn import CliTurnManager, decode_event, finalize_turn
from agent_toolkit._agents_server.file_operations import run_file_operation
from agent_toolkit._agents_server.launch_prompts import (
    LIGHTWEIGHT_LAUNCH_KINDS,
    PUBLIC_WAIT_NOTICE,
    cli_runtime_instructions,
    launch_prompt,
    python_runtime_instructions,
)
from agent_toolkit._agents_server.session_errors import ActionableRuntimeError, SessionOwnerGoneError
from agent_toolkit._agents_server.state import LaunchKind, SessionState
from agent_toolkit._common import antigravity_cli, host_homes, process_tree
from agent_toolkit._common.antigravity_cli import build_command
from agent_toolkit._common.message_format import auto_message

_MODEL_OUTPUT_STEP_TYPES = frozenset({"agent_response", "tool"})
_MINIMUM_GEMINI_VERSION = (1, 5, 13)


def _gemini_delegation_binary(environment: dict[str, str]) -> str:
    home = pathlib.Path(environment.get("USERPROFILE" if os.name == "nt" else "HOME", ""))
    if not home.is_absolute():
        raise ActionableRuntimeError(
            "agy委譲に使うホームディレクトリを確認できません",
            next_action="起動元のHOME（WindowsではUSERPROFILE）を実在するホームの絶対パスに直す",
        )
    try:
        settings = json.loads((home / ".gemini" / "antigravity-cli" / "settings.json").read_text(encoding="utf-8"))
    except OSError, ValueError:
        settings = None
    if not isinstance(settings, dict) or settings.get("modelProvider") != "gemini":
        raise ActionableRuntimeError(
            "agy委譲にはGemini APIキー方式の明示選択が必要です",
            next_action=(
                "ユーザーがGemini APIキー方式を選ぶ場合は、"
                '`~/.gemini/antigravity-cli/settings.json`で`"modelProvider": "gemini"`を明示選択する。'
                "認証方式を自動で変更せず、個人ログインで代替しない"
            ),
        )
    if not environment.get("GEMINI_API_KEY", "").strip():
        raise ActionableRuntimeError(
            "agy委譲にGEMINI_API_KEYが設定されていません",
            next_action="ユーザーが起動元へGEMINI_API_KEYを設定する。キーの値を引数やログへ出さない",
        )
    executable = shutil.which("agy", path=environment.get("PATH", ""))
    if executable is None:
        raise ActionableRuntimeError(
            "agyが見つかりません", next_action="Gemini APIキー方式に対応するagyを導入し、起動元のPATHへ加える"
        )
    # 認証やモデル呼び出しを行わない版数照会に、APIキーを渡さない。
    probe_env = {key: value for key, value in environment.items() if key != "GEMINI_API_KEY"}
    try:
        version = process_tree.run_with_timeout(
            [executable, "--version"],
            env=probe_env,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError) as error:
        raise ActionableRuntimeError(
            "agyの版を確認できません", next_action="起動元で`agy --version`の診断を確認し、CLIの導入状態を直す"
        ) from error
    if not re.fullmatch(r"\d+\.\d+\.\d+", version) or tuple(map(int, version.split("."))) < _MINIMUM_GEMINI_VERSION:
        raise ActionableRuntimeError(
            "agyをGemini APIキー方式を固定できる版へ更新してください",
            next_action="agyを1.5.13以降へ更新し、起動元で`agy --version`を確認する",
        )
    return executable


def _system_prompt(launch_kind: LaunchKind) -> str:
    """役割、公開待機、保持配布物と委譲元のユーザー規範を本文先頭へ渡す。"""
    root = plugin_roots.SERVER_PLUGIN_ROOT
    parts = [
        launch_prompt(launch_kind),
        PUBLIC_WAIT_NOTICE,
        cli_runtime_instructions(root),
        f"agent-toolkit plugin root: {root}",
    ]
    if launch_kind in LIGHTWEIGHT_LAUNCH_KINDS:
        parts.append(python_runtime_instructions())

    config = host_homes.claude_config_dir()
    candidates = [config / "CLAUDE.md", *sorted((config / "rules").rglob("*.md"))]
    for path in candidates:
        if not path.is_file() or path.is_relative_to(config / "rules" / "agent-toolkit"):
            continue
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            logging.getLogger(__name__).warning(
                "ユーザー規範を読めません: %s: %s\n"
                "次の操作: 対象ファイルの読取権限とUTF-8を復旧する。復旧後の追送時に規範を再取得する。",
                path,
                error,
            )
            continue
        parts.append(auto_message(body, source="agents-server", kind="user-rules", attributes={"path": str(path)}))
    return "\n\n".join(parts)


def _event_text(payload: dict[str, Any]) -> str:
    for key in ("response", "text", "text_delta", "message", "summary"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def result_values(session: SessionState, payload: dict[str, Any]) -> dict[str, Any]:
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

    async def prepare_command(
        self, prompt: str, model: str | None, effort: str | None, conversation_id: str | None, environment: dict[str, str]
    ) -> list[str]:
        executable = await run_file_operation(_gemini_delegation_binary, environment)
        command = self.build_command(prompt, model, effort, conversation_id)
        return [executable, "--settings", '{"modelProvider":"gemini"}', *command[1:]]

    async def send_message(self, session: SessionState, prompt: str) -> dict[str, Any]:
        """保留した待機結果を引き継ぎ、同じ会話へ継続を配送する。"""
        held = resume_waits.HeldTurn.capture(session)
        if held is not None:
            resume_waits.finalize_pending_result(session, touch=False)
        try:
            return await super().send_message(session, prompt)
        except BaseException:
            if held is not None:
                held.restore(session)
            raise

    @staticmethod
    def event_session_id(payload: dict[str, Any]) -> str | None:
        return antigravity_cli.event_session_id(payload)

    async def process_event(self, session: SessionState, payload: dict[str, Any]) -> bool:
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
            if session.result_contract_recovery_pending:
                # 形式回復が保持した今回の本文を、孫待機用の旧状態で上書きしない。
                await self._notify_waiters()
                return True
            if resume_waits.has_pending_auto_resume_targets(session):
                resume_waits.begin_auto_resume_wait(
                    session, {"status": session.status, "agent_message": session.agent_message, "error": session.error}
                )
                session.status = "running"
                session.touch()
            await self._notify_waiters()
            return True
        return False


def check_dependencies() -> None:
    """コマンド列の構築を通し、モジュールの読み込みだけで失敗しないことを確かめる。"""
    build_command("", None, None, None)


__all__ = ["AntigravityManager", "SessionOwnerGoneError", "check_dependencies", "decode_event"]
