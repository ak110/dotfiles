"""終端結果の値だけから、引き継ぎ起動の次操作を組み立てる。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import codex_resume_state, engine_availability
from agent_toolkit._agents_server.state import SessionState


def _availability_failure(engine: str, status: str, error: Any) -> bool:
    """backendと共通の可用性判定から、結果に引き継ぎ案内を付ける対象を求める。"""
    session = SessionState(session_id="handoff", cwd="", engine=engine, status=status, error=error)
    if engine in {"agy", "opencode"}:
        return engine_availability.failure_error(session) is not None
    if engine not in {"claude", "codex"}:
        return False

    def model_rejected(value: Mapping[str, Any]) -> str | None:
        if engine == "codex" and engine_availability.rejected_parameter(value) == "model":
            return engine_availability.ENGINE_MODEL_REJECTED_REASON
        return None

    return engine_availability.unavailable_reason(session, other_reason=model_rejected) is not None


def result_next_action(result: Mapping[str, Any]) -> str | None:
    """可用性失敗とCodexの状態互換性・接続失敗へ、前任IDを使った引き継ぎ起動を案内する。"""
    engine = str(result.get("engine", ""))
    status = str(result.get("status", ""))
    error = result.get("error")
    if not _availability_failure(engine, status, error) and not (
        engine == "codex" and codex_resume_state.handoff_failure(status, error)
    ):
        return None
    identifier = json.dumps(result.get("session_id"), ensure_ascii=False)
    return f'`start`へ`{{"handoff_from": {identifier}}}`だけを渡し、前任の進捗を後任へ引き継ぐ'
