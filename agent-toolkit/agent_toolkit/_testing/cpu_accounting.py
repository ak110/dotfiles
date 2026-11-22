import concurrent.futures
import contextlib
import contextvars
import dataclasses
import functools
import gc
import sys
import threading
import time
import unittest.mock
from collections.abc import Callable, Generator
from importlib import _bootstrap
from typing import Any

import psutil

_IMPORT_NAMESPACE: Any = vars(_bootstrap)
_thread_cpu = time.thread_time
_process_threads = psutil.Process().threads


@dataclasses.dataclass
class _ThreadGc:
    manual_depth: int = 0
    gc_started: float | None = None
    gc_work_started: float | None = None
    gc_cpu: float = 0.0
    gc_work_cpu: float = 0.0
    import_cpu: float = 0.0


@dataclasses.dataclass
class CpuBudget:
    completed: dict[str, float] = dataclasses.field(default_factory=lambda: {"worker": 0.0, "gc": 0.0, "import": 0.0})
    active: dict[int, tuple[float, _ThreadGc, float, float]] = dataclasses.field(default_factory=dict)
    lock: Any = dataclasses.field(default_factory=threading.RLock)


_budget: contextvars.ContextVar[CpuBudget | None] = contextvars.ContextVar("pytest_cpu_budget", default=None)
_pool_start: contextvars.ContextVar[bool] = contextvars.ContextVar("pytest_cpu_pool_start", default=False)
_original_start = threading.Thread.start
_original_submit = concurrent.futures.ThreadPoolExecutor.submit
_original_pool_init = concurrent.futures.ThreadPoolExecutor.__init__


@contextlib.contextmanager
def activate(budget: CpuBudget) -> Generator[None]:
    """各段階で使う試験の所有者を、同期処理と非同期taskへ伝える。"""
    token = _budget.set(budget)
    try:
        yield
    finally:
        _budget.reset(token)


def _owned_call(budget: CpuBudget, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    observed = _thread_gc()
    native = threading.get_native_id()

    with budget.lock:
        nested = native in budget.active
    if nested:
        return function(*args, **kwargs)
    with budget.lock:
        started = _thread_cpu()
        budget.active[native] = (started, observed, observed.gc_cpu, observed.import_cpu)
    token = _pool_start.set(False)
    try:
        with activate(budget):
            return function(*args, **kwargs)
    finally:
        _pool_start.reset(token)
        with budget.lock:
            _, _, gc_before, import_before = budget.active.pop(native)
            budget.completed["worker"] += _thread_cpu() - started
            budget.completed["gc"] += observed.gc_cpu - gc_before
            budget.completed["import"] += observed.import_cpu - import_before


def _start_thread(thread: threading.Thread) -> None:
    budget = _budget.get()
    if budget is None or _pool_start.get():
        _original_start(thread)
        return
    original = thread.run
    own_run = "run" in vars(thread)

    def run() -> None:
        try:
            _owned_call(budget, original)
        finally:
            if own_run:
                vars(thread)["run"] = original
            else:
                del thread.run

    vars(thread)["run"] = run
    try:
        _original_start(thread)
    except BaseException:
        if own_run:
            vars(thread)["run"] = original
        else:
            del thread.run
        raise


def _submit(executor: concurrent.futures.ThreadPoolExecutor, function: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    budget = _budget.get()

    token = _pool_start.set(True)
    try:
        if budget is None:
            return _original_submit(executor, function, *args, **kwargs)
        return _original_submit(executor, _owned_call, budget, function, *args, **kwargs)
    finally:
        _pool_start.reset(token)


def _init_pool(
    executor: concurrent.futures.ThreadPoolExecutor,
    max_workers: int | None = None,
    thread_name_prefix: str = "",
    initializer: Callable[..., Any] | None = None,
    initargs: tuple[Any, ...] = (),
    **ctxkwargs: Any,
) -> None:

    budget = _budget.get()
    if budget is not None and callable(initializer):
        initializer = functools.partial(_owned_call, budget, initializer)
    _original_pool_init(executor, max_workers, thread_name_prefix, initializer, initargs, **ctxkwargs)


@dataclasses.dataclass
class _Observer:
    owner: object | None = None
    thread: int | None = None
    import_depth: int = 0
    gc_cpu: float = 0.0
    import_cpu: float = 0.0
    local: threading.local = dataclasses.field(default_factory=threading.local)
    original_import: Callable[..., Any] = _IMPORT_NAMESPACE["_find_and_load"]
    original_collect: Callable[..., int] = gc.collect
    patches: contextlib.ExitStack = dataclasses.field(default_factory=contextlib.ExitStack)


_state = _Observer()


def _thread_gc() -> _ThreadGc:
    observed = getattr(_state.local, "gc", None)
    if not isinstance(observed, _ThreadGc):
        observed = _ThreadGc()
        _state.local.gc = observed
    return observed


def _gc_event(phase: str, info: dict[str, int]) -> None:

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
            elapsed = max(0.0, _thread_cpu() - started - (observed.gc_cpu + observed.gc_work_cpu - gc_before))
            observed.import_cpu += elapsed
            _state.import_cpu += elapsed


def start(owner: object) -> None:
    if _state.owner is not None:
        return
    _state.owner, _state.thread = owner, threading.get_ident()
    _state.gc_cpu = _state.import_cpu = 0.0
    _state.import_depth = 0

    _state.local = threading.local()
    _state.original_import = _IMPORT_NAMESPACE["_find_and_load"]
    _state.original_collect = gc.collect
    _IMPORT_NAMESPACE["_find_and_load"] = _import

    gc.collect = _collect  # ty: ignore[invalid-assignment]
    gc.callbacks.append(_gc_event)
    _state.patches.enter_context(unittest.mock.patch.object(threading.Thread, "start", _start_thread))
    _state.patches.enter_context(unittest.mock.patch.object(concurrent.futures.ThreadPoolExecutor, "__init__", _init_pool))
    _state.patches.enter_context(unittest.mock.patch.object(concurrent.futures.ThreadPoolExecutor, "submit", _submit))


def stop(owner: object) -> None:
    if _state.owner is not owner:
        return
    if _IMPORT_NAMESPACE["_find_and_load"] is _import:
        _IMPORT_NAMESPACE["_find_and_load"] = _state.original_import
    if gc.collect is _collect:
        gc.collect = _state.original_collect
    if _gc_event in gc.callbacks:
        gc.callbacks.remove(_gc_event)
    _state.patches.close()
    _state.owner, _state.thread = None, None
    _state.local = threading.local()


def snapshot(budget: CpuBudget | None = None) -> dict[str, float]:
    if budget is None:
        return {"gc": _state.gc_cpu, "import": _state.import_cpu}
    with budget.lock:
        result = {**budget.completed}

        if budget.active:
            clocks = {row.id: row.user_time + row.system_time for row in _process_threads()}
            for native, (started, worker, gc_before, import_before) in budget.active.items():
                result["worker"] += max(0.0, clocks.get(native, started) - started)
                result["gc"] += worker.gc_cpu - gc_before
                result["import"] += worker.import_cpu - import_before
        return result


def main_snapshot() -> dict[str, float]:
    observed = _thread_gc()
    return {"gc": observed.gc_cpu, "import": observed.import_cpu}
