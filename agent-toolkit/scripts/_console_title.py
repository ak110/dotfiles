"""起動ターミナルのウィンドウタイトル制御。

長時間稼働するコマンドが起動したターミナルを占有する場合、
そのターミナルを一覧から識別しやすいよう、稼働中だけウィンドウタイトルを設定する。

設計上の判断:

- ターミナルへ接続されているときだけ設定する。パイプ・リダイレクト・非PTY起動では
  制御文字が出力先へそのまま記録・転送されて表示が乱れるため、`isatty`で接続を判定する。
- Windowsはコンソールタイトル設定API、それ以外はOSC制御シーケンスで設定方式を分ける。
- `console_title`は終了時にタイトルを元へ戻す。Windowsは設定前のタイトルをAPIで取得して復元する。
  OSC方式の端末は現在のタイトルを問い合わせる確実な手段がないため空タイトルへ戻し、
  シェルが次のプロンプト描画で自身のタイトルを再設定するのに委ねる。
- `set_console_title`は復元を行わない単発設定であり、配下プロセスが書き換えたタイトルを
  呼び出し側の意図した値へ再設定する用途に使う。
"""

import contextlib
import sys
import typing

# pylint: disable=duplicate-code  # 配布物独立性を保つため同等機能を独立実装する。


@contextlib.contextmanager
def console_title(title: str, *, stream: typing.TextIO | None = None) -> typing.Iterator[None]:
    """ターミナルのウィンドウタイトルを`title`へ設定し、終了時に元へ戻す。

    ターミナルへ接続されていないときは何もしない。

    Args:
        title: 設定するウィンドウタイトル。
        stream: OSC制御文字の出力先兼ターミナル接続判定先。既定は標準エラー出力。
    """
    out = sys.stderr if stream is None else stream
    if not _isatty(out):
        yield
        return
    if sys.platform == "win32":
        with _windows_console_title(title):
            yield
    else:
        out.write(_osc_set_title(title))
        out.flush()
        try:
            yield
        finally:
            out.write(_osc_set_title(""))
            out.flush()


def set_console_title(title: str, *, stream: typing.TextIO | None = None) -> None:
    """ターミナルのウィンドウタイトルを`title`へ設定する（復元は行わない）。

    配下プロセスが書き換えたタイトルを呼び出し側の意図した値へ再設定する用途に使う。
    ターミナルへ接続されていないときは何もしない。

    Args:
        title: 設定するウィンドウタイトル。
        stream: OSC制御文字の出力先兼ターミナル接続判定先。既定は標準エラー出力。
    """
    out = sys.stderr if stream is None else stream
    if not _isatty(out):
        return
    if sys.platform == "win32":
        _set_windows_console_title(title)
    else:
        out.write(_osc_set_title(title))
        out.flush()


def _isatty(stream: typing.TextIO) -> bool:
    """`stream`がターミナルへ接続されているかを判定する。"""
    try:
        return stream.isatty()
    except (AttributeError, ValueError):
        return False


def _osc_set_title(title: str) -> str:
    """ウィンドウタイトルを設定するOSC制御シーケンスを組み立てる。

    `ESC ] 2 ; <title> BEL`。`2`はアイコン名を変えずウィンドウタイトルのみ設定する。
    """
    return f"\033]2;{title}\a"


# GetConsoleTitleW用バッファ長。本ツールが表示するタイトルは短いため固定長とする。
_WINDOWS_TITLE_BUFFER_LEN = 1024


def _windows_kernel32() -> typing.Any:
    """`ctypes.windll.kernel32`を取得する。

    `ctypes.windll`はWindows専用属性のため`getattr`経由で取得して型解析を回避する。
    """
    import ctypes  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    return getattr(ctypes, "windll").kernel32  # noqa: B009


def _set_windows_console_title(title: str) -> None:
    """Windowsコンソールのタイトルを設定する（復元は行わない）。"""
    _windows_kernel32().SetConsoleTitleW(title)


@contextlib.contextmanager
def _windows_console_title(title: str) -> typing.Iterator[None]:
    """Windowsコンソールのタイトルを設定し、終了時に設定前のタイトルへ戻す。

    `ctypes.windll`はWindows専用属性のため`getattr`経由で取得して型解析を回避する。
    """
    import ctypes  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    kernel32 = _windows_kernel32()
    buffer = ctypes.create_unicode_buffer(_WINDOWS_TITLE_BUFFER_LEN)
    kernel32.GetConsoleTitleW(buffer, len(buffer))
    original = buffer.value
    kernel32.SetConsoleTitleW(title)
    try:
        yield
    finally:
        kernel32.SetConsoleTitleW(original)
