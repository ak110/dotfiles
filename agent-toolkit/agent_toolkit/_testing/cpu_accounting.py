"""CPU予算から、初回importと共有heapの旧世代自動GCの区間和を除く。

process CPUの原計測は呼出側が所有する。主測定threadの初回importと、各threadの
共有旧世代GCを控除し、DUTの仕事、若い世代のGC、明示的なcollectは原計測へ残す。
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
class _ThreadGc:
    """同じthreadの時計で対応付けるGCと明示collectの区間。"""

    manual_depth: int = 0
    gc_started: float | None = None
    gc_work_started: float | None = None
    gc_cpu: float = 0.0
    gc_work_cpu: float = 0.0


@dataclasses.dataclass
class _Observer:
    """root sessionの合算と主threadの初回importを所有する。"""

    owner: object | None = None
    thread: int | None = None
    import_depth: int = 0
    gc_cpu: float = 0.0
    import_cpu: float = 0.0
    local: threading.local = dataclasses.field(default_factory=threading.local)
    original_import: Callable[..., Any] = _IMPORT_NAMESPACE["_find_and_load"]
    original_collect: Callable[..., int] = gc.collect


_state = _Observer()


def _thread_gc() -> _ThreadGc:
    observed = getattr(_state.local, "gc", None)
    if not isinstance(observed, _ThreadGc):
        observed = _ThreadGc()
        _state.local.gc = observed
    return observed


def _gc_event(phase: str, info: dict[str, int]) -> None:
    # 世代0と明示collectは試験自身の仕事。旧世代の自動走査には先行試験のheapが含まれる。
    young = info.get("generation", 0) == 0
    observed = _thread_gc()
    if observed.manual_depth:
        return
    if young:
        if phase == "start":
            observed.gc_work_started = _thread_cpu()
        elif observed.gc_work_started is not None:
            observed.gc_work_cpu += _thread_cpu() - observed.gc_work_started
            observed.gc_work_started = None
        return
    if phase == "start":
        observed.gc_started = _thread_cpu()
    elif observed.gc_started is not None:
        elapsed = _thread_cpu() - observed.gc_started
        observed.gc_cpu += elapsed
        _state.gc_cpu += elapsed
        observed.gc_started = None


def _collect(generation: int = 2) -> int:
    observed = _thread_gc()
    outer = observed.manual_depth == 0
    started = _thread_cpu()
    observed.manual_depth += 1
    try:
        return _state.original_collect(generation)
    finally:
        observed.manual_depth -= 1
        if outer:
            observed.gc_work_cpu += _thread_cpu() - started


def _import(name: str, *args: Any, **kwargs: Any) -> Any:
    if threading.get_ident() != _state.thread:
        return _state.original_import(name, *args, **kwargs)
    observed = _thread_gc()
    initial = _state.import_depth == 0 and observed.manual_depth == 0 and name not in sys.modules
    _state.import_depth += 1
    started, gc_before = _thread_cpu(), observed.gc_cpu + observed.gc_work_cpu
    try:
        return _state.original_import(name, *args, **kwargs)
    finally:
        _state.import_depth -= 1
        if initial and sys.modules.get(name) is not None:
            # 同じthreadのGCだけを引く。他threadのGCを引くとimportの控除量を失う。
            _state.import_cpu += max(0.0, _thread_cpu() - started - (observed.gc_cpu + observed.gc_work_cpu - gc_before))


def start(owner: object) -> None:
    """最初のpytest sessionが観測を所有し、nested sessionでは重ねない。"""
    if _state.owner is not None:
        return
    _state.owner, _state.thread = owner, threading.get_ident()
    _state.gc_cpu = _state.import_cpu = 0.0
    _state.import_depth = 0
    # 終了したthreadの状態はlocalが回収し、セッションをまたいで保持しない。
    _state.local = threading.local()
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
    _state.owner, _state.thread = None, None
    _state.local = threading.local()


def snapshot() -> dict[str, float]:
    """段階前後の差を得るため、累計CPU秒数を返す。"""
    return {"gc": _state.gc_cpu, "import": _state.import_cpu}
