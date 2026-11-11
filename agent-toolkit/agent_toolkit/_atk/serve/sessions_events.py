"""セッション画面の更新通知とローカル監視。"""

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


async def subscribe(state: SessionsState) -> asyncio.Queue[str]:
    """SSE購読キューを生成して登録し返す。"""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
    async with state.lock:
        state.subscribers.add(queue)
    return queue


async def unsubscribe(state: SessionsState, queue: asyncio.Queue[str]) -> None:
    """購読キューを解除する。存在しない場合はエラーにしない。"""
    async with state.lock:
        state.subscribers.discard(queue)


async def deliver_refresh(state: SessionsState) -> None:
    """全購読者へ一覧の再取得を促す通知を配信する。

    未配信の通知は破棄して一覧の再取得の1件だけを残す。
    一覧の再取得は選択中の記録の再取得も伴うため、破棄した通知の内容を含み、同じ通知も重ねない。
    """
    payload = json.dumps({"type": "refresh"}, ensure_ascii=False)
    async with state.lock:
        targets = list(state.subscribers)
    for queue in targets:
        while not queue.empty():
            queue.get_nowait()
        queue.put_nowait(payload)


async def deliver_record(state: SessionsState, host: str, engine: str, path: str) -> None:
    """全購読者へ記録1件の更新を配信する。

    一覧の再取得を促す通知と異なり、選択中の記録を開いている購読者だけが詳細を再取得する。
    キューが満杯の場合は未配信の通知を破棄し、一覧の再取得を促す通知へ置き換える。
    一覧の再取得は選択中の記録の再取得も伴うため、破棄した更新を補える。
    """
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
    """リモートヘルパーの変更通知をSSEへ中継する。未知の型は無視する。"""
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
    """ローカルの記録rootの変更監視を開始する。

    監視は一覧の再取得と記録1件の更新の2種類の通知を、イベントループ上の購読者へ中継する。
    """
    loop = asyncio.get_running_loop()
    context.state.record_watch = _start_local_record_watch(context, loop)


def _start_local_record_watch(context: SessionsContext, loop: asyncio.AbstractEventLoop) -> session_watch.RecordWatch | None:

    def on_flush(refresh: bool, records: list[tuple[str, str]]) -> None:
        async def deliver() -> None:
            if refresh:
                await deliver_refresh(context.state)
            for engine, path in records:
                await deliver_record(context.state, context.hostname, engine, path)

        with contextlib.suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(deliver(), loop)

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
    """監視準備をworkerで待ち、保持欄をloopで更新する。"""
    loop = asyncio.get_running_loop()
    await run_sync_operation(
        _start_local_record_watch,
        context,
        loop,
        on_success=lambda watch: setattr(context.state, "record_watch", watch),
    )


async def stop_local_watch_async(context: SessionsContext) -> None:
    """監視の全threadが終了してから保持欄を空へ戻す。"""
    watch = context.state.record_watch
    if watch is not None:
        await run_sync_operation(watch.stop, on_success=lambda _result: setattr(context.state, "record_watch", None))
    warmer = context.state.cost_warmer
    if warmer is not None:
        await run_sync_operation(warmer.stop, on_success=lambda _result: setattr(context.state, "cost_warmer", None))


def stop_local_watch(context: SessionsContext) -> None:
    """ローカルの記録rootの変更監視を終了させる。"""
    warmer, context.state.cost_warmer = context.state.cost_warmer, None
    if warmer is not None:
        warmer.stop()
    watch = context.state.record_watch
    if watch is None:
        return
    context.state.record_watch = None
    watch.stop()
