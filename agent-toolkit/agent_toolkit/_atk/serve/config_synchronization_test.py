"""serveのconfigに関するsynchronizationの試験。"""

# pylint: disable=protected-access

import asyncio
import contextlib
import pathlib
import threading

import pytest
import watchdog.events

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, state, wi_operations
from agent_toolkit._atk.serve import runtime as serve_runtime


@pytest.mark.asyncio
async def test_state_publishes_only_markdown_change_events(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Markdownの変更イベントだけを購読者へ通知する。"""
    events = [
        ("on_opened", watchdog.events.FileOpenedEvent(str(tmp_path / "entry.md")), False),
        ("on_closed_no_write", watchdog.events.FileClosedNoWriteEvent(str(tmp_path / "entry.md")), False),
        ("on_created", watchdog.events.FileCreatedEvent(str(tmp_path / "entry.md")), True),
        ("on_modified", watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md")), True),
        ("on_deleted", watchdog.events.FileDeletedEvent(str(tmp_path / "entry.md")), True),
        ("on_created", watchdog.events.FileCreatedEvent(str(tmp_path / "entry.lock")), False),
        ("on_modified", watchdog.events.FileModifiedEvent(str(tmp_path / "entry.lock")), False),
        ("on_deleted", watchdog.events.FileDeletedEvent(str(tmp_path / "entry.lock")), False),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.tmp"),
                str(tmp_path / "entry.md"),
            ),
            True,
        ),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.md"),
                str(tmp_path / "entry.tmp"),
            ),
            True,
        ),
        (
            "on_moved",
            watchdog.events.FileMovedEvent(
                str(tmp_path / "entry.lock"),
                str(tmp_path / "entry.lock.bak"),
            ),
            False,
        ),
    ]
    for handler_name, event, expected in events:
        current = state.ServeState(tmp_path, debounce_seconds=0)
        current._loop = asyncio.get_running_loop()
        published = threading.Event()
        monkeypatch.setattr(current, "publish", published.set)
        getattr(current, handler_name)(event)
        await asyncio.sleep(0)
        assert published.is_set() is expected


@pytest.mark.asyncio
async def test_sse_multiple_subscribers_and_cleanup(tmp_path: pathlib.Path) -> None:
    """複数購読者へ変更通知を配信し、切断後に購読を除去する。"""
    current = state.ServeState(tmp_path)
    first = current.events(heartbeat=1)
    second = current.events(heartbeat=1)
    # 購読の登録直後に送る合図を受け取ってから、変更を通知する。
    assert "event: subscribed" in await anext(first)
    assert "event: subscribed" in await anext(second)
    first_next = asyncio.create_task(anext(first))
    second_next = asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    current.publish()
    assert "event: changed" in await first_next
    assert "event: changed" in await second_next
    await first.aclose()
    await second.aclose()


@pytest.mark.asyncio
async def test_background_sync_task_starts_and_stops(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """定期更新タスクが起動時に開始し終了時に停止する。"""
    monkeypatch.setattr(serve_runtime, "_BACKGROUND_SYNC_INTERVAL_SECONDS", 0.001)
    calls: list[str] = []
    called = asyncio.Event()
    loop = asyncio.get_running_loop()

    class _Operations(wi_operations.Operations):
        def background_sync(self) -> bool:
            calls.append("sync")
            loop.call_soon_threadsafe(called.set)
            return True

    current_state = state.ServeState(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state,
        operations=_Operations(tmp_path),
    )
    async with app.test_app():  # ty: ignore[invalid-context-manager]
        # 初回呼び出しの成立を待ち、起動を確認する。
        await asyncio.wait_for(called.wait(), timeout=5)

    # 停止の成立は、`after_serving`の完了後に呼び出し回数が増えないことで確認する。
    # 実行中の周期が残りうるため、基準を取る前に処理待ちのタスクを消化させる。
    await asyncio.sleep(0)
    before = len(calls)
    called.clear()
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(called.wait(), timeout=0.1)
    assert len(calls) == before
