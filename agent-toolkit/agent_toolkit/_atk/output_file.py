"""エージェント環境の出力保存を生成側で判断し、両ストリームを分けて保持する。"""

from __future__ import annotations

import contextlib
import io
import pathlib
import sys
from collections.abc import Callable, Iterator
from typing import TextIO

from agent_toolkit._atk import outcome

AUTO_SAVE_THRESHOLD_BYTES = 16 * 1024
"""通常出力を自動保存するUTF-8バイト数の境界。短い出力は直接表示する。"""


def save_text(text: str, create_directory: Callable[[], pathlib.Path], *, filename: str) -> pathlib.Path:
    """生成側が選んだ新規ファイルへ全量を保存し、その絶対パスを返す。"""
    saved = create_directory() / filename
    with saved.open("x", encoding="utf-8", newline="") as stream:
        stream.write(text)
    return saved.resolve()


def _report_saved(path: pathlib.Path, *, stderr: bool = False) -> None:
    """標準出力と標準エラーの保存先・行数を別の標識で表示する。"""
    with path.open(encoding="utf-8", newline="") as stream:
        lines = sum(1 for _line in stream)
    destination = sys.stderr if stderr else sys.stdout
    print(f"{'標準エラー保存先' if stderr else '保存先'}: {path}", file=destination)
    print(f"{'標準エラー行数' if stderr else '行数'}: {lines}", file=destination)


def _emit(text: str, create_directory: Callable[[], pathlib.Path], *, stderr: bool) -> pathlib.Path | None:
    """短い出力は保持したストリームへ、長い出力はファイルへ渡す。"""
    stream = sys.stderr if stderr else sys.stdout
    if len(text.encode("utf-8")) <= AUTO_SAVE_THRESHOLD_BYTES:
        stream.write(text)
        return None
    try:
        saved = save_text(text, create_directory, filename="stderr.txt" if stderr else "output.txt")
    except Exception as error:  # noqa: BLE001  # 保存の失敗でも本来の全量と終了を保持する
        outcome.report_warning(
            f"出力を自動保存できなかったため全量を表示する: {error}",
            next_action="表示した全量と終了コードを使い、保存先の権限・空き容量を確認する",
        )
        stream.write(text)
    else:
        _report_saved(saved, stderr=stderr)
        return saved
    return None


@contextlib.contextmanager
def auto_save(
    create_directory: Callable[[], pathlib.Path],
    *,
    after_save: Callable[[pathlib.Path], None] | None = None,
    force_stdout: bool = False,
) -> Iterator[None]:
    """有限終了の両出力を保持し、ファイル消費のあるstdoutは実行前に保存先を開く。

    waitの結果を消費する前に保存を準備し、保存不能なら本体を開始しない。
    例外とSystemExitでもそれまでの出力と元の終了状態を保持する。
    """
    stdout_buffer, stderr_buffer = io.StringIO(), io.StringIO()
    saved: pathlib.Path | None = None
    stdout_stream: TextIO = stdout_buffer
    if force_stdout:
        try:
            saved = (create_directory() / "output.txt").resolve()
            stdout_stream = saved.open("x", encoding="utf-8", newline="", buffering=1)
        except Exception as error:  # noqa: BLE001  # 未受領の結果を消費する前に停止する
            outcome.report_failure(
                f"出力保存の準備に失敗したため呼び出しを開始しない: {error}",
                next_action="managed-tempの権限・空き容量を確認して同じコマンドを再実行する",
            )
            raise SystemExit(1) from error
    try:
        with contextlib.redirect_stdout(stdout_stream), contextlib.redirect_stderr(stderr_buffer):
            yield
    finally:
        if saved is not None:
            stdout_stream.close()
            _report_saved(saved)
            if after_save is not None:
                after_save(saved)
        else:
            emitted = _emit(stdout_buffer.getvalue(), create_directory, stderr=False)
            if emitted is not None and after_save is not None:
                after_save(emitted)
        _emit(stderr_buffer.getvalue(), create_directory, stderr=True)
