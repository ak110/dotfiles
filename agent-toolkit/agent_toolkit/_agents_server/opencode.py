"""OpenCode 1の非対話JSONイベントを、turn単位のCLI実行へ接続する。

1.19.3の`run.ts`で確かめたsessionIDとpartを読み、CLIの正常終了をturnの完了として扱う。
イベントは変換前のまま保存する。後続の証拠抽出と使用量集計も同じ公開形式を読む。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent_toolkit._agents_server import context_usage, launch_prompts, session_errors
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server.cli_turn import CliTurnManager, finalize_turn
from agent_toolkit._agents_server.result_projection import action_detail
from agent_toolkit._agents_server.state import LaunchKind, SessionState
from agent_toolkit._common import host_homes, message_format


def build_command(prompt: str, model: str | None, effort: str | None, conversation_id: str | None) -> list[str]:
    """provider/modelとvariantを独立した引数として渡す。"""
    if not model:
        raise session_errors.DelegateBackendError("OpenCodeのモデルが未設定。工程別のモデル候補を設定する")
    command = ["opencode", "run", "--model", model, "--format", "json", "--auto"]
    if effort is not None:
        command += ["--variant", effort]
    if conversation_id is not None:
        command += ["--session", conversation_id]
    return [*command, "--", prompt]


def system_prompt(launch_kind: LaunchKind) -> str:
    """pluginの共通規範・委譲先規範・ユーザー規範を本文先頭へ連結する。"""
    root = plugin_roots.SERVER_PLUGIN_ROOT
    files = sorted((root / "rules").glob("*.md"))
    files.append(root / "share" / "rules-subagent.md")
    user_rules = host_homes.claude_config_dir() / "rules"
    if user_rules.is_dir():
        files.extend(sorted(path for path in user_rules.rglob("*.md") if path.is_file()))
    sections = [launch_prompts.LAUNCH_SYSTEM_PROMPTS[launch_kind], launch_prompts.cli_runtime_instructions(root)]
    sections.append(f"agent-toolkit plugin root: {root}\n必要なskillと内部資源はこのrootから読む。")
    for path in files:
        sections.append(
            message_format.auto_message(
                path.read_text(encoding="utf-8"), source="agents-server", kind="rules", attributes={"path": str(path)}
            )
        )
    return "\n\n".join(sections)


class OpenCodeManager(CliTurnManager):
    """OpenCodeのイベントと正常終了を共有セッション状態へ反映する。"""

    ENGINE = "opencode"
    NAME = "OpenCode CLI"
    LOG = logging.getLogger("agent-toolkit.agents-server.opencode")
    # runにはturnの実行時間上限が無いため、所有する処理で有限の上限を持つ。
    RUN_TIMEOUT = 3600.0
    # CLIのJSONイベントには起動入力が無いため、ログの消費側へturn入力を補う。
    RECORD_INPUT = True
    build_command = staticmethod(build_command)
    system_prompt = staticmethod(system_prompt)

    @staticmethod
    def event_session_id(payload: dict[str, Any]) -> str | None:
        """全JSONイベントの共通フィールドから会話識別子を返す。"""
        value = payload.get("sessionID")
        return value if isinstance(value, str) and value else None

    async def process_event(self, session: SessionState, payload: dict[str, Any]) -> bool:
        """本文・ツール実行・使用量と、接続先からの明示エラーを反映する。"""
        kind = payload.get("type")
        part = payload.get("part")
        part = part if isinstance(part, dict) else {}
        if kind == "error":
            error = payload.get("error")
            message = error if isinstance(error, str) else json.dumps(error, ensure_ascii=False)
            finalize_turn(session, {"status": "failed", "agent_message": session.agent_message, "error": {"message": message}})
            await self._notify_waiters()
            return True
        if kind in {"text", "reasoning", "tool_use"}:
            session.model_output_observed = True
            text = part.get("text")
            if isinstance(text, str) and text:
                session.set_progress(text)
                if kind == "text":
                    session.agent_message += text
            if kind == "tool_use":
                tool = str(part.get("tool") or "tool")
                state = part.get("state")
                detail = action_detail(state.get("input") if isinstance(state, dict) else None)
                session.last_action = f"{tool}: {detail}" if detail else tool
            session.status = "running"
            session.touch()
            await self._notify_waiters()
        if kind == "step_finish":
            tokens = part.get("tokens")
            if isinstance(tokens, dict):
                total = context_usage.valid_number(tokens.get("total"))
                context_usage.record_context_usage(session, total, None)
        return False

    def finish_process(self, session: SessionState, returncode: int | None, stderr: str, finalized: bool) -> None:
        """errorイベント・終了コードと最終本文からturnを終端させる。"""
        if finalized:
            return
        if returncode != 0 or not session.agent_message.strip():
            raise session_errors.DelegateBackendError(
                f"OpenCode CLI returned no successful assistant output: exit={returncode}, stderr={stderr}"
            )
        finalize_turn(session, {"status": "completed", "agent_message": session.agent_message, "error": None})
