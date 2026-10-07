"""`atk serve`の実行時の処理（同期処理の並列数の制限、停止要求中の応答、定期同期、起動と停止の手続き）。"""

import asyncio
import contextlib
import json
import logging
import typing

import quart

from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.serve import wi_operations as _wi_operations

logger = logging.getLogger(__name__)

_BACKGROUND_SYNC_INTERVAL_SECONDS = 60.0
"""定期バックグラウンド更新の間隔。

`atk wi process-loop`が10分間隔で更新する先例に対し、
Web UIはエンドユーザーが画面を閲覧する前提のため短く取る。
"""


SSE_HEARTBEAT_SEC = 15.0
"""3画面のSSEがheartbeatを送る間隔。

中継するリバースプロキシの無通信タイムアウト（Apacheは指定が無ければ300秒）より十分短く保つ。
heartbeatはスクリプトから観測できる名前付きイベントで送り、ブラウザーが無通信を判定する根拠とする。
"""


SSE_STALL_SEC = SSE_HEARTBEAT_SEC * 3
"""ブラウザーがSSEを再接続するまでの無通信時間。heartbeat 3回分の欠落を接続の停止とみなす。"""


class BoundedWorkers:
    """要求キャンセル後も同期処理完了まで同時実行枠を保持する。"""

    def __init__(self, limit: int) -> None:
        self._semaphore = asyncio.Semaphore(limit)

    async def run[**P, R](self, function: typing.Callable[P, R], *args: P.args, **kwargs: P.kwargs) -> R:
        """同期関数を有界ワーカーで実行する。"""

        async def managed() -> R:
            async with self._semaphore:
                return await asyncio.to_thread(function, *args, **kwargs)

        return await asyncio.shield(asyncio.create_task(managed()))


# 停止要求で打ち切った要求へ返す応答本文。WI・計画ファイル・セッションの各画面はAPIの`error`を表示する。
_SHUTDOWN_RESPONSE_BODY = json.dumps(
    {"error": "atk serveが停止処理中のため要求を中断しました。再起動後に画面を再読み込みする"}, ensure_ascii=False
).encode("utf-8")


class ShutdownAwareAsgi:
    """HTTPの各要求の処理を停止要求と競合させ、停止要求が先なら要求を打ち切って応答を完了する。

    hypercornは停止時に`server.wait_closed()`で全接続の終了を待ってから`graceful_timeout`を適用するため、
    処理に時間のかかる要求（一覧・検索の走査やリモート取得）が接続を保持すると停止がその完了まで待つ。
    アプリのASGI呼び出しの1箇所で打ち切り、今後加えるハンドラーも書き足さずに対象とする。
    応答を開始していない要求には503を返す。`BoundedWorkers.run`は`asyncio.shield`で同期処理の完了を保つため、
    WIの変更処理は要求を打ち切っても途中で止まらない。
    """

    def __init__(self, inner: typing.Any, shutdown: asyncio.Event) -> None:
        self._inner = inner
        self._shutdown = shutdown

    async def __call__(self, scope: dict[str, typing.Any], receive: typing.Any, send: typing.Any) -> None:
        if scope.get("type") != "http":
            await self._inner(scope, receive, send)
            return
        progress = {"started": False, "completed": False}

        async def tracked_send(message: dict[str, typing.Any]) -> None:
            if message.get("type") == "http.response.start":
                progress["started"] = True
            elif message.get("type") == "http.response.body" and not message.get("more_body", False):
                progress["completed"] = True
            await send(message)

        request_task = asyncio.ensure_future(self._inner(scope, receive, tracked_send))
        stop_task = asyncio.ensure_future(self._shutdown.wait())
        try:
            await asyncio.wait({request_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            request_task.cancel()
            raise
        finally:
            stop_task.cancel()
        if request_task.done():
            request_task.result()
            return
        request_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await request_task
        if progress["completed"]:
            return
        if not progress["started"]:
            await send(
                {
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"application/json; charset=utf-8")],
                }
            )
            await send({"type": "http.response.body", "body": _SHUTDOWN_RESPONSE_BODY, "more_body": False})
            return
        await send({"type": "http.response.body", "body": b"", "more_body": False})


class ServeRuntime:
    """Webハンドラ間で共有する操作・ワーカー・同期タスクを保持する。"""

    def __init__(self, operations: _wi_operations.Operations, workers: BoundedWorkers, state: serve_state.ServeState) -> None:
        self.operations = operations
        self.workers = workers
        self.state = state
        self.sync_task: asyncio.Task[bool] | None = None
        self.background_task: asyncio.Task[None] | None = None
        self.remote_stop_task: asyncio.Task[None] | None = None

    async def synchronize(self) -> bool:
        """同時に届いた同期要求へ同じ実行結果を返す。"""
        if self.sync_task is None or self.sync_task.done():
            self.sync_task = asyncio.create_task(self.workers.run(self.operations.sync))
        current = self.sync_task
        try:
            result = await asyncio.shield(current)
            self.state.publish("sync-ok")
            return result
        finally:
            if current.done() and self.sync_task is current:
                self.sync_task = None

    async def background_sync_loop(self) -> None:
        """一定間隔でリポジトリを更新し、失敗時は次周期で再試行する。"""
        while True:
            await asyncio.sleep(_BACKGROUND_SYNC_INTERVAL_SECONDS)
            try:
                if await self.workers.run(self.operations.background_sync):
                    self.state.publish("sync-ok")
            except Exception as error:  # pylint: disable=broad-exception-caught
                logger.warning("WIの定期Git同期に失敗しました: %s", error)
                self.state.publish("sync-error")


def register_lifecycle(
    app: quart.Quart,
    runtime: ServeRuntime,
    plans: serve_plans.PlansContext,
    sessions: serve_sessions.SessionsContext,
) -> None:
    """バックグラウンド同期とリモート接続の開始・終了処理を登録する。"""

    @app.before_serving
    async def start_background_tasks() -> None:
        runtime.background_task = asyncio.create_task(runtime.background_sync_loop())
        serve_plans.start_local_watchers(plans)
        serve_plans.start_remote_watchers(plans)
        serve_sessions.start_local_watch(sessions)
        serve_sessions.start_remote_clients(sessions)
        runtime.remote_stop_task = asyncio.create_task(stop_remote_connections_on_shutdown())

    async def stop_remote_connections_on_shutdown() -> None:
        # 停止要求から`after_serving`までの間に常駐SSHが終わると、常駐接続はバックオフの後に再接続する。
        # systemdがcgroup全体へSIGTERMを送る停止では常駐SSHが同時に終わるため、停止要求の時点で停止を始める。
        await runtime.state.shutdown_requested.wait()
        await serve_sessions.stop_remote_clients(sessions)
        await serve_plans.stop_remote_watchers(plans)

    @app.after_serving
    async def stop_background_tasks() -> None:
        if runtime.remote_stop_task is not None:
            if not runtime.remote_stop_task.done():
                runtime.remote_stop_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await runtime.remote_stop_task
            runtime.remote_stop_task = None
        await serve_sessions.stop_remote_clients(sessions)
        serve_sessions.stop_local_watch(sessions)
        await serve_plans.stop_remote_watchers(plans)
        serve_plans.stop_local_watchers(plans)
        if runtime.background_task is None:
            return
        runtime.background_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await runtime.background_task
        runtime.background_task = None
