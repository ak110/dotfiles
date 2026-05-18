"""Claude Code agent-toolkit: hook間で共有するセッション状態ファイルのアクセスヘルパー。

並列ツール呼び出しで複数のhookプロセスが同一の状態ファイルへ同時書き込みする
仕様に対応するため、書き込みは排他ロック付き`update_state`ヘルパー経由でのみ実施する。
`read_state` → 操作 → 直接 `write_state` する従来パターンは廃止する
（先発プロセスの追加キーが後発プロセスの書き込みで消失する事象を防ぐ）。

ロック実装はPython標準ライブラリのみで、POSIXは`fcntl.flock`、Windowsは`msvcrt.locking`を使う。
書き込みは同一ディレクトリの一時ファイル経由`os.replace`でアトミックに反映する。

パス規則は`agent-toolkit/skills/agent-standards/references/claude-hooks.md`の
「セッション状態ファイル」節に記載がある。
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import tempfile
from collections.abc import Callable
from typing import IO

_FILENAME_PREFIX = "claude-agent-toolkit-"
_FILENAME_SUFFIX = ".json"


def state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / f"{_FILENAME_PREFIX}{session_id}{_FILENAME_SUFFIX}"


def read_state(session_id: str) -> dict:
    """セッション状態を読む。session_idが無効・不在・破損時は空辞書を返す。"""
    if not isinstance(session_id, str) or not session_id:
        return {}
    try:
        data = json.loads(state_path(session_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update_state(session_id: str, mutator: Callable[[dict], dict | None]) -> bool:
    """セッション状態を排他ロック下で読み取り・変更・書き込みする。

    `mutator`は現在の状態辞書を受け取り、書き込むべき新しい辞書を返す。
    変更不要時は`None`を返すと書き込みをスキップする。
    実際に書き込みを実施した場合は`True`、それ以外は`False`を返す。

    並列ツール呼び出しで同一セッションのhookが同時起動する場合に備え、
    ロックファイル経由でOS別排他ロックを取得する（POSIX: `fcntl.flock(LOCK_EX)`、
    Windows: `msvcrt.locking(LK_LOCK, 1)`）。
    書き込みは同一ディレクトリの一時ファイルへ出力後、`os.replace`でアトミックに反映する。

    `session_id`が無効、書き込みに失敗した場合はベストエフォートで例外を抑制する。
    """
    if not isinstance(session_id, str) or not session_id:
        return False
    path = state_path(session_id)
    lock_path = path.parent / (path.name + ".lock")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_file:  # noqa: SIM115 -- ロック保持のため
            _acquire_lock(lock_file)
            try:
                current = _read_locked(path)
                updated = mutator(current)
                if updated is None:
                    return False
                _atomic_write(path, json.dumps(updated, ensure_ascii=False))
                return True
            finally:
                _release_lock(lock_file)
    except OSError:
        return False


def _read_locked(path: pathlib.Path) -> dict:
    """ロック取得後に状態ファイルを読み込む。不在・破損時は空辞書を返す。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: pathlib.Path, content: str) -> None:
    """同一ディレクトリの一時ファイル経由でアトミック書き込みする。

    一時ファイル作成→書き込み→`os.replace`の順で実行し、書き込み中断時は
    旧ファイル内容が残るよう保証する。
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_name, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


if os.name == "nt":
    import msvcrt  # type: ignore[import-not-found]  # pylint: disable=import-error

    def _acquire_lock(lock_file: IO) -> None:
        """Windows: バイト範囲ロックを取得する。

        空ファイルでも`LK_LOCK`はブロッキング取得可能。
        `LK_LOCK`は最大10秒で再試行する仕様のため、長時間の競合に備えて
        OSError時はループで再試行する。
        """
        lock_file.seek(0)
        while True:
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                return
            except OSError:
                continue

    def _release_lock(lock_file: IO) -> None:
        """Windows: バイト範囲ロックを解放する。"""
        lock_file.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _acquire_lock(lock_file: IO) -> None:
        """POSIX: ファイル全体への排他ロックを取得する。"""
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)

    def _release_lock(lock_file: IO) -> None:
        """POSIX: ファイル全体への排他ロックを解放する。"""
        with contextlib.suppress(OSError):
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
