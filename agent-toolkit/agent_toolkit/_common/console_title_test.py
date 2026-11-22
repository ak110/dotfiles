# pylint: disable=protected-access

import io
import typing

from agent_toolkit._common import console_title as _console_title


class _Detached(io.StringIO):
    def isatty(self) -> typing.NoReturn:
        raise ValueError("closed stream")


def test_skips_without_tty() -> None:
    stream = io.StringIO()
    with _console_title.console_title("atk serve", stream=stream):
        pass
    assert stream.getvalue() == ""


def test_skips_when_isatty_raises() -> None:
    stream = _Detached()
    with _console_title.console_title("atk serve", stream=stream):
        pass
    assert stream.getvalue() == ""


def test_set_console_title_skips_without_tty() -> None:
    stream = io.StringIO()
    _console_title.set_console_title("atk wi process-loop", stream=stream)
    assert stream.getvalue() == ""
