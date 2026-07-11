"""Claude Code agent-toolkit: `atk fb process-loop`実行時の観測ログモジュール。

目的は`process-loop`起動セッションのフィードバック件数・セッション全体の所要時間・
plan-impl系サブエージェントの所要時間を後から分析できるよう機械記録することにある。

有効化条件は環境変数`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`のセッションに限定し、
それ以外の対話セッションでは`append`を呼んでも何も書き込まない（no-op）。
本モジュールのimport自体は環境変数の値に関わらず副作用を持たない
（判定は`append`呼び出し時に行う）。

ログパスは`platformdirs.user_state_dir("agent-toolkit", appauthor=False)`配下の
`process-feedbacks.log`とする。排他ロックとサイズローテーションは`_file_lock.py`へ委譲する。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import platformdirs
from _file_lock import acquire_lock as _acquire_lock
from _file_lock import release_lock as _release_lock
from _file_lock import rotate_if_needed as _rotate_if_needed

_ENABLE_ENV_VAR = "DOTFILES_AUTONOMOUS_EXIT_REQUIRED"
_MAX_BYTES = 1_000_000


def log_path() -> Path:
    """ログファイルのパスを返す。"""
    return Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "process-feedbacks.log"


def _is_enabled() -> bool:
    """`DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`のセッションでのみ真を返す。"""
    return os.environ.get(_ENABLE_ENV_VAR) == "1"


def append(event: str, **fields: object) -> None:
    """観測イベントを1行追記する。

    `DOTFILES_AUTONOMOUS_EXIT_REQUIRED=1`未設定のセッションではno-opとする。
    出力形式: `<ISO8601> event=<name> k=v k=v ...`。
    書き込み失敗（権限不足等）は呼び出し元の動作へ影響させないため無視する。
    """
    if not _is_enabled():
        return
    path = log_path()
    _rotate_if_needed(path, _MAX_BYTES)
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    line = f"{timestamp} event={event}" + (f" {rendered}" if rendered else "") + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / (path.name + ".lock")
        with open(lock_path, "a+", encoding="utf-8") as lock_file:  # noqa: SIM115 -- ロック保持のため
            _acquire_lock(lock_file)
            try:
                with path.open("a", encoding="utf-8") as f:
                    f.write(line)
            finally:
                _release_lock(lock_file)
    except OSError:
        return
