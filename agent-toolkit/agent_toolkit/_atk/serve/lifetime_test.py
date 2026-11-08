"""監視の獲得直後と配信準備中の例外でも所有した資源を回収する。"""

import asyncio
import errno
import pathlib
import threading
from typing import Any

import pytest

from agent_toolkit._atk.serve import session_watch, state
from agent_toolkit._atk.serve.plans import views
from agent_toolkit._atk.wi import process_loop_watch
from agent_toolkit._common import watch_observer
from agent_toolkit._testing.serve_plans_support import _context


class _Observer:
    def __init__(self, *, fail_start: bool = False) -> None:
        self.ident: int | None = None
        self.fail_start = fail_start
        self.stopped = 0
        self.joined = 0

    def schedule(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def start(self) -> None:
        if self.fail_start:
            raise OSError("監視開始失敗")
        self.ident = 1

    def stop(self) -> None:
        self.stopped += 1

    def join(self) -> None:
        assert self.ident is not None
        self.joined += 1


@pytest.mark.asyncio
async def test_serve_state_acquires_observer_only_when_started(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """監視を使わない状態の構築と停止は、ObserverのOS資源を確保しない。"""
    created: list[_Observer] = []

    def factory() -> _Observer:
        observer = _Observer()
        created.append(observer)
        return observer

    monkeypatch.setattr(state.watchdog.observers, "Observer", factory)
    watched = state.ServeState(tmp_path)
    try:
        assert not created
        watched.stop()
        assert not created
        assert not watched.is_watching()
        await watched.start_async(asyncio.get_running_loop())
        assert len(created) == 1
    finally:
        await watched.stop_async()
    assert created[0].stopped == created[0].joined == 1


def test_observer_constructor_exhaustion_uses_existing_polling_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Observerの構築時に通知資源が不足しても、同じ監視登録をポーリングへ渡す。"""
    polling: Any = _Observer()
    scheduled: list[Any] = []
    errors: list[OSError] = []

    def exhausted() -> None:
        raise OSError(errno.EMFILE, "inotify instance limit reached")

    monkeypatch.setattr(watch_observer.watchdog.observers, "Observer", exhausted)
    monkeypatch.setattr(watch_observer.watchdog.observers.polling, "PollingObserver", lambda **_kwargs: polling)
    observer = watch_observer.start_observer(scheduled.append, on_fallback=errors.append)
    try:
        assert observer is polling and scheduled == [polling]
        assert len(errors) == 1 and errors[0].errno == errno.EMFILE
    finally:
        observer.stop()
        observer.join()
    assert scheduled[0].stopped == scheduled[0].joined == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_start", [False, True])
async def test_serve_state_context_cleans_acquisition_and_body_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fail_start: bool
) -> None:
    """開始前の失敗と開始後の例外で、未開始のthreadをjoinせず終了する。"""
    observer = _Observer(fail_start=fail_start)
    monkeypatch.setattr(state.watchdog.observers, "Observer", lambda: observer)
    with pytest.raises((OSError, ValueError)):
        async with state.ServeState(tmp_path):
            raise ValueError("配信準備失敗")
    assert observer.stopped == 1
    assert observer.joined == (0 if fail_start else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_direct_start_failure_releases_partial_watch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, asynchronous: bool
) -> None:
    """通常の監視とポーリング監視の両方を開始できない場合、公開startが開始途中の監視資源を回収して送出する。"""
    observer = _Observer(fail_start=True)
    polling = _Observer(fail_start=True)
    monkeypatch.setattr(state.watchdog.observers, "Observer", lambda: observer)
    monkeypatch.setattr(watch_observer.watchdog.observers.polling, "PollingObserver", lambda **_kwargs: polling)
    watched = state.ServeState(tmp_path)
    with pytest.raises(OSError, match="監視開始失敗"):
        if asynchronous:
            await watched.start_async(asyncio.get_running_loop())
        else:
            watched.start(asyncio.get_running_loop())
    assert observer.stopped >= 1 and polling.stopped >= 1
    assert observer.joined == polling.joined == 0


@pytest.mark.parametrize("fail_start", [False, True])
def test_process_loop_cleans_observer_on_start_or_wait_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, fail_start: bool
) -> None:
    """開始途中と開始後の両方で、待機の所有境界が停止を保証する。"""
    observer = _Observer(fail_start=fail_start)
    monkeypatch.setattr(process_loop_watch.watchdog.observers, "Observer", lambda: observer)

    def fail_wait(*_args: Any, **_kwargs: Any) -> bool:
        raise ValueError("待機失敗")

    monkeypatch.setattr(process_loop_watch, "_wait_event", fail_wait)
    with pytest.raises((OSError, ValueError)):
        process_loop_watch.wait_for_changes(tmp_path, None, timeout=0)
    assert observer.stopped == 1
    assert observer.joined == (0 if fail_start else 1)


def test_record_watch_releases_observer_after_tracker_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """複数の停止失敗を保持し、後段のjoinまで実行する。"""
    observer = _Observer()
    monkeypatch.setattr(state.watchdog.observers, "Observer", lambda: observer)
    tracker = session_watch.RecordChangeTracker([], lambda _refresh, _records: None)
    watched = session_watch.RecordWatch(tracker)
    watched.start()

    def fail_tracker() -> None:
        raise ValueError("通知の停止失敗")

    def fail_observer() -> None:
        observer.stopped += 1
        raise OSError("監視の停止失敗")

    monkeypatch.setattr(tracker, "stop", fail_tracker)
    monkeypatch.setattr(observer, "stop", fail_observer)
    with pytest.raises(BaseExceptionGroup) as failed:
        watched.stop()
    assert [type(error) for error in failed.value.exceptions] == [ValueError, OSError]
    assert observer.stopped == observer.joined == 1


class _Exhausted(_Observer):
    """inotifyの上限到達と同じ`OSError(EMFILE)`で開始に失敗する、watchdogが標準で選ぶObserverの代役。"""

    def start(self) -> None:
        raise OSError(errno.EMFILE, "inotify instance limit reached")


@pytest.fixture(name="exhausted")
def _exhausted(monkeypatch: pytest.MonkeyPatch) -> list[_Exhausted]:
    """watchdogが標準で選ぶObserverの開始をinotifyの上限到達で失敗させ、切替後のポーリング間隔を短くする。"""
    created: list[_Exhausted] = []

    def factory() -> _Exhausted:
        created.append(_Exhausted())
        return created[-1]

    monkeypatch.setattr(watch_observer.watchdog.observers, "Observer", factory)
    monkeypatch.setattr(watch_observer, "POLLING_INTERVAL_SECONDS", 0.05)
    return created


def test_process_loop_wait_falls_back_to_polling_and_warns_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], exhausted: list[_Exhausted]
) -> None:
    """inotifyの上限到達でもprocess-loopの待機は変更を検知して戻り、切替の警告は待機を繰り返しても1回だけ出る。"""
    monkeypatch.setattr(process_loop_watch, "_polling_fallback_reported", False)
    private_notes = tmp_path / "private-notes"
    for index in range(2):
        timer = threading.Timer(0.5, (private_notes / f"entry-{index}.md").write_text, ("本文",))
        timer.start()
        try:
            assert process_loop_watch.wait_for_changes(private_notes, None, timeout=10)
        finally:
            timer.cancel()
    assert len(exhausted) == 2 and all(observer.stopped == 1 for observer in exhausted)
    assert capsys.readouterr().err.count("ポーリング監視へ切り替えた") == 1


@pytest.mark.asyncio
async def test_serve_state_falls_back_to_polling_and_notifies(
    tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture, exhausted: list[_Exhausted]
) -> None:
    """inotifyの上限到達でも`ServeState`は開始し、Markdownの変更を購読者へ届けて切替を警告する。"""
    async with state.ServeState(tmp_path, debounce_seconds=0) as watched:
        events = watched.events(heartbeat=30)
        assert await anext(events) == "event: subscribed\ndata: {}\n\n"
        await asyncio.to_thread((tmp_path / "entry.md").write_text, "本文")
        assert (await asyncio.wait_for(anext(events), timeout=10)).startswith("event: changed")
        await events.aclose()
    assert len(exhausted) == 1 and exhausted[0].stopped == 1
    assert "ポーリング監視へ切り替えた" in caplog.text


@pytest.mark.asyncio
async def test_plan_watch_falls_back_to_polling_and_notifies(
    tmp_path: pathlib.Path, index_path: pathlib.Path, caplog: pytest.LogCaptureFixture, exhausted: list[_Exhausted]
) -> None:
    """inotifyの上限到達でも計画ファイルの監視は開始し、変更を購読者へ届けて切替を警告する。"""
    del index_path
    root = tmp_path / "plans"
    root.mkdir()
    context = _context(root)
    context.state.debounce_sec = 0.01
    queue: asyncio.Queue[str] = asyncio.Queue()
    context.state.subscribers.add(queue)
    await views.start_local_watchers_async(context)
    try:
        assert context.state.local_observer is not None
        await asyncio.to_thread((root / "plan.md").write_text, "# 計画\n")
        assert await asyncio.wait_for(queue.get(), timeout=10)
    finally:
        await views.stop_local_watchers_async(context)
    assert len(exhausted) == 1 and exhausted[0].stopped == 1
    assert "ポーリング監視へ切り替えた" in caplog.text
