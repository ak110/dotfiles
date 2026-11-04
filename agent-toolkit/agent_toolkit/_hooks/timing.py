"""hookの所要を、入力本文を含めずセッションの一時領域へ記録する。"""

import json
import time

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.file_lock import acquire_lock, release_lock

_MAX_BYTES = 64 * 1024


def record(payload: object, operation: str, started: float, status: str, *, stage: str = "total") -> None:
    """同じセッションの並行hookを直列化し、直近の有限な計測だけを保持する。"""
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
        return
    directory = managed_temp.find_session_temp(payload["session_id"])
    if directory is None:
        return
    row = (
        json.dumps(
            {
                "event": payload.get("hook_event_name", ""),
                "operation": operation,
                "stage": stage,
                "seconds": time.monotonic() - started,
                "status": status,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
    )
    # 計測の書込障害はhookの本来の結果を変えない。本文や例外メッセージは保存しない。
    try:
        with (directory / "hook-timings.lock").open("a+b") as lock:
            acquire_lock(lock)
            try:
                with (directory / "hook-timings.jsonl").open("a+b") as output:
                    output.seek(0, 2)
                    if output.tell() + len(row) > _MAX_BYTES:
                        output.seek(-min(output.tell(), _MAX_BYTES // 2), 2)
                        retained = output.read().split(b"\n", 1)[-1]
                        output.seek(0)
                        output.truncate()
                        output.write(retained)
                    output.write(row)
            finally:
                release_lock(lock)
    except OSError:
        return
