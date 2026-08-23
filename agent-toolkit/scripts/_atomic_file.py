"""Claude Code agent-toolkit: 一時ファイル経由の原子的書き込み共通ヘルパー。

`_session_state.py`のセッション状態書き込みと`_review_table.py`のレビュー表書き込みが
同一の一時ファイル経由`os.replace`パターンを個別に持っていたため、本モジュールへ集約する。
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path


def atomic_write(path: Path, content: str, *, fsync: bool = False) -> None:
    """同一ディレクトリの一時ファイル経由で`content`を原子的に書き込む。

    一時ファイル作成→書き込み→（`fsync=True`時はディスクへの同期→）`os.replace`の順で実行し、
    書き込み中断時は旧ファイル内容が残るよう保証する。
    `fsync`は呼び出し元の耐障害性要件に応じて指定する（既定は無効）。
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            if fsync:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
