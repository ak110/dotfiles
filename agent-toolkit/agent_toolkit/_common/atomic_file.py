"""Claude Code agent-toolkit: 一時ファイル経由の原子的書き込み共通ヘルパー。

`_common/session_state.py`のセッション状態書き込みと`_atk/review_table.py`のレビュー指摘管理表の書き込みが
同一の一時ファイル経由`os.replace`パターンを個別に持っていたため、本モジュールへ集約する。
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from pathlib import Path

_LOGGER = logging.getLogger(__name__)
# 短命な読取ハンドルとの競合だけを待つ。保存やbackendの起動は繰り返さない。
# 表示更新を長く止めないため、待つ時間の合計は1秒未満に収める。
_REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.04, 0.08, 0.16)


def _replace(source: str, destination: Path) -> None:
    for attempt in range(len(_REPLACE_RETRY_DELAYS) + 1):
        try:
            os.replace(source, destination)
            return
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32) or attempt == len(_REPLACE_RETRY_DELAYS):
                # 呼出側の既存診断ログにも、一時ファイルを回収する前の置換元・先を残す。
                error.add_note(f"atomic replace source={source} destination={destination}")
                _LOGGER.exception("原子的な置換に失敗: source=%s destination=%s", source, destination)
                raise
            time.sleep(_REPLACE_RETRY_DELAYS[attempt])


def atomic_write(path: Path, content: str, *, fsync: bool = False) -> None:
    """同一ディレクトリの一時ファイル経由で`content`を原子的に書き込む。

    一時ファイル作成→書き込み→（`fsync=True`時はディスクへの同期→）`os.replace`の順で実行し、
    書き込み中断時は旧ファイル内容が残るよう保証する。
    `fsync`は呼び出し元の耐障害性要件に応じて指定する（指定しなければ無効）。
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
        _replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
