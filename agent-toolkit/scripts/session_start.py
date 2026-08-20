"""Claude Code SessionStartで旧Codex User scope MCPの残存を診断する。"""

from __future__ import annotations

import json
import pathlib

from _message_format import llm_notice

_CLAUDE_CONFIG_PATH = pathlib.Path.home() / ".claude.json"
_CODEX_NAME = "codex"
_LEGACY_TIMEOUT = 7_200_000
_HOOK_ID = "agent-toolkit/session-start"


def _load_user_codex(path: pathlib.Path = _CLAUDE_CONFIG_PATH) -> dict[str, object] | None:
    """Claude CodeのUser scope設定からcodex serverを読み取り専用で取得する。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers")
    if not isinstance(servers, dict):
        return None
    value = servers.get(_CODEX_NAME)
    return value if isinstance(value, dict) else None


def _is_legacy_definition(value: object) -> bool:
    """過去の`codex mcp-server`登録に一致するかを返す。"""
    if not isinstance(value, dict):
        return False
    timeout = value.get("timeout")
    return (
        value.get("type") in (None, "stdio")
        and value.get("command") == "codex"
        and value.get("args") == ["mcp-server"]
        and (timeout is None or timeout == _LEGACY_TIMEOUT)
    )


def _diagnostic(_value: dict[str, object]) -> str:
    """旧登録を変更せずに確認・削除する手順を返す。"""
    return llm_notice(
        "旧User scope MCPサーバー`codex`を検出した。"
        "新しい`codex_app_server`はagent-toolkit pluginから提供される。"
        "旧定義は`claude mcp get codex`で確認し、不要であれば"
        "`claude mcp remove --scope user codex`を明示的に実行して削除すること。"
        "この診断は`~/.claude.json`を読み取るだけで、変更しない。",
        _HOOK_ID,
        tag="notice",
    )


def main(payload_text: str) -> int:
    """SessionStart payloadを読み、旧登録があれば追加コンテキストを返す。"""
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    value = _load_user_codex()
    if value is None or not _is_legacy_definition(value):
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": _diagnostic(value),
                }
            },
            ensure_ascii=False,
        )
    )
    return 0
