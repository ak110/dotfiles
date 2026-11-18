"""保持期限の掃引のように一定の間隔の実行で十分な処理を、間隔内の起動では省くための記録。"""

from __future__ import annotations

import contextlib
import pathlib
import time

from agent_toolkit._common.atomic_file import atomic_write


def claim(record: pathlib.Path, *, interval_seconds: float, now: float | None = None) -> bool:
    """前回の記録から`interval_seconds`が過ぎていれば今回の時刻を記録して真を返し、間隔内なら偽を返す。

    同時に終了した複数のプロセスがそろって処理しないよう、処理の前に記録する。
    処理が途中で失敗した場合も次の実行は間隔の後になるため、期限より十分短い間隔で使う。
    記録の欠落・破損、記録が未来の時刻を指す場合（時計の巻き戻し）、記録を書けない場合は処理を省かない。
    """
    current = time.time() if now is None else now
    try:
        last = float(record.read_text(encoding="ascii"))
    except OSError, ValueError:
        last = None
    if last is not None and 0 <= current - last < interval_seconds:
        return False
    with contextlib.suppress(OSError):
        record.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(record, f"{current!r}\n")
    return True
