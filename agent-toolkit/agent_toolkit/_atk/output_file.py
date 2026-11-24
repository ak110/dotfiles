from __future__ import annotations

import contextlib
import io
import json
import pathlib
import sys
from collections.abc import Callable, Generator
from typing import TextIO

from agent_toolkit._atk import outcome

AUTO_SAVE_THRESHOLD_BYTES = 48 * 1024


def save_text(text: str, create_directory: Callable[[], pathlib.Path], *, filename: str) -> pathlib.Path:
    saved = create_directory() / filename
    with saved.open("x", encoding="utf-8", newline="") as stream:
        stream.write(text)
    return saved.resolve()


def estimated_tokens(size_bytes: int) -> int:
    return -(-size_bytes // 3)


def saved_report_text(path: pathlib.Path, *, stderr: bool = False) -> str:
    with path.open(encoding="utf-8", newline="") as stream:
        lines = sum(1 for _line in stream)
    text = f"{'標準エラー保存先' if stderr else '保存先'}: {path}\n{'標準エラー行数' if stderr else '行数'}: {lines}\n"
    size = path.stat().st_size
    if size > AUTO_SAVE_THRESHOLD_BYTES:
        text += (
            f"注意: 約{estimated_tokens(size)}トークン（{size}バイト）あり、全文の読取は文脈を大きく消費する。"
            "全文を一度に読まず、rgやjqで必要な箇所を探してから、範囲を指定して読め\n"
        )
    return text


def _report_saved(path: pathlib.Path, *, stderr: bool = False) -> None:
    (sys.stderr if stderr else sys.stdout).write(saved_report_text(path, stderr=stderr))


def _emit(text: str, create_directory: Callable[[], pathlib.Path], *, stderr: bool, save: bool) -> pathlib.Path | None:
    stream = sys.stderr if stderr else sys.stdout
    if not save:
        stream.write(text)
        return None
    try:
        saved = save_text(text, create_directory, filename="stderr.txt" if stderr else "output.txt")
    except Exception as error:  # noqa: BLE001  # 保存の失敗でも本来の全量と終了を保持する
        outcome.report_warning(
            f"出力を自動保存できなかったため全量を表示する: {error}",
            next_action="表示した全量と終了コードを使い、保存先の権限・空き容量を確認してください",
        )
        stream.write(text)
    else:
        _report_saved(saved, stderr=stderr)
        return saved
    return None


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _streams_to_save(stdout_text: str, stderr_text: str) -> tuple[bool, bool]:
    stdout_size, stderr_size = _utf8_size(stdout_text), _utf8_size(stderr_text)
    if stdout_size + stderr_size <= AUTO_SAVE_THRESHOLD_BYTES:
        return False, False
    if stdout_size >= stderr_size:
        return True, stderr_size > AUTO_SAVE_THRESHOLD_BYTES
    return stdout_size > AUTO_SAVE_THRESHOLD_BYTES, True


def _report_error_events(path: pathlib.Path) -> None:
    with path.open(encoding="utf-8", newline="") as stream:
        for raw in stream:
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("kind") != "error":
                continue
            text, action = event.get("text"), event.get("next_action")
            if isinstance(text, str) and text:
                print(f"失敗: {text}")
            if isinstance(action, str) and action:
                # 照会ごとに次の操作の標識の有無が異なるため、表示では標識を1つにそろえる。
                print("次の操作: " + action.removeprefix("次の操作:").strip())


def _discard_unused(saved: pathlib.Path, discard_directory: Callable[[pathlib.Path], None] | None) -> None:
    """結果を書かずに失敗した呼び出しの空の保存先と、その生成領域を片付ける。"""
    try:
        saved.unlink()
        if discard_directory is not None:
            discard_directory(saved.parent)
    except Exception:  # noqa: BLE001  # 保存先は保持期限後の掃引で回収する
        pass


@contextlib.contextmanager
def auto_save(
    create_directory: Callable[[], pathlib.Path],
    *,
    after_save: Callable[[pathlib.Path], None] | None = None,
    force_stdout: bool = False,
    direct_stdout: bool = False,
    discard_directory: Callable[[pathlib.Path], None] | None = None,
) -> Generator[None]:
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
                if failed:
                    # 成功時の要約は結果の件数を示す。失敗した本体の出力へ適用すると、
                    # 「0件」のように失敗を結果が無いことと読める表示になる。
                    _report_error_events(saved)
                elif after_save is not None:
                    after_save(saved)
            stderr_text = stderr_buffer.getvalue()
            _emit(stderr_text, create_directory, stderr=True, save=_utf8_size(stderr_text) > AUTO_SAVE_THRESHOLD_BYTES)
        else:
            save_stdout, save_stderr = _streams_to_save(stdout_buffer.getvalue(), stderr_buffer.getvalue())
            if direct_stdout:
                total = _utf8_size(stdout_buffer.getvalue()) + _utf8_size(stderr_buffer.getvalue())
                save_stdout, save_stderr = False, bool(stderr_buffer.getvalue()) and total > AUTO_SAVE_THRESHOLD_BYTES
            emitted = _emit(stdout_buffer.getvalue(), create_directory, stderr=False, save=save_stdout)
            if emitted is not None and after_save is not None:
                after_save(emitted)
            _emit(stderr_buffer.getvalue(), create_directory, stderr=True, save=save_stderr)
