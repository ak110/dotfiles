"""監視の獲得直後と配信準備中の例外でも所有した資源を回収する。"""

import asyncio
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk.serve import session_watch, state
from agent_toolkit._atk.wi import process_loop_watch


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
    """公開されたstartメソッド自身が、開始途中に得た監視資源を回収する。"""
    observer = _Observer(fail_start=True)
    monkeypatch.setattr(state.watchdog.observers, "Observer", lambda: observer)
    watched = state.ServeState(tmp_path)
    with pytest.raises(OSError, match="監視開始失敗"):
        if asynchronous:
            await watched.start_async(asyncio.get_running_loop())
        else:
            watched.start(asyncio.get_running_loop())
    assert observer.stopped == 1
    assert observer.joined == 0


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
