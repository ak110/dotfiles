"""実行主体ごとの規範を起動時の文脈へ追加するhandler。

Codex backendの子はstatusline表示の判定のため
``AGENT_TOOLKIT_DELEGATED_SESSION``を持たないので、委譲先の判定には
``AGENT_TOOLKIT_OWNER_SESSION``も用いる。

Claude Codeはhook 1件の出力を10,000文字で切り詰める。条文の欠落を防ぐため、
最大構成を同じ上限へ収める契約テストを置く。
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Mapping
from typing import Any

from _hook_notice import formatter as _notice_formatter

_HOOK_ID = "agent-toolkit/rules_context"
_llm_notice = _notice_formatter(_HOOK_ID)

QUALITY_CHECKPOINT_NOTICE = (
    "ユーザーが観測する本来の目的を明示する。必要な利用場面を満たす最小の設計を選ぶ。"
    "エージェント向けの会話上の案内と永続的な成果物の文脈を分離し、会話だけに適用する指示を成果物へ混入させない。"
    "要件を満たさない場合は明示的に失敗させる。根拠のないフォールバック、旧経路及び互換経路は温存せず撤去する。"
    "`AGENTS.md`とagent-toolkitの規範を正本として扱う。"
)

SHARE_DIR = pathlib.Path(__file__).resolve().parent.parent / "share"
MAIN_RULES_PATH = SHARE_DIR / "rules-main.md"
MAIN_RULES_CLAUDE_CODE_PATH = SHARE_DIR / "rules-main.claude-code.md"
SUBAGENT_RULES_PATH = SHARE_DIR / "rules-subagent.md"
CLAUDE_CODE_OUTPUT_LIMIT = 10_000


def is_delegated(environ: Mapping[str, str]) -> bool:
    """環境変数からagents_serverの委譲先かを判定する。"""
    return environ.get("AGENT_TOOLKIT_DELEGATED_SESSION") == "1" or bool(environ.get("AGENT_TOOLKIT_OWNER_SESSION"))


def compose_session_start(source: str, *, delegated: bool, host: str) -> str | None:
    """SessionStartへ追加する本文を構成する。"""
    parts: list[str] = []
    if source == "compact":
        parts.append(QUALITY_CHECKPOINT_NOTICE)
    if not delegated:
        parts.append(MAIN_RULES_PATH.read_text(encoding="utf-8").rstrip("\n"))
        if host == "claude":
            parts.append(MAIN_RULES_CLAUDE_CODE_PATH.read_text(encoding="utf-8").rstrip("\n"))
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
