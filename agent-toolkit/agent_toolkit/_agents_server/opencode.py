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
    if not model:
        raise session_errors.DelegateBackendError("OpenCodeのモデルが未設定。工程別のモデル候補を設定する")
    command = ["opencode", "run", "--model", model, "--format", "json", "--auto"]
    if effort is not None:
        command += ["--variant", effort]
    if conversation_id is not None:
        command += ["--session", conversation_id]
    return [*command, "--", prompt]


def system_prompt(launch_kind: LaunchKind) -> str:
    """起動の本文とユーザー規範を本文先頭へ連結する。

    `~/.claude/rules/agent-toolkit/`は旧い配布物の置き場で、更新時に回収するため読まない。
    """
    root = plugin_roots.SERVER_PLUGIN_ROOT
    sections = [launch_prompts.launch_prompt(launch_kind), launch_prompts.cli_runtime_instructions(root)]
    sections.append(f"agent-toolkit plugin root: {root}\nagent-toolkitのskillと内部資源はこのrootから読む。")
    user_rules = host_homes.claude_config_dir() / "rules"
    if user_rules.is_dir():
        for path in sorted(user_rules.rglob("*.md")):
            if not path.is_file() or path.is_relative_to(user_rules / "agent-toolkit"):
                continue
            sections.append(
                message_format.auto_message(
                    path.read_text(encoding="utf-8"), source="agents-server", kind="rules", attributes={"path": str(path)}
                )
            )
    return "\n\n".join(sections)


class OpenCodeManager(CliTurnManager):
    ENGINE = "opencode"
    NAME = "OpenCode CLI"
    LOG = logging.getLogger("agent-toolkit.agents-server.opencode")

    RUN_TIMEOUT = 3600.0
    # CLIのJSONイベントには起動入力が無いため、ログの消費側へturn入力を補う。
    RECORD_INPUT = True
    build_command = staticmethod(build_command)
    system_prompt = staticmethod(system_prompt)

    @staticmethod
    def event_session_id(payload: dict[str, Any]) -> str | None:
        value = payload.get("sessionID")
        return value if isinstance(value, str) and value else None

    async def process_event(self, session: SessionState, payload: dict[str, Any]) -> bool:
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
        if finalized:
            return
        if returncode != 0 or not session.agent_message.strip():
            raise session_errors.DelegateBackendError(
                f"OpenCode CLI returned no successful assistant output: exit={returncode}, stderr={stderr}"
            )
        finalize_turn(session, {"status": "completed", "agent_message": session.agent_message, "error": None})
