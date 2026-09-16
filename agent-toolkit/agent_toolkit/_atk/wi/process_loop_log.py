"""Claude Code agent-toolkit: `atk wi process-loop`実行時の観測ログモジュール。

目的は`process-loop`起動セッションのAWI件数・セッション全体の所要時間・
計画実行系サブエージェントの所要時間を後から分析できるよう機械記録することにある。

有効化条件は環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`のセッションに限定し、
それ以外の対話セッションでは`append`を呼んでも何も書き込まない（no-op）。
本モジュールのimport自体は環境変数の値に関わらず副作用を持たない
（判定は`append`呼び出し時に行う）。

ログパスは`platformdirs.user_state_dir("agent-toolkit", appauthor=False)`配下の
`process-wi.log`とする。排他ロックとサイズローテーションは`_file_lock.py`へ委譲する。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import platformdirs

from agent_toolkit._common.file_lock import locked_rotate_and_append as _locked_rotate_and_append

_ENABLE_ENV_VAR = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_MAX_BYTES = 1_000_000
_ABORT_FILENAME = "process-wi-abort"


def log_path() -> Path:
    """ログファイルのパスを返す。"""
    state_home = os.environ.get("XDG_STATE_HOME")
    if state_home:
        return Path(state_home) / "agent-toolkit" / "process-wi.log"
    return Path(platformdirs.user_state_dir("agent-toolkit", appauthor=False)) / "process-wi.log"


def abort_path() -> Path:
    """`atk wi process-loop`の中断要求を保持する状態ファイルのパスを返す。

    常駐処理本体（`process_loop.py`）とStop hookの双方がこのパスを使うため、
    解決処理は本モジュールを唯一の正本とする。本モジュールは常駐処理の重い依存を持たず、
    hookからのimportでも起動コストを増やさない。
    """
    return log_path().parent / _ABORT_FILENAME


def request_abort() -> Path:
    """常駐処理へ中断を要求し、要求ファイルのパスを返す。

    既に要求がある場合は内容を保ったまま同じパスを返す。
    """
    path = abort_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _is_enabled() -> bool:
    """`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`のセッションでのみ真を返す。"""
    return os.environ.get(_ENABLE_ENV_VAR) == "1"


def append(event: str, **fields: object) -> None:
    """観測イベントを1行追記する。

    `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`未設定のセッションではno-opとする。
    出力形式: `<ISO8601> event=<name> k=v k=v ...`。
    書き込み失敗（権限不足等）は呼び出し元の動作へ影響させないため無視する。
    """
    if not _is_enabled():
        return
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    line = f"{timestamp} event={event}" + (f" {rendered}" if rendered else "") + "\n"
    _locked_rotate_and_append(log_path(), line, _MAX_BYTES)
