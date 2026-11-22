import asyncio
import contextlib
import json
import logging
import typing

from agent_toolkit._atk.serve import session_costs as _session_costs
from agent_toolkit._atk.serve import session_watch
from agent_toolkit._atk.serve.sessions_models import SUBSCRIBER_QUEUE_SIZE, SessionsContext, SessionsState
from agent_toolkit._common.async_operations import run_sync_operation

logger = logging.getLogger("agent_toolkit._atk.serve.sessions")


def schedule_delivery(
    state: SessionsState,
    loop: asyncio.AbstractEventLoop,
    factory: typing.Callable[[], typing.Coroutine[typing.Any, typing.Any, None]],
) -> None:

    def accepted() -> None:
        if not state.local_rescan_stopping:
            _ = asyncio.create_task(factory())

    with contextlib.suppress(RuntimeError):
        loop.call_soon_threadsafe(accepted)


async def subscribe(state: SessionsState) -> asyncio.Queue[str]:
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    async with state.lock:
        state.subscribers.add(queue)
    return queue


async def unsubscribe(state: SessionsState, queue: asyncio.Queue[str]) -> None:
    async with state.lock:
        state.subscribers.discard(queue)


async def deliver_refresh(state: SessionsState) -> None:
    payload = json.dumps({"type": "refresh"}, ensure_ascii=False)
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(payload)


async def deliver_record(state: SessionsState, host: str, engine: str, path: str) -> None:
    payload = json.dumps(
        {"type": session_watch.RECORD_TYPE, "host": host, "engine": engine, "path": path},
        ensure_ascii=False,
    )
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(json.dumps({"type": "refresh"}, ensure_ascii=False))


async def _relay_remote_notification(state: SessionsState, host: str, event: dict[str, typing.Any]) -> None:
    kind = event.get("type")
    if kind == session_watch.REFRESH_TYPE:
        await deliver_refresh(state)
        return
    if kind == session_watch.RECORD_TYPE:
        engine = event.get("engine")
        path = event.get("path")
        if isinstance(engine, str) and isinstance(path, str):
            await deliver_record(state, host, engine, path)


def start_local_watch(context: SessionsContext) -> None:
    loop = asyncio.get_running_loop()
    context.state.record_watch = _start_local_record_watch(context, loop)


def _start_local_record_watch(context: SessionsContext, loop: asyncio.AbstractEventLoop) -> session_watch.RecordWatch | None:

    def on_flush(refresh: bool, records: list[tuple[str, str]]) -> None:
        async def deliver() -> None:
            if refresh:
                await deliver_refresh(context.state)
            for engine, path in records:
                await deliver_record(context.state, context.hostname, engine, path)

        schedule_delivery(context.state, loop, deliver)

    tracker = session_watch.RecordChangeTracker(
        [(context.claude_home / "projects", "claude"), (context.codex_home / "sessions", "codex")],
        on_flush,
    )
    watch = session_watch.RecordWatch(tracker)
    try:
        watch.start()
    except OSError as error:
        logger.warning("セッション記録の監視を開始できません: %s", error)
        return None
    context.state.watch_loop = loop
    context.state.cost_warmer = _session_costs.FamilyCostWarmer(
        context.state.family_costs,
        claude_home=context.claude_home,
        codex_home=context.codex_home,
        state_dir=context.state_dir,
        slim_directory=context.state.slim_records.directory,
    )
    return watch


async def start_local_watch_async(context: SessionsContext) -> None:
    loop = asyncio.get_running_loop()
    await run_sync_operation(
        _start_local_record_watch,
        context,
        loop,
        on_success=lambda watch: setattr(context.state, "record_watch", watch),
    )


async def stop_local_watch_async(context: SessionsContext) -> None:
    with context.state.local_rescan_flag_lock:
        context.state.local_rescan_stopping = True
        thread = context.state.local_rescan_thread
    if thread is not None:
        await run_sync_operation(thread.join)
        context.state.local_rescan_thread = None
    watch = context.state.record_watch
    if watch is not None:
        await run_sync_operation(watch.stop, on_success=lambda _result: setattr(context.state, "record_watch", None))
    warmer = context.state.cost_warmer
    if warmer is not None:
        await run_sync_operation(warmer.stop, on_success=lambda _result: setattr(context.state, "cost_warmer", None))


def stop_local_watch(context: SessionsContext) -> None:
    with context.state.local_rescan_flag_lock:
        context.state.local_rescan_stopping = True
        thread = context.state.local_rescan_thread
    if thread is not None:
        thread.join()
        context.state.local_rescan_thread = None
    warmer = context.state.cost_warmer
    if warmer is not None:
        warmer.stop()
        context.state.cost_warmer = None
    watch = context.state.record_watch
    if watch is None:
        return
    context.state.record_watch = None
    watch.stop()
