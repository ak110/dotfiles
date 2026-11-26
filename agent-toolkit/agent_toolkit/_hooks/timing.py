"""hookの所要を、入力本文を含めずセッションの一時領域へ記録する。"""

import json
import time
from collections.abc import Generator, Iterable
from contextlib import contextmanager

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.file_lock import acquire_lock, release_lock

_MAX_BYTES = 64 * 1024


def record(payload: object, operation: str, started: float, status: str, *, stage: str = "total") -> None:
    record_stages(payload, operation, [(stage, time.monotonic() - started, status)])


class Stages:
    def __init__(self) -> None:
        self.rows: list[tuple[str, float, str]] = []

    @contextmanager
    def measure(self, stage: str) -> Generator[None]:
        started = time.monotonic()
        status = "error"
        try:
            yield
            status = "ok"
        finally:
            self.rows.append((stage, time.monotonic() - started, status))


def record_stages(payload: object, operation: str, rows: Iterable[tuple[str, float, str]]) -> None:
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str):
        return
    try:
        directory = managed_temp.find_session_temp(payload["session_id"])
    except OSError, managed_temp.ManagedTempError:
        return
    if directory is None:
        return
    data = b"".join(
        json.dumps(
            {
                "event": payload.get("hook_event_name", ""),
                "operation": operation,
                "stage": stage,
                "seconds": seconds,
                "status": status,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        + b"\n"
        for stage, seconds, status in rows
    )
    if not data:
        return

    try:
        with (directory / "hook-timings.lock").open("a+b") as lock:
            acquire_lock(lock)
            try:
                with (directory / "hook-timings.jsonl").open("a+b") as output:
                    output.seek(0, 2)
                    if output.tell() + len(data) > _MAX_BYTES:
                        output.seek(-min(output.tell(), _MAX_BYTES // 2), 2)
                        retained = output.read().split(b"\n", 1)[-1]
                        output.seek(0)
                        output.truncate()
                        output.write(retained)
                    output.write(data)
            finally:
                release_lock(lock)
    except OSError:
        return
