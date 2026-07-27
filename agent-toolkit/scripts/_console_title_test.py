"""ターミナルタイトル制御のテスト。"""

# pylint: disable=protected-access

import contextlib
import io
import typing

import _console_title
import pytest


class _Tty(io.StringIO):
    """ターミナルへ接続された出力先を模擬する。"""

    def isatty(self) -> bool:
        return True


class _Detached(io.StringIO):
    """`isatty`が例外を送出する出力先を模擬する。"""

    def isatty(self) -> typing.NoReturn:
        raise ValueError("closed stream")


def test_sets_and_restores_title_on_tty() -> None:
    """ターミナル接続時はタイトルを設定し、終了時に空タイトルへ戻す。"""
    stream = _Tty()
    with _console_title.console_title("atk serve :28766", stream=stream):
        assert stream.getvalue() == "\033]2;atk serve :28766\a"
    assert stream.getvalue() == "\033]2;atk serve :28766\a\033]2;\a"


def test_restores_title_when_body_raises() -> None:
    """本体が例外を送出してもタイトルを元へ戻す。"""
    stream = _Tty()
    with pytest.raises(RuntimeError), _console_title.console_title("atk serve :28766", stream=stream):
        raise RuntimeError("body failure")
    assert stream.getvalue().endswith("\033]2;\a")


def test_skips_without_tty() -> None:
    """ターミナル未接続時は制御文字を出力しない。"""
    stream = io.StringIO()
    with _console_title.console_title("atk serve :28766", stream=stream):
        pass
    assert stream.getvalue() == ""


def test_skips_when_isatty_raises() -> None:
    """`isatty`が例外を送出する出力先でも何もせず通過する。"""
    stream = _Detached()
    with _console_title.console_title("atk serve :28766", stream=stream):
        pass
    assert stream.getvalue() == ""


def test_uses_windows_api_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowsではコンソールタイトル設定APIの分岐へ入りOSC制御文字を出力しない。"""
    monkeypatch.setattr(_console_title.sys, "platform", "win32")
    entered: list[str] = []

    @contextlib.contextmanager
    def fake_branch(title: str) -> typing.Iterator[None]:
        entered.append(title)
        yield

    monkeypatch.setattr(_console_title, "_windows_console_title", fake_branch)
    stream = _Tty()
    with _console_title.console_title("atk serve :28766", stream=stream):
        pass
    assert entered == ["atk serve :28766"]
    assert stream.getvalue() == ""


def test_osc_sequence_targets_window_title_only() -> None:
    """OSC制御シーケンスはウィンドウタイトルのみを対象とする。"""
    assert _console_title._osc_set_title("x") == "\033]2;x\a"


def test_set_console_title_uses_windows_api_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windowsでは`set_console_title`もコンソールタイトル設定APIを直接呼び復元しない。"""
    monkeypatch.setattr(_console_title.sys, "platform", "win32")
    calls: list[str] = []
    monkeypatch.setattr(_console_title, "_set_windows_console_title", calls.append)
    stream = _Tty()
    _console_title.set_console_title("atk mq process-loop", stream=stream)
    assert calls == ["atk mq process-loop"]
    assert stream.getvalue() == ""


def test_set_console_title_writes_osc_sequence_without_restore() -> None:
    """非WindowsのTTYでは`set_console_title`がOSC制御文字を1回だけ出力する。"""
    stream = _Tty()
    _console_title.set_console_title("atk mq process-loop", stream=stream)
    assert stream.getvalue() == "\033]2;atk mq process-loop\a"


def test_set_console_title_skips_without_tty() -> None:
    """ターミナル未接続時は`set_console_title`も制御文字を出力しない。"""
    stream = io.StringIO()
    _console_title.set_console_title("atk mq process-loop", stream=stream)
    assert stream.getvalue() == ""
