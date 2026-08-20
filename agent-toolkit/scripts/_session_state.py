"""Claude Code agent-toolkit: hook間で共有するセッション状態ファイルのアクセスヘルパー。

並列ツール呼び出しで複数のhookプロセスが同一の状態ファイルへ同時書き込みする
仕様に対応するため、通常状態は排他ロック付き`update_state`、計画名記録は
独立した`claim_session_title`経由でのみ書き込む。
`read_state` → 操作 → 直接 `write_state` する従来パターンは廃止する
（先発プロセスの追加キーが後発プロセスの書き込みで消失する事象を防ぐ）。

ロック取得・解放は`_file_lock.py`（POSIX: `fcntl.flock`、Windows: `msvcrt.locking`）へ委譲する。
状態ロックの取得開始と削除は共通の調整ロックで直列化し、使用中のロックを削除しない。
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
import time
from collections.abc import Callable, Iterator
from typing import TextIO

from _file_lock import acquire_lock as _acquire_lock
from _file_lock import release_lock as _release_lock

_FILENAME_PREFIX = "claude-agent-toolkit-"
_FILENAME_SUFFIX = ".json"
_LOCK_SUFFIX = ".lock"
_TITLE_DIRECTORY_NAME = "claude-agent-toolkit-session-title"
_LOCK_COORDINATION_FILENAME = "claude-agent-toolkit-session-state-locks.lock"
_SESSION_TITLE_KEY = "last_hook_session_title"

STALE_STATE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
"""状態ファイルを回収するまでの経過時間。

セッションは終了イベントの後も`--continue`・`--resume`・`/resume`で同じ`session_id`へ戻れるため、
終了イベントを契機に削除すると再開後の記録が失われる。回収は再開の実用的な範囲を十分に超える
期間だけ更新が無かったものに限る。
"""


def state_path(session_id: str) -> pathlib.Path:
    """セッション状態ファイルのパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / f"{_FILENAME_PREFIX}{session_id}{_FILENAME_SUFFIX}"


def title_state_path(session_id: str) -> pathlib.Path:
    """計画名の再出力抑止記録のパスを返す。"""
    return pathlib.Path(tempfile.gettempdir()) / _TITLE_DIRECTORY_NAME / f"{session_id}{_FILENAME_SUFFIX}"


def _lock_path(path: pathlib.Path) -> pathlib.Path:
    """状態ファイルに対応するロックファイルのパスを返す。"""
    return path.with_name(path.name + _LOCK_SUFFIX)


@contextlib.contextmanager
def _lock_coordination() -> Iterator[None]:
    """状態ロックの取得開始と削除を直列化する。"""
    path = pathlib.Path(tempfile.gettempdir()) / _LOCK_COORDINATION_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as lock_file:
        _acquire_lock(lock_file)
        try:
            yield
        finally:
            _release_lock(lock_file)


@contextlib.contextmanager
def _locked_state(path: pathlib.Path) -> Iterator[None]:
    """状態ファイルと同じセッション別ロックを取得して処理を実行する。"""
    lock_file: TextIO
    with _lock_coordination():
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = _lock_path(path).open("a+", encoding="utf-8")
        try:
            _acquire_lock(lock_file)
        except OSError:
            lock_file.close()
            raise
    try:
        yield
    finally:
        _release_lock(lock_file)
        lock_file.close()


def sweep_stale_states(
    *,
    keep_session_id: str | None = None,
    now: float | None = None,
    max_age_seconds: float = STALE_STATE_MAX_AGE_SECONDS,
) -> int:
    """期限を過ぎた状態ファイルとロックファイルを回収し、削除した状態ファイル数を返す。

    `keep_session_id`を渡すと、当該セッションの状態ファイルとロックファイルを
    期限にかかわらず回収対象から除く。セッション終了イベントを契機に呼ぶ場合、
    当該セッションは`--continue`・`--resume`・`/resume`で戻れる一方、
    更新時刻は最後の記録時点のままとなり、長く記録が無いだけで削除されうるため。

    状態ファイルは更新時刻が期限を超えた場合に、対のロックファイルとともに削除する。
    計画名の再出力抑止記録は独立した保存先を使うため、本走査の対象に含めない。
    対応する状態ファイルが無いロックファイルは、ロック自身の更新時刻で判定する。
    ロックは`open(path, "a+")`で開くだけで内容を書かないため更新時刻が進まず、
    当該値は作成時刻に等しい。対の状態ファイルがある間は、そちらの更新時刻のほうが
    稼働の有無をよく表すため、ロック単独では判定しない。

    `update_state`はロックを先に作成して状態ファイルを後段で生成するため、
    対応する状態ファイルが無いロックはセッション開始直後にも生じる。
    期限を課さずに回収すると稼働中の排他を壊すため、孤立ロックにも同じ期限を課す。

    個別の削除失敗は無視して走査を継続する。
    """
    directory = pathlib.Path(tempfile.gettempdir())
    threshold = (time.time() if now is None else now) - max_age_seconds
    kept_name = state_path(keep_session_id).name if keep_session_id else None
    removed = 0
    for path in directory.glob(f"{_FILENAME_PREFIX}*{_FILENAME_SUFFIX}"):
        if path.name == kept_name or not _is_stale(path, threshold):
            continue
        if _collect_stale_state(path, threshold):
            removed += 1
    for lock_path in directory.glob(f"{_FILENAME_PREFIX}*{_FILENAME_SUFFIX}{_LOCK_SUFFIX}"):
        if kept_name is not None and lock_path.name == kept_name + _LOCK_SUFFIX:
            continue
        if (lock_path.parent / lock_path.name[: -len(_LOCK_SUFFIX)]).exists():
            continue
        _collect_stale_orphan_lock(lock_path, threshold)
    return removed


def _collect_stale_state(path: pathlib.Path, threshold: float) -> bool:
    """期限切れ状態と対のロックを、ロック利用者がいない間に回収する。"""
    lock_path = _lock_path(path)
    try:
        with _lock_coordination():
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                _acquire_lock(lock_file)
                try:
                    if not _is_stale(path, threshold):
                        return False
                    removed = _unlink_quietly(path)
                finally:
                    _release_lock(lock_file)
            if removed:
                _unlink_quietly(lock_path)
            return removed
    except OSError:
        return False


def _collect_stale_orphan_lock(lock_path: pathlib.Path, threshold: float) -> None:
    """期限切れの孤立ロックを、ロック利用者がいない間に回収する。"""
    path = lock_path.with_name(lock_path.name[: -len(_LOCK_SUFFIX)])
    try:
        with _lock_coordination():
            if path.exists() or not _is_stale(lock_path, threshold):
                return
            with lock_path.open("a+", encoding="utf-8") as lock_file:
                _acquire_lock(lock_file)
                try:
                    if path.exists() or not _is_stale(lock_path, threshold):
                        return
                finally:
                    _release_lock(lock_file)
            _unlink_quietly(lock_path)
    except OSError:
        return


def _is_stale(path: pathlib.Path, threshold: float) -> bool:
    """更新時刻が閾値より古い場合に真を返す。取得できない場合は偽を返す。"""
    try:
        return path.stat().st_mtime < threshold
    except OSError:
        return False


def _unlink_quietly(path: pathlib.Path) -> bool:
    """削除に成功した場合だけ真を返す。失敗は無視する。"""
    try:
        path.unlink()
    except OSError:
        return False
    return True


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
    try:
        with _locked_state(path):
            current = _read_locked(path)
            updated = mutator(current)
            if updated is None:
                return False
            _atomic_write(path, json.dumps(updated, ensure_ascii=False))
            return True
    except OSError:
        return False


def claim_session_title(session_id: str, title: str) -> bool:
    """計画名を未記録のセッションへ一度だけ保存する。"""
    if not isinstance(session_id, str) or not session_id or not isinstance(title, str) or not title:
        return False
    path = title_state_path(session_id)
    try:
        with _locked_state(path):
            current = _read_title_locked(path)
            if current is None or current:
                return False
            _atomic_write(path, json.dumps({_SESSION_TITLE_KEY: title}, ensure_ascii=False))
            return True
    except OSError:
        return False


def delete_state(session_id: str) -> bool:
    """有効なセッションの通常状態と対のロックを安全に削除する。"""
    if not isinstance(session_id, str) or not session_id:
        return False
    return _delete_paths_and_locks((state_path(session_id),))


def clear_session_state(session_id: str) -> bool:
    """会話破棄時に通常状態と計画名記録及び双方のロックを削除する。"""
    if not isinstance(session_id, str) or not session_id:
        return False
    return _delete_paths_and_locks((state_path(session_id), title_state_path(session_id)))


def _delete_paths_and_locks(paths: tuple[pathlib.Path, ...]) -> bool:
    """指定状態と各ロックを、ロック利用者がいない間に削除する。"""
    succeeded = True
    try:
        with _lock_coordination():
            for path in paths:
                lock_path = _lock_path(path)
                if not path.exists() and not lock_path.exists():
                    continue
                removed = False
                try:
                    with lock_path.open("a+", encoding="utf-8") as lock_file:
                        _acquire_lock(lock_file)
                        try:
                            path.unlink(missing_ok=True)
                            removed = True
                        finally:
                            _release_lock(lock_file)
                except OSError:
                    succeeded = False
                if removed and not _unlink_quietly(lock_path):
                    succeeded = False
    except OSError:
        return False
    return succeeded


def _read_locked(path: pathlib.Path) -> dict:
    """ロック取得後に状態ファイルを読み込む。不在・破損時は空辞書を返す。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read_title_locked(path: pathlib.Path) -> dict | None:
    """計画名記録を読み、不在時は空辞書、破損・読取失敗時は`None`を返す。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    title = data.get(_SESSION_TITLE_KEY)
    if set(data) != {_SESSION_TITLE_KEY} or not isinstance(title, str) or not title:
        return None
    return data


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
