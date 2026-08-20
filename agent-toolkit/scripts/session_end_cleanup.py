"""SessionEnd hook: 期限切れの共有状態JSONを回収する。

セッション終了イベントは、同じ`session_id`が後から再び使われる場合にも発火する。
`--continue`・`--resume`・`/resume`で戻ると同じ`session_id`で会話が続くため、
当該イベントを契機に状態を削除すると再開後の記録が失われる。
そのため通常は削除せず、更新から一定期間が経過した他セッションの状態だけを回収する。
計画名の再出力抑止記録は通常状態と別の保存先で保持し、期限回収の対象に含めない。
当該セッション自身の状態は、記録が無いまま期間が経過した場合も回収対象から除く。

例外は終了理由が`clear`の場合とする。この理由は会話が破棄されたことを示し、
同じ会話へ戻る経路ではないため、状態を保持する意味が無い。
`session_id`が再利用された場合には、残った記録が誤った判定材料になる。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    clear_session_state,
    sweep_stale_states,
)

_DISCARDED_REASON = "clear"


def main(payload_text: str) -> int:
    """期限切れの状態を回収し、会話破棄時だけ自セッションの状態も削除する。

    失敗時もSessionEndを通過させる。
    """
    try:
        payload = json.loads(payload_text or "{}")
    except (json.JSONDecodeError, ValueError):
        return 0
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SessionEnd":
        return 0
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    sweep_stale_states(keep_session_id=session_id)
    if payload.get("reason") != _DISCARDED_REASON or session_id is None:
        return 0
    if not clear_session_state(session_id):
        print(
            f"[session_end_cleanup] セッション状態を削除できませんでした: session_id={session_id}",
            file=sys.stderr,
        )
    return 0
