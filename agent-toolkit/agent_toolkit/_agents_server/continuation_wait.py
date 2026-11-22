"""委譲先の定型返却から委譲元の応答待ちを判定する。"""

from __future__ import annotations

import pathlib
from typing import Any

from agent_toolkit._agents_server import result_projection, shared_layout
from agent_toolkit._common import session_registry, session_state, task_documents


def continuation_wait_seconds(session_id: str, *, state_root: pathlib.Path | None = None) -> int | None:
    """既存の登録簿で、次の配送が始まっていない最新の定型返却だけを調べる。"""
    resolved = session_registry.resolve(session_id, state_root=state_root)
    info = resolved.resume_info
    if (
        resolved.state != session_registry.Resolution.TERMINAL
        or resolved.resume_pending
        or info is None
        or info.status != "completed"
    ):
        return None
    task = task_documents.launched_task_document(info.launch_info.prompt)
    if task is None:
        return None
    responses = info.launch_info.final_responses
    if not responses or responses[-1][0] != info.turn_seq:
        return None
    lines = responses[-1][1].strip().splitlines()
    if not lines:
        return None
    declaration = task_documents.read_declaration(task)
    if isinstance(declaration, str) or not declaration.continuations:
        return None
    instructions = {entry.instruction for entry in declaration.continuations}
    head = lines[0]
    match head:
        case "計画作成完了" | "計画なし準備完了":
            waiting = head in declaration.result_heads and "実装開始" in instructions
        case "計画検査完了":
            # 通常は途中通知であり、マージなしの終了だけが次の指示を待つ。
            sections = [line for line in lines if line.startswith("統合区分:")]
            waiting = bool(sections) and all(line == "統合区分: マージなし" for line in sections) and "統合許可" in instructions
        case "統合準備完了":
            waiting = head in declaration.result_heads and "統合許可" in instructions
        case "続行できない区分: user_decision" | "続行できない区分: owner_input" | "続行できない区分: authorization":
            waiting = len(lines) > 1 and lines[1].startswith("続行できない理由: ")
        case _:
            waiting = False
    return result_projection.elapsed_seconds(info.session_updated_at) if waiting else None


def continuation_notices(
    host_session_id: str | None, targets: set[str], state_root: pathlib.Path | None
) -> list[dict[str, Any]]:
    """回収済みの定型返却の応答待ちを、再配送せずに返す。"""
    if host_session_id is None:
        return []
    tracked = session_state.read_state(host_session_id).get("agents_server_sessions")
    if not isinstance(tracked, dict):
        return []
    return [
        {"session_id": identifier, "status": "awaiting_reply", "next_action": continuation_action(seconds)}
        for identifier, record in tracked.items()
        if isinstance(identifier, str)
        and shared_layout.valid_session_id(identifier)
        and identifier not in targets
        and isinstance(record, dict)
        and record.get("owner_agent_id") == "main"
        and record.get("pending_observation") is False
        and not record.get("kill_requested")
        and (seconds := continuation_wait_seconds(identifier, state_root=state_root)) is not None
    ]


def continuation_action(seconds: int) -> str:
    return f"応答待ち（返却から {seconds} 秒）。役割の条件に従い `send_message` で返答する"
