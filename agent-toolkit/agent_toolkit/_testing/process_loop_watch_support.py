"""process_loop_watchの試験で共有する準備処理と隔離fixture。"""

import collections
import pathlib
import sys
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._atk.wi import process_loop_log as _pl_log
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


class _Console:
    """`msvcrt`の`kbhit`・`getwch`と同じ契約で、対話コンソールへ入力済みのキーを渡す。"""

    def __init__(self, keys: str = "") -> None:
        self.keys = collections.deque(keys)

    def kbhit(self) -> bool:
        return bool(self.keys)

    def getwch(self) -> str:
        return self.keys.popleft()


class _ConsoleStdin:
    """Windowsの対話コンソールに接続した標準入力。"""

    def isatty(self) -> bool:
        return True


def _use_windows_console(monkeypatch: pytest.MonkeyPatch, console: _Console) -> None:
    monkeypatch.setattr(_pl_watch, "_INPUT_PLATFORM", "nt")
    monkeypatch.setitem(sys.modules, "msvcrt", console)
    monkeypatch.setattr(_pl_watch.sys, "stdin", _ConsoleStdin())


def _log_lines() -> list[str]:
    path = _pl_log.log_path()
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _enter_then_stop(console: _Console) -> Callable[..., bool]:
    """最初の開始前待機の間にEnterを入力し、子の起動後の待機で常駐を止める。"""
    calls: list[dict[str, Any]] = []

    def wait(*_args: Any, **kwargs: Any) -> bool:
        calls.append(kwargs)
        interrupt = kwargs.get("interrupt")
        if len(calls) > 1 or interrupt is None:
            raise KeyboardInterrupt
        assert not interrupt()
        console.keys.extend("\r")
        assert interrupt()
        return True

    return wait


_SUBMISSION_ACTIVE = _pl_watch._submission_active  # pylint: disable=protected-access  # noqa: SLF001
