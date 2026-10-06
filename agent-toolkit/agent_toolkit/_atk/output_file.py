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


def saved_report_text(path: pathlib.Path, *, stderr: bool = False) -> str:
    """保存先・行数の2行を返す。標準出力と標準エラーは別の標識で示す。

    保存後の表示が直接表示の上限の内訳へこの2行を含めるため、表示と同じ文字列をここだけで組み立てる。
    """
    with path.open(encoding="utf-8", newline="") as stream:
        lines = sum(1 for _line in stream)
    return f"{'標準エラー保存先' if stderr else '保存先'}: {path}\n{'標準エラー行数' if stderr else '行数'}: {lines}\n"


def _report_saved(path: pathlib.Path, *, stderr: bool = False) -> None:
    """標準出力と標準エラーの保存先・行数を別の標識で表示する。"""
    (sys.stderr if stderr else sys.stdout).write(saved_report_text(path, stderr=stderr))


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


def _discard_unused(saved: pathlib.Path, discard_directory: Callable[[pathlib.Path], None] | None) -> None:
    """結果を書かずに失敗した呼び出しの空の保存先と、その生成領域を片付ける。"""
    try:
        saved.unlink()
        if discard_directory is not None:
            discard_directory(saved.parent)
    except Exception as error:  # noqa: BLE001  # 片付けの失敗で本来の終了状態を変えない
        outcome.report_warning(
            f"結果の無い出力保存先を片付けられなかった: {saved.parent}: {error}",
            next_action="対応不要（終了状態は変えていない）。残った領域は最終更新から7日で自動削除される",
        )


@contextlib.contextmanager
def auto_save(
    create_directory: Callable[[], pathlib.Path],
    *,
    after_save: Callable[[pathlib.Path], None] | None = None,
    force_stdout: bool = False,
    discard_directory: Callable[[pathlib.Path], None] | None = None,
) -> Iterator[None]:
    """有限終了の両出力を保持し、ファイル消費のあるstdoutは実行前に保存先を開く。

    waitの結果を消費する前に保存を準備し、保存不能なら本体を開始しない。
    例外とSystemExitでもそれまでの出力と元の終了状態を保持する。
    実行前に開いた保存先へ何も書かずに非0で終わった場合は、空の保存先と`discard_directory`へ渡す
    生成領域を片付け、保存先と行数を表示しない。読む結果が無い保存先を受信側へ渡さないためである。
    """
    stdout_buffer, stderr_buffer = io.StringIO(), io.StringIO()
    saved: pathlib.Path | None = None
    stdout_stream: TextIO = stdout_buffer
    failed = False
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
    except SystemExit as error:
        failed = error.code not in (None, 0)
        raise
    except BaseException:
        failed = True
        raise
    finally:
        if saved is not None:
            empty = stdout_stream.tell() == 0
            stdout_stream.close()
            if failed and empty:
                _discard_unused(saved, discard_directory)
            else:
                _report_saved(saved)
                if after_save is not None:
                    after_save(saved)
        else:
            emitted = _emit(stdout_buffer.getvalue(), create_directory, stderr=False)
            if emitted is not None and after_save is not None:
                after_save(emitted)
        _emit(stderr_buffer.getvalue(), create_directory, stderr=True)
