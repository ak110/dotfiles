"""serveのassetsに関するsynchronizationの試験。"""

# pylint: disable=protected-access

import asyncio
import pathlib
import typing

import pytest
import watchdog.events

from agent_toolkit._atk.serve import state
from agent_toolkit._testing.serve_support import _FakeTimer


@pytest.mark.asyncio
async def test_state_publishes_once_after_last_change(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """閾値内の連続変更をまとめ、最後の変更後に1回だけ通知する。"""
    clock = [1_000.0]
    timers: list[_FakeTimer] = []

    def _make_timer(
        interval: float,
        function: typing.Callable[..., None],
        args: tuple[typing.Any, ...] = (),
    ) -> _FakeTimer:
        timer = _FakeTimer(interval, function, args)
        timers.append(timer)
        return timer

    current = state.ServeState(
        tmp_path,
        debounce_seconds=0.03,
        monotonic=lambda: clock[0],
        timer_factory=_make_timer,
    )
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))
    event = watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md"))

    current.on_modified(event)
    clock[0] += 0.01
    current.on_modified(event)
    clock[0] += 0.01
    current.on_modified(event)

    assert [timer.cancelled for timer in timers] == [True, True, False]
    for timer in timers[:-1]:
        timer.function(*timer.args)
    await asyncio.sleep(0)
    assert not published
    timers[-1].function(*timers[-1].args)
    await asyncio.sleep(0)
    assert published == ["changed"]


@pytest.mark.asyncio
async def test_state_discards_pending_notification_when_stopped(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """監視停止時は保留中の通知を破棄する。"""
    clock = [1_000.0]
    timers: list[_FakeTimer] = []

    def make_timer(interval, function, args=()):
        timer = _FakeTimer(interval, function, args)
        timers.append(timer)
        return timer

    current = state.ServeState(tmp_path, debounce_seconds=0.02, monotonic=lambda: clock[0], timer_factory=make_timer)
    current._loop = asyncio.get_running_loop()
    published: list[str] = []
    monkeypatch.setattr(current, "publish", lambda: published.append("changed"))

    current.on_modified(watchdog.events.FileModifiedEvent(str(tmp_path / "entry.md")))
    current.stop()
    assert len(timers) == 1 and timers[0].cancelled
    clock[0] += 0.04
    timers[0].function(*timers[0].args)
    await asyncio.sleep(0)

    assert not published
