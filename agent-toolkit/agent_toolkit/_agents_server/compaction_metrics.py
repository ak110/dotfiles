"""Codexコンパクションの所要時間を振り返り用JSONLへ記録する。

記録先は``<state_dir>/agents-server/compaction/<thread_id>.jsonl``とし、
同じ``item_id``はファイルロックの排他範囲で1回だけ追記する。1行は``version``・
``thread_id``・``item_id``・``started_at_ms``・``completed_at_ms``・
``duration_seconds``を持つJSONオブジェクトである。所要秒数はミリ秒差へ
``round((completed_at_ms - started_at_ms) / 1000, 1)``を適用して算出する。
"""

from __future__ import annotations

import json
from pathlib import Path

from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import file_lock


def record_directory() -> Path:
    """コンパクション計測記録の既定ディレクトリを返す。"""
    return _atk_config.state_dir() / "agents-server" / "compaction"


def append_compaction_record(
    thread_id: str,
    item_id: str,
    started_at_ms: int,
    completed_at_ms: int,
) -> None:
    """コンパクション1回分を、同じ``item_id``と重複しないよう追記する。"""
    path = record_directory() / f"{thread_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        file_lock.acquire_lock(lock_file)
        try:
            if path.exists():
                for line in path.read_text(encoding="utf-8").splitlines():
                    try:
                        existing = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(existing, dict) and existing.get("item_id") == item_id:
                        return
            record = {
                "version": 1,
                "thread_id": thread_id,
                "item_id": item_id,
                "started_at_ms": started_at_ms,
                "completed_at_ms": completed_at_ms,
                "duration_seconds": round((completed_at_ms - started_at_ms) / 1000, 1),
            }
            with path.open("a", encoding="utf-8") as stream:
                stream.write(f"{json.dumps(record, ensure_ascii=False)}\n")
        finally:
            file_lock.release_lock(lock_file)
