"""残った公開start試験について、workerでのGCを一時観測する。合否には使わない。"""

import contextlib
import gc
import inspect
import json
import pathlib
import threading
import time
from collections.abc import Generator
from typing import Any

import pytest


@contextlib.contextmanager
def observe_call(item: pytest.Item) -> Generator[None]:
    """1つの試験の全threadの世代別GCを集約し、CPU超過時だけ既存の報告へ残す。"""
    active: dict[tuple[int, int], tuple[float, bool, list[str]]] = {}
    totals: dict[tuple[int, int, bool], dict[str, Any]] = {}

    def observe_gc(phase: str, info: dict[str, int]) -> None:
        now = time.thread_time()
        identity, generation = threading.get_ident(), info.get("generation", 0)
        key = identity, generation
        if phase == "start":
            frame = inspect.currentframe()
            stack = []
            collect_wrapper = False
            for _ in range(8):
                if frame is None:
                    break
                filename = pathlib.Path(frame.f_code.co_filename).name
                stack.append(f"{filename}:{frame.f_code.co_name}:{frame.f_lineno}")
                collect_wrapper |= filename == "cpu_accounting.py" and frame.f_code.co_name == "_collect"
                frame = frame.f_back
            del frame
            active[key] = now, collect_wrapper, stack
        elif phase == "stop" and key in active:
            started, collect_wrapper, stack = active.pop(key)
            elapsed = now - started
            row = totals.setdefault(
                (identity, generation, collect_wrapper),
                {
                    "thread": threading.current_thread().name,
                    "generation": generation,
                    "collect_wrapper": collect_wrapper,
                    "cpu_seconds": 0.0,
                    "count": 0,
                    "max_cpu_seconds": 0.0,
                    "max_stack": [],
                },
            )
            row["cpu_seconds"] += elapsed
            row["count"] += 1
            if elapsed > row["max_cpu_seconds"]:
                row["max_cpu_seconds"], row["max_stack"] = elapsed, stack

    process_started, main_started = time.process_time(), time.thread_time()
    gc.callbacks.append(observe_gc)
    try:
        yield
    finally:
        if observe_gc in gc.callbacks:
            gc.callbacks.remove(observe_gc)
        process_cpu = time.process_time() - process_started
        main_cpu = time.thread_time() - main_started
        if process_cpu >= 0.5:
            item.add_report_section(
                "call",
                "CPU帰属（判定非使用）",
                json.dumps({"process_cpu": process_cpu, "main_thread_cpu": main_cpu, "gc": list(totals.values())}),
            )
