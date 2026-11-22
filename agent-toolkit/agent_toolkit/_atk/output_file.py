"""エージェント環境の出力保存を生成側で判断し、両ストリームを分けて保持する。"""

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
"""直接表示する標準出力と標準エラーの合計のUTF-8バイト数の上限。超える出力はファイルへ保存する。

ホストが1回のツール出力を切り詰めない量（Claude Codeへ配布する`bashOutputMaxChars`の62,000バイトと、
Codexへ配布する20,000トークン）から、実行環境が付け加える分の余裕を引いた値とする。
"""


def save_text(text: str, create_directory: Callable[[], pathlib.Path], *, filename: str) -> pathlib.Path:
    """生成側が選んだ新規ファイルへ全量を保存し、その絶対パスを返す。"""
    saved = create_directory() / filename
    with saved.open("x", encoding="utf-8", newline="") as stream:
        stream.write(text)
    return saved.resolve()


def estimated_tokens(size_bytes: int) -> int:
    """UTF-8バイト数から、文章主体の本文のトークン数を多めに見積もる（バイト数÷3の切上げ）。"""
    return -(-size_bytes // 3)


def saved_report_text(path: pathlib.Path, *, stderr: bool = False) -> str:
    """保存先と行数を返す。両ストリームは別の標識で示す。

    直接表示の上限を超える量を保存した場合だけ、推定トークン数と、範囲を選んで読む注意の行を続ける。
    量によらず保存する呼び出しの短い出力は、全文を読んでも文脈を大きく消費しないため注意を付けない。
    """
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
    """標準出力と標準エラーの保存先・行数を別の標識で表示する。"""
    (sys.stderr if stderr else sys.stdout).write(saved_report_text(path, stderr=stderr))


def _emit(text: str, create_directory: Callable[[], pathlib.Path], *, stderr: bool, save: bool) -> pathlib.Path | None:
    """`save`が偽なら保持したストリームへ、真ならファイルへ渡す。"""
    stream = sys.stderr if stderr else sys.stdout
    if not save:
        stream.write(text)
        return None
    try:
        saved = save_text(text, create_directory, filename="stderr.txt" if stderr else "output.txt")
    except Exception as error:  # noqa: BLE001  # 保存の失敗でも本来の全量と終了を保持する
        outcome.report_warning(
            f"出力を自動保存できなかったため全量を表示する: {error}",
            next_action="表示した全量と終了コードを使い、保存先の権限・空き容量を確認せよ",
        )
        stream.write(text)
    else:
        _report_saved(saved, stderr=stderr)
        return saved
    return None


def _utf8_size(text: str) -> int:
    return len(text.encode("utf-8"))


def _streams_to_save(stdout_text: str, stderr_text: str) -> tuple[bool, bool]:
    """標準出力と標準エラーのうち保存するものを返す。

    ホストは両ストリームを1つのツール出力にまとめるため、合計で直接表示の上限と比べる。
    合計が上限を超える場合は大きい方を保存し、残りも単独で上限を超えるなら保存する。
    """
    stdout_size, stderr_size = _utf8_size(stdout_text), _utf8_size(stderr_text)
    if stdout_size + stderr_size <= AUTO_SAVE_THRESHOLD_BYTES:
        return False, False
    if stdout_size >= stderr_size:
        return True, stderr_size > AUTO_SAVE_THRESHOLD_BYTES
    return stdout_size > AUTO_SAVE_THRESHOLD_BYTES, True


def _report_error_events(path: pathlib.Path) -> None:
    """保存した出力のJSON Linesから、`kind`が`error`の行の本文と次の操作を表示する。"""
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
    discard_directory: Callable[[pathlib.Path], None] | None = None,
) -> Generator[None]:
    """有限で終了する呼び出しの標準出力と標準エラーを保持し、量によらず標準出力を保存する呼び出しでは実行前に保存先を開く。

    waitの結果を消費する前に保存を準備し、保存不能なら本体を開始しない。
    例外とSystemExitでもそれまでの出力と元の終了状態を保持する。
    実行前に開いた保存先へ何も書かずに非0で終わった場合は、空の保存先と`discard_directory`へ渡す
    生成領域を片付け、保存先と行数を表示しない。読む結果が無い保存先を受信側へ渡さないためである。
    実行前に開いた保存先へ出力を書いて非0で終わった場合は、`after_save`を呼ばず、保存した出力のうち
    エラーの本文と次の操作を表示する。受信側が保存先を開かずに失敗の理由を読めるようにするためである。
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
            emitted = _emit(stdout_buffer.getvalue(), create_directory, stderr=False, save=save_stdout)
            if emitted is not None and after_save is not None:
                after_save(emitted)
            _emit(stderr_buffer.getvalue(), create_directory, stderr=True, save=save_stderr)
