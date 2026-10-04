"""監視開始に失敗したserveの後始末を検証する。"""

import contextlib
import queue
import threading
from typing import Any

import pytest

from pytools.media_remote import _window_move


@pytest.mark.parametrize("fail_worker", [False, True])
def test_monitor_start_failure_releases_worker_and_selection(fail_worker: bool) -> None:
    closed = threading.Event()
    events: queue.Queue[Any] = queue.Queue()

    @contextlib.contextmanager
    def uia() -> Any:
        try:
            if fail_worker:
                raise OSError("UIA initialization failed")
            yield None
        finally:
            closed.set()

    class FailedAPI:
        def physical_coordinates(self) -> Any:
            return contextlib.nullcontext()

        def run_hooks(self, *args: Any) -> None:
            del args
            raise OSError("hook registration failed")

        def stop_hooks(self) -> None:
            pass

    monitor = _window_move.WindowMover(api=FailedAPI(), uia_factory=uia, events=events)
    with pytest.raises(OSError), monitor:
        pytest.fail("開始に失敗した監視をserveへ渡した")
    assert closed.is_set()
    assert not monitor.on_mouse(0x207, (1, 2), 1, "")
    assert not any(thread.name.startswith("media-window-") for thread in threading.enumerate())
