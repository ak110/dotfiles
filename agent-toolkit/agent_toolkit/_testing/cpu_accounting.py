"""CPU予算から、初回importと共有heapの旧世代自動GCの区間和を除く。

process CPUの原計測は呼出側が所有する。主測定threadで観測した初期化費用だけを
控除し、DUTのthread、若い世代のGC、明示的なcollectの仕事は原計測へ残す。
"""

import dataclasses
import gc
import sys
import threading
import time
from collections.abc import Callable
from importlib import _bootstrap
from typing import Any

# CPythonの遅延importの計時点は型stubへ公開されない。
_IMPORT_NAMESPACE: Any = vars(_bootstrap)
_thread_cpu = time.thread_time


@dataclasses.dataclass
class _Observer:
    """同じ主threadで観測するroot sessionと重複区間の状態。"""

    owner: object | None = None
    thread: int | None = None
    import_depth: int = 0
    manual_depth: int = 0
    gc_started: float | None = None
    gc_work_started: float | None = None
    gc_cpu: float = 0.0
    gc_work_cpu: float = 0.0
    import_cpu: float = 0.0
    other_gc_started: dict[int, float] = dataclasses.field(default_factory=dict)
    other_gc_cpu: float = 0.0
    original_import: Callable[..., Any] = _IMPORT_NAMESPACE["_find_and_load"]
    original_collect: Callable[..., int] = gc.collect


_state = _Observer()


def _gc_event(phase: str, info: dict[str, int]) -> None:
    # 世代0と明示collectは試験自身の仕事。旧世代の自動走査には先行試験のheapが含まれる。
    thread = threading.get_ident()
    young = info.get("generation", 0) == 0
    if thread != _state.thread:
        # 他threadの旧世代GCは帰属診断だけに使い、明示collectも含む原計測を控除しない。
        if not young:
            if phase == "start":
                _state.other_gc_started[thread] = _thread_cpu()
            elif (started := _state.other_gc_started.pop(thread, None)) is not None:
                _state.other_gc_cpu += _thread_cpu() - started
        return
    if _state.manual_depth:
        return
    if young:
        if phase == "start":
            _state.gc_work_started = _thread_cpu()
        elif _state.gc_work_started is not None:
            _state.gc_work_cpu += _thread_cpu() - _state.gc_work_started
            _state.gc_work_started = None
        return
    if phase == "start":
        _state.gc_started = _thread_cpu()
    elif _state.gc_started is not None:
        _state.gc_cpu += _thread_cpu() - _state.gc_started
        _state.gc_started = None


def _collect(generation: int = 2) -> int:
    if threading.get_ident() != _state.thread:
        return _state.original_collect(generation)
    outer = _state.manual_depth == 0
    started = _thread_cpu()
    _state.manual_depth += 1
    try:
        return _state.original_collect(generation)
    finally:
        _state.manual_depth -= 1
        if outer:
            _state.gc_work_cpu += _thread_cpu() - started


def _import(name: str, *args: Any, **kwargs: Any) -> Any:
    if threading.get_ident() != _state.thread:
        return _state.original_import(name, *args, **kwargs)
    initial = _state.import_depth == 0 and _state.manual_depth == 0 and name not in sys.modules
    _state.import_depth += 1
    started, gc_before = _thread_cpu(), _state.gc_cpu + _state.gc_work_cpu
    try:
        return _state.original_import(name, *args, **kwargs)
    finally:
        _state.import_depth -= 1
        if initial and sys.modules.get(name) is not None:
            # 全GCをimport費用から分離し、DUTのGCがimport控除へ混入することを防ぐ。
            _state.import_cpu += max(0.0, _thread_cpu() - started - (_state.gc_cpu + _state.gc_work_cpu - gc_before))


def start(owner: object) -> None:
    """最初のpytest sessionが観測を所有し、nested sessionでは重ねない。"""
    if _state.owner is not None:
        return
    _state.owner, _state.thread = owner, threading.get_ident()
    _state.gc_cpu = _state.gc_work_cpu = _state.import_cpu = _state.other_gc_cpu = 0.0
    _state.gc_started = _state.gc_work_started = None
    _state.other_gc_started.clear()
    _state.original_import = _IMPORT_NAMESPACE["_find_and_load"]
    _state.original_collect = gc.collect
    _IMPORT_NAMESPACE["_find_and_load"] = _import
    # 組込みcollectorを同じ公開signatureで一時的に計時する。
    gc.collect = _collect  # ty: ignore[invalid-assignment]
    gc.callbacks.append(_gc_event)


def stop(owner: object) -> None:
    """所有sessionの終了時に、所有する観測だけを元へ戻す。"""
    if _state.owner is not owner:
        return
    if _IMPORT_NAMESPACE["_find_and_load"] is _import:
        _IMPORT_NAMESPACE["_find_and_load"] = _state.original_import
    if gc.collect is _collect:
        gc.collect = _state.original_collect
    if _gc_event in gc.callbacks:
        gc.callbacks.remove(_gc_event)
    _state.other_gc_started.clear()
    _state.owner, _state.thread = None, None


def snapshot() -> dict[str, float]:
    """段階前後の差を得るため、累計CPU秒数を返す。"""
    return {"gc": _state.gc_cpu, "import": _state.import_cpu, "other_gc": _state.other_gc_cpu}
