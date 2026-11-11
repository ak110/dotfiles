"""serveのstateに関するsynchronizationの試験。"""

# pylint: disable=protected-access

import asyncio
import contextlib
import pathlib
import typing

import pytest
import watchdog.events

from agent_toolkit._atk.serve import state, wi_operations
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing.serve_support import _BlockingSync, _FakeTimer, _recorder, _sync_app


@pytest.mark.asyncio
async def test_state_publishes_at_max_wait_deadline_and_restarts_debounce(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値未満の間隔で変更が続いても最大待機時間で1回通知し、以降は新しい保留期間を開始する。

    実時間のスケジューリング遅延で結果が変わらないよう、単調時計とタイマーを注入して検証する。
    """
    debounce = 0.05
    max_wait = debounce * state.ServeState._MAX_DEBOUNCE_FACTOR
    clock = [1_000.0]
    timers: list[_FakeTimer] = []

    def _make_timer(interval: float, function: typing.Callable[..., None], args: tuple[typing.Any, ...] = ()) -> _FakeTimer:
        timer = _FakeTimer(interval, function, args)
        timers.append(timer)
        return timer

    current = state.ServeState(
        tmp_path,
        debounce_seconds=debounce,
        monotonic=lambda: clock[0],
        timer_factory=_make_timer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    # 閾値未満の間隔で変更を送り続け、期限に到達するまで発行されないことを確認する。
    deadline = clock[0] + max_wait
    while clock[0] < deadline:
        current.on_modified(event)
        # 直前に設定したタイマーの発火時刻が期限を超えないこと（期限が上界であること）。
        assert clock[0] + timers[-1].interval <= deadline
        await asyncio.sleep(0)
        assert not published
        clock[0] += 0.02

    # 期限に到達した変更はタイマーを介さず直ちに発行する。
    pending_before = len(timers)
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    assert len(timers) == pending_before

    # 以降は新しい保留期間となり、静穏期間の経過で再び1回発行する。
    clock[0] += 0.01
    current.on_modified(event)
    await asyncio.sleep(0)
    assert published == ["changed"]
    latest = timers[-1]
    assert latest.interval == debounce
    latest.function(*latest.args)
    await asyncio.sleep(0)
    assert published == ["changed", "changed"]


@pytest.mark.asyncio
async def test_cancelled_sync_request_does_not_cancel_shared_sync(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """開始要求のキャンセル後も共有同期が継続し、別要求へ同じ結果を返す。"""
    operations = wi_operations.Operations(tmp_path)
    sync = _BlockingSync()
    monkeypatch.setattr(operations, "sync", sync)
    app = _sync_app(tmp_path, operations)
    first = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.to_thread(sync.started.wait)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(app.test_client().post("/api/sync"))
    await asyncio.sleep(0)
    sync.release.set()
    response = await second
    assert sync.calls == 1
    assert await response.get_json() == {"synced": True}


def test_serve_state_watches_all_queue_states(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """状態監視は年月配置を含むキューrootを再帰監視する。"""
    current = state.ServeState(tmp_path)
    scheduled: list[tuple[str, bool]] = []
    observer = state.watchdog.observers.Observer()
    monkeypatch.setattr(state.watchdog.observers, "Observer", lambda: observer)
    monkeypatch.setattr(
        observer,
        "schedule",
        lambda _handler, path, recursive=False: scheduled.append((path, recursive)),
    )
    monkeypatch.setattr(observer, "start", lambda: None)
    loop = asyncio.new_event_loop()
    try:
        current.start(loop)
        assert scheduled == [(str(tmp_path), True)]
        assert not (tmp_path / "awi").exists()
        assert not (tmp_path / "uwi").exists()
    finally:
        current.stop()
        loop.close()


def test_sync_ignores_rate_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """エンドユーザーの明示的な同期はレート制限を経由せず、毎回未送信commitをpushしてからpullする。"""
    calls: list[str] = []

    @contextlib.contextmanager
    def lock(_path: pathlib.Path, **_kwargs: object) -> typing.Generator[None]:
        yield

    monkeypatch.setattr(_wi_sync, "repo_lock", lock)
    monkeypatch.setattr(_wi_sync, "push_pending_commits", _recorder(calls, "push", result=None))
    monkeypatch.setattr(_wi_sync, "pull", _recorder(calls, "pull", result=None))
    monkeypatch.setattr(_wi_sync, "pull_if_stale", _recorder(calls, "pull_if_stale", result=True))
    operations = wi_operations.Operations(tmp_path)
    assert operations.sync() is True
    assert operations.sync() is True
    assert calls == ["push", "pull", "push", "pull"]


def test_markdown_change_marks_index_before_debounced_publish(tmp_path: pathlib.Path) -> None:
    """Markdownの変更は通知の間引きを待たずに索引の変更記録へ渡し、Markdown以外とディレクトリは渡さない。

    間引き後の通知で画面が再取得したときに、索引が古い一覧を返さないようにするためである。
    """
    current = state.ServeState(tmp_path, debounce_seconds=60, timer_factory=_FakeTimer)
    calls: list[str] = []
    current.change_listener = lambda: calls.append("changed")

    current.on_created(watchdog.events.FileCreatedEvent(str(tmp_path / "2026" / "10" / "a.md")))
    current.on_modified(watchdog.events.FileModifiedEvent(str(tmp_path / "note.txt")))
    current.on_created(watchdog.events.DirCreatedEvent(str(tmp_path / "2026" / "11")))

    assert calls == ["changed"]
