"""Claude Code agent-toolkit: `atk wi process-loop`実行時の観測ログモジュール。

目的は`process-loop`起動セッションのAWI件数・セッション全体の所要時間・
計画実行系サブエージェントの所要時間を後から分析できるよう機械記録することにある。

有効化条件は環境変数`AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`のセッションに限定し、
それ以外の対話セッションでは`append`を呼んでも何も書き込まない（no-op）。
本モジュールのimport自体は環境変数の値に関わらず副作用を持たない
（判定は`append`呼び出し時に行う）。

ログパスは状態ディレクトリ（`agent_toolkit._common.state_paths.state_dir`）配下の
`process-wi.log`とする。排他ロックとサイズローテーションは`_common/file_lock.py`へ委譲する。
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Generator
from pathlib import Path

from filelock import FileLock

from agent_toolkit._common import state_paths as _state_paths
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.file_lock import locked_rotate_and_append as _locked_rotate_and_append
from agent_toolkit._common.process_loop_session import is_process_loop_session

_MAX_BYTES = 1_000_000
_ABORT_FILENAME = "process-wi-abort"
_INSTRUCTION_FILENAME = "process-wi-instructions"
INSTRUCTION_SEPARATOR = "\n---\n"
INSTRUCTION_MAX_CHARS = 2000


def log_path() -> Path:
    return _state_paths.state_dir() / "process-wi.log"


if __name__ == "__main__":
    print(log_path())


def abort_path() -> Path:
    return log_path().parent / _ABORT_FILENAME


def instruction_path() -> Path:
    return log_path().parent / _INSTRUCTION_FILENAME


def read_instructions() -> list[str]:
    path = instruction_path()
    try:
        body = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    return [item for item in body.split(INSTRUCTION_SEPARATOR) if item]


def append_instruction(body: str) -> tuple[bool, str]:
    """追加指示を追記し、追記したかどうかとユーザーへ示す要旨を返す。

    保持済みのいずれかと完全一致する本文は、誤操作による二重投入として追記しない。
    追記後の合計が`INSTRUCTION_MAX_CHARS`を超える投入は拒否する。
    """
    path = instruction_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock"), timeout=10):
        text = body.strip()
        if not text:
            return False, "本文が空である"
        existing = read_instructions()
        if text in existing:
            return False, "同じ本文が保持済みである"
        merged = [*existing, text]
        total = len(INSTRUCTION_SEPARATOR.join(merged))
        if total > INSTRUCTION_MAX_CHARS:
            return False, f"保持中の合計が上限{INSTRUCTION_MAX_CHARS}文字を超える（投入後{total}文字）"
        atomic_write(path, INSTRUCTION_SEPARATOR.join(merged))
        return True, f"保持中の追加指示は{len(merged)}件である"


def discard_instructions() -> int:
    path = instruction_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock"), timeout=10):
        count = len(read_instructions())
        instruction_path().unlink(missing_ok=True)
        return count


@contextlib.contextmanager
def launch_instructions(*, timeout: float = 10) -> Generator[str]:
    path = instruction_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path.with_suffix(".lock"), timeout=timeout):
        instructions = read_instructions()
        yield INSTRUCTION_SEPARATOR.join(instructions)
        if instructions:
            path.unlink(missing_ok=True)


def recent_session_ids() -> tuple[tuple[str, str], ...]:
    """終了済みの会話を、同じIDを除き新しい記録から最大5件返す。  pyfltr: allow-comment"""
    path = log_path()
    found: dict[str, str] = {}
    for candidate in (path, path.with_suffix(".log.1")):
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for line in reversed(lines):
            parts = line.split()
            if len(parts) < 3 or parts[1] != "event=session_end":
                continue
            for field in parts[2:]:
                if field.startswith("session_id=") and (identifier := field.removeprefix("session_id=")):
                    found.setdefault(identifier, parts[0])
                    if len(found) == 5:
                        return tuple(found.items())
                    break
    return tuple(found.items())


def append(event: str, *, session_id: str | None = None, **fields: object) -> None:
    """観測イベントを1行追記する。

    `AGENT_TOOLKIT_PROCESS_LOOP_SESSION=1`未設定のセッションではno-opとする。
    出力形式: `<ISO8601> event=<name> k=v k=v ...`。
    書き込み失敗（権限不足等）は呼び出し元の動作へ影響させないため無視する。
    """
    if not is_process_loop_session(session_id, os.environ):
        return
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    if session_id is not None:
        fields = {"session_id": session_id, **fields}
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    line = f"{timestamp} event={event}" + (f" {rendered}" if rendered else "") + "\n"
    _locked_rotate_and_append(log_path(), line, _MAX_BYTES)
