"""Claude Code plugin agent-toolkit: StopFailure hook。

ターンがAPIエラーで終了したときに発火する観測専用イベント`StopFailure`のフック。
出力と終了コードは無視されるため、通知やブロックはできない。
メインのrate_limitとserver_errorは、Function hooksのturn.completeへ1回限りの再開予約要求を渡す。
pyfltr: allow-comment
"""

import json
lazy import os
lazy import re

lazy from agent_toolkit._common import delegated_session, host_homes, process_loop_session


def main(payload_text: str) -> int:
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError, ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0
    session_id = payload.get("session_id")
    error = payload.get("error")
    if (
        error in ("rate_limit", "server_error")
        and not payload.get("agent_id")
        and not delegated_session.is_delegated(os.environ)
        and isinstance(session_id, str)
        and re.fullmatch(r"[A-Za-z0-9_-]+", session_id)
    ):
        prompt_id = payload.get("prompt_id")
        if process_loop_session.is_identified_process_loop_session(session_id, os.environ):
            if error != "rate_limit":
                return 0
            name = f"rate-limit-{session_id}.txt"
        elif isinstance(prompt_id, str) and re.fullmatch(r"[A-Za-z0-9_-]+", prompt_id):
            name = f"{error.replace('_', '-')}-{session_id}.{prompt_id}.txt"
        else:
            return 0
        request = host_homes.claude_config_dir() / "agent-toolkit-function-hooks" / name
        request.parent.mkdir(parents=True, exist_ok=True)
        request.write_text("requested", encoding="utf-8")
    return 0
