"""Codex向け品質確認通知のhandler。"""

from __future__ import annotations

import json
import pathlib
import sys
from typing import Any

_SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from _hook_notice import formatter as _notice_formatter  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_HOOK_ID = "agent-toolkit/quality_checkpoint"
_llm_notice = _notice_formatter(_HOOK_ID)
_KNOWN_SOURCES = frozenset({"compact", "startup", "resume", "clear"})
_KNOWN_PERMISSION_MODES = frozenset({"default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"})
_SESSION_START_FIELDS = frozenset(
    {"session_id", "transcript_path", "cwd", "hook_event_name", "model", "permission_mode", "source"}
)
_REQUIRED_STRING_FIELDS = (
    "session_id",
    "cwd",
    "hook_event_name",
    "model",
    "permission_mode",
    "source",
)

QUALITY_CHECKPOINT_NOTICE = (
    "Keep the original user-visible purpose explicit. Prefer the minimum design that is "
    "sufficient for the required scenario. Separate agent-facing conversation guidance "
    "from durable artifact context, and keep conversation-only directives out of artifacts. "
    "Fail explicitly when requirements are not met. Remove unsupported fallback, legacy, "
    "and compatibility paths instead of preserving them without evidence. Retrieve only "
    "the information needed for the decision. Do not repeat searches or checks that cannot "
    "change the next decision. Treat AGENTS.md and the agent-toolkit rules as the source of truth."
)


def _parse_payload(payload_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise ValueError("フック入力JSONの解析に失敗した") from exc
    if not isinstance(payload, dict):
        raise ValueError("フック入力はJSON objectである必要がある")
    return payload


def _validate_session_start_payload(payload: dict[str, Any]) -> None:
    if payload.keys() != _SESSION_START_FIELDS:
        raise ValueError("フック入力はCodex SessionStartの既知フィールドだけを含む必要がある")
    for field in _REQUIRED_STRING_FIELDS:
        if not isinstance(payload.get(field), str):
            raise ValueError(f"{field}は文字列である必要がある")
    if "transcript_path" not in payload or not (
        payload["transcript_path"] is None or isinstance(payload["transcript_path"], str)
    ):
        raise ValueError("transcript_pathは文字列またはnullである必要がある")
    if payload["permission_mode"] not in _KNOWN_PERMISSION_MODES:
        raise ValueError("permission_modeは既知のCodex値である必要がある")


def main(payload_text: str) -> int:
    """1件のSessionStart payloadを処理する。"""
    payload = _parse_payload(payload_text)
    _validate_session_start_payload(payload)
    if payload.get("hook_event_name") != "SessionStart":
        raise ValueError("hook_event_nameはSessionStartである必要がある")
    source = payload.get("source")
    if not isinstance(source, str) or source not in _KNOWN_SOURCES:
        raise ValueError("sourceは既知のSessionStart値である必要がある")

    if source == "compact":
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "SessionStart",
                        "additionalContext": _llm_notice(QUALITY_CHECKPOINT_NOTICE),
                    }
                },
                ensure_ascii=False,
            )
        )
    return 0
