"""SessionEnd hook: 期限切れの共有状態JSONを回収する。

セッション終了イベントは、同じ`session_id`が後から再び使われる場合にも発火する。
`--continue`・`--resume`・`/resume`で戻ると同じ`session_id`で会話が続くため、
このイベントを契機に状態を削除すると再開後の記録が失われる。
そのため通常は削除せず、更新から一定期間が経過した他セッションの状態だけを回収する。
命名の種別と前回設定名も通常状態と同じ寿命で保持する。
自セッション自身の状態は、記録が無いまま期間が経過した場合も回収対象から除く。
期限による回収は1日1回に限る。SessionEndは委譲先のturnごとにも発火し、回収1回が登録簿や記録の全件を読むためである。

例外は終了理由が`clear`の場合とする。この理由は会話が破棄されたことを示し、
この終了の後に同じ会話へ戻ることはないため、状態を保持する意味が無い。
`session_id`が再利用された場合には、残った記録が誤った判定材料になる。

SessionStartが作成するセッションのmanaged-tempも、同じ理由でSessionEndでは削除しない。
`agents_server`経由のsessionはturnごとにSessionEndが発火し、後続のturnと委譲先が同じ領域の成果物を読む。
記録と領域自体は7日の掃引へ委ね、消費終了を確認できた再生成環境だけを先に回収する。
未回収の結果・後続担当・生存する所有者がある領域は保持し、中断時も次の日次掃引で再判定する。

本処理は`agent-toolkit/hooks/hooks.json`で非同期（`"async": true`）に登録する。
監査記録は`docs/development/audit-records-hooks-lifecycle.md`の「agent-toolkit/hooks/hooks.json：SessionEndの非同期化：2026年9月24日」にある。
"""

from __future__ import annotations

import contextlib
import functools
import json
import os
import time

lazy from agent_toolkit._agents_server import shared_roots, shared_sweep
lazy from agent_toolkit._atk.agents_exit_session import sweep_function_hook_files
lazy from agent_toolkit._atk.managed_temp import creation, resources, sweep
lazy from agent_toolkit._common import interval_gate, registry_index, session_activity, state_paths
lazy from agent_toolkit._common.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    STALE_STATE_MAX_AGE_SECONDS,
    clear_session_state,
    sweep_stale_states,
)
lazy from agent_toolkit._hooks.background_tasks_entries import sweep_nested_launch_caches

_DISCARDED_REASON = "clear"

_EXPIRY_SWEEP_INTERVAL_SECONDS = 24 * 60 * 60
"""期限切れ状態の回収の間隔。保持期限（共有状態7日、hook状態14日）に対し、1日遅れても回収の結果は変わらない。"""


def main(payload_text: str) -> int:
    """期限切れ状態を回収し、会話破棄時だけ自セッション状態も削除する。

    失敗時もSessionEndを通過させる。
    """
    try:
        payload = json.loads(payload_text or "{}")
    except json.JSONDecodeError, ValueError:
        return 0
    if not isinstance(payload, dict) or payload.get("hook_event_name") != "SessionEnd":
        return 0
    from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    run_skill_batch.hook_request(payload, host="codex" if os.environ.get("CODEX_THREAD_ID") else "claude")
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        session_id = None
    discarded = payload.get("reason") == _DISCARDED_REASON
    # SessionEndはモデルへの配送先を持たない。失敗した回収は次回の期限掃引で再試行する。
    if interval_gate.claim(state_paths.state_dir() / "sweeps" / "session-end", interval_seconds=_EXPIRY_SWEEP_INTERVAL_SECONDS):
        keep_root_session_id = (
            shared_roots.resolve_conversation_root_session_id({"CLAUDE_CODE_SESSION_ID": session_id})
            if session_id is not None
            else None
        )
        with contextlib.suppress(OSError):
            shared_sweep.sweep_stale_shared_state(keep_root_session_id=keep_root_session_id)
        with contextlib.suppress(OSError):
            sweep_function_hook_files(keep_session_id=session_id, clear=discarded)
        sweep_stale_states(keep_session_id=session_id)
        with contextlib.suppress(OSError):
            session_activity.sweep_exited_records()
        sweep_nested_launch_caches(max_age_seconds=STALE_STATE_MAX_AGE_SECONDS, now=time.time())
    elif discarded:
        with contextlib.suppress(OSError):
            sweep_function_hook_files(keep_session_id=session_id, clear=True)
    with contextlib.suppress(OSError, ValueError):
        area = creation.find_session_temp(session_id) if session_id is not None else None

        if area is not None and not sweep.has_live_owner(area) and session_id not in registry_index.pending_session_ids():
            cleaned = resources.cleanup_regenerable(area, references=functools.cache(resources.process_references))
            resources.log_retained(cleaned["retained"])
    if not discarded or session_id is None:
        return 0
    clear_session_state(session_id)
    return 0
