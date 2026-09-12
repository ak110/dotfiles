"""実行主体ごとの規範を起動時の文脈へ追加するhandler。

委譲先の判定は`_common.delegated_session`を正本とする。

Claude Codeはhook 1件の出力を10,000文字で切り詰める。条文の欠落を防ぐため、
最大構成を同じ上限へ収める契約テストを置く。
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._hooks.notice import formatter as _notice_formatter

_HOOK_ID = "agent-toolkit/rules_context"
_llm_notice = _notice_formatter(_HOOK_ID)

QUALITY_CHECKPOINT_NOTICE = (
    "目的・利用場面を明示し、最小設計を選ぶ。会話限定指示を成果物へ混入させない。"
    "要件未達と無根拠な代替・旧・互換経路を拒み、規範を正本とする。"
)

SHARE_DIR = pathlib.Path(__file__).resolve().parents[2] / "share"
MAIN_RULES_PATH = SHARE_DIR / "rules-main.md"
MAIN_RULES_CLAUDE_CODE_PATH = SHARE_DIR / "rules-main.claude-code.md"
SUBAGENT_RULES_PATH = SHARE_DIR / "rules-subagent.md"
CLAUDE_CODE_OUTPUT_LIMIT = 10_000
SESSION_TEMP_PREFIX = "session"


def compose_session_start(source: str, *, delegated: bool, host: str) -> str | None:
    """SessionStartへ追加する本文を構成する。"""
    parts: list[str] = []
    if source == "compact":
        parts.append(QUALITY_CHECKPOINT_NOTICE)
    normative_parts: list[str] = []
    if not delegated:
        normative_parts.append(MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip("\n"))
        if host == "claude":
            normative_parts.append(MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip("\n"))
    if normative_parts:
        normative_body = "\n\n".join(normative_parts)
        parts.append(f'<normative-context source="agent-toolkit">\n{normative_body}\n</normative-context>')
    return "\n\n".join(parts) or None


def compose_subagent_start() -> str:
    """SubagentStartへ追加する本文を返す。"""
    return SUBAGENT_RULES_PATH.read_text(encoding="utf-8").rstrip("\n")


def _parse_payload(payload_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError("フック入力JSONの解析に失敗した") from exc
    if not isinstance(payload, dict):
        raise ValueError("フック入力はJSON objectである必要がある")
    return payload


def main(payload_text: str, *, host: str = "claude") -> int:
    """SessionStart又はSubagentStartのpayloadを処理する。"""
    payload = _parse_payload(payload_text)
    event_name = payload.get("hook_event_name")
    if event_name == "SessionStart":
        source = payload.get("source")
        if not isinstance(source, str):
            raise ValueError("sourceは文字列である必要がある")
        content = compose_session_start(source, delegated=is_delegated(os.environ), host=host)
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            try:
                session_temp = managed_temp.create_managed_temp(
                    SESSION_TEMP_PREFIX,
                    session_id=session_id,
                )
            except (managed_temp.ManagedTempError, OSError):
                pass
            else:
                temp_context = f"このセッションの管理対象一時領域: {session_temp}"
                content = f"{content}\n\n{temp_context}" if content else temp_context
    elif event_name == "SubagentStart":
        content = compose_subagent_start()
    else:
        raise ValueError("hook_event_nameはSessionStart又はSubagentStartである必要がある")

    if content is not None:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event_name,
                        "additionalContext": _llm_notice(content),
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0
