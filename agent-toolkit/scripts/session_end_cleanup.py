"""SessionEnd hook: 親セッションの共有状態JSONを破棄する。"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _session_state import delete_state  # noqa: E402  # pylint: disable=wrong-import-position,import-error


def main(payload_text: str) -> int:
    """対象状態を削除し、失敗時もSessionEndを通過させる。"""
    try:
        payload = json.loads(payload_text or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SessionEnd":
        return 0
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return 0
    if not delete_state(session_id):
        print(
            f"[session_end_cleanup] セッション状態を削除できませんでした: session_id={session_id}",
            file=sys.stderr,
        )
    return 0
