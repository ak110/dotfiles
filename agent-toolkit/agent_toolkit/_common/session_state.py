"""Claude Code agent-toolkit: hook間で共有するセッション状態ファイルのアクセスヘルパー。

並列ツール呼び出しで複数のhookプロセスが同一の状態ファイルへ同時書き込みする
仕様に対応するため、状態は排他ロック付き`update_state`経由で書き込む。
`read_state` → 操作 → 直接 `write_state` する従来パターンは廃止する
（先発プロセスの追加キーが後発プロセスの書き込みで消失する事象を防ぐ）。

ロック取得・解放は`file_lock.py`（POSIX: `fcntl.flock`、Windows: `msvcrt.locking`）へ委譲する。
状態ディレクトリの固定1個のロックで、短いJSONの更新と回収を直列化する。
ロックは削除せず、書き込みは同一ディレクトリの一時ファイル経由`os.replace`で反映する。

dotfilesでの開発用パス規則は`docs/development/internals-session-state-common.md`の
「状態ファイルの設計」節に記載がある。
"""

from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
import tempfile
import time
from collections.abc import Callable, Generator

from agent_toolkit._common import session_activity, state_paths
from agent_toolkit._common import transcript as _transcript
from agent_toolkit._common.atomic_file import atomic_write as _atomic_write
from agent_toolkit._common.file_lock import acquire_lock as _acquire_lock
from agent_toolkit._common.file_lock import release_lock as _release_lock

_FILENAME_PREFIX = "claude-agent-toolkit-"
_FILENAME_SUFFIX = ".json"
_LOCK_SUFFIX = ".lock"
_INHERITED_FROM_SESSION_KEY = "inherited_from_session_id"
_WARN_NOTICE_COUNTS_KEY = "warn_notice_counts"
_TRANSCRIPT_SESSION_ID_KEYS = ("sessionId", "session_id")

STALE_STATE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
"""状態ファイルを回収するまでの経過時間。

セッションは終了イベントの後も`--continue`・`--resume`・`/resume`で同じ`session_id`へ戻れるため、
終了イベントを契機に削除すると再開後の記録が失われる。回収は再開の実用的な範囲を十分に超える
期間だけ更新が無かったものに限る。
"""


def state_path(session_id: str) -> pathlib.Path:
    return state_paths.state_dir() / "hook-sessions" / f"{_FILENAME_PREFIX}{session_id}{_FILENAME_SUFFIX}"


def _lock_path(path: pathlib.Path) -> pathlib.Path:
    return path.parent / ".state.lock"


@contextlib.contextmanager
def _locked_state(path: pathlib.Path) -> Generator[None]:
    """状態rootの共有ロックを取得し、JSONの更新区間を実行する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with _lock_path(path).open("a+", encoding="utf-8") as lock_file:
        _acquire_lock(lock_file)
        try:
            yield
        finally:
            _release_lock(lock_file)


def sweep_stale_states(
    *,
    keep_session_id: str | None = None,
    now: float | None = None,
    max_age_seconds: float = STALE_STATE_MAX_AGE_SECONDS,
) -> int:
    """期限を過ぎた状態ファイルを回収し、削除した件数を返す。

    `keep_session_id`を渡すと、指定したセッションの状態を
    期限にかかわらず回収対象から除く。セッション終了イベントを契機に呼ぶ場合、
    そのセッションは`--continue`・`--resume`・`/resume`で戻れる一方、
    更新時刻は最後の記録時点のままとなり、長く記録が無いだけで削除されうるため。

    更新時刻が期限を超えた場合に削除する。
    固定1個の共有ロックは保持する。

    個別の削除失敗は無視して走査を継続する。
    """
    directory = state_path("unused").parent
    threshold = (time.time() if now is None else now) - max_age_seconds
    kept_state_name = state_path(keep_session_id).name if keep_session_id else None
    stale = [
        path
        for path in directory.glob(f"{_FILENAME_PREFIX}*{_FILENAME_SUFFIX}")
        if path.name != kept_state_name and _is_stale(path, threshold)
    ]
    if not stale:
        return 0
    # 相談が開いたまま入力待ちを続けても、自動振り返りの抑止を期限で失わない。
    # 生存する会話の全件確認は、期限切れの候補がある場合だけ行う。
    consultations = {
        state_path(session_id).name
        for session_id in session_activity.living_root_session_ids()
        if read_state(session_id).get("add_awi_skill_invoked") is True
    }
    removed = 0
    for path in stale:
        if path.name not in consultations and _collect_stale_state(path, threshold):
            removed += 1
    return removed


def _collect_stale_state(path: pathlib.Path, threshold: float) -> bool:
    """期限切れ状態を、ロックを使うプロセスがいない間に回収する。対応するロックは削除しない。"""
    try:
        with _locked_state(path):
            if not _is_stale(path, threshold):
                return False
            return _unlink_quietly(path)
    except OSError:
        return False


def _is_stale(path: pathlib.Path, threshold: float) -> bool:
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
    if not isinstance(session_id, str) or not session_id:
        return {}
    path = state_path(session_id)
    try:
        if not path.exists() and (pathlib.Path(tempfile.gettempdir()) / path.name).exists():
            with _locked_state(path):
                _migrate_legacy_state(path)
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def inherit_state_from_transcript(session_id: str, transcript_path: str) -> bool:
    if not isinstance(session_id, str) or not session_id or not isinstance(transcript_path, str) or not transcript_path:
        return False
    target = state_path(session_id)
    try:
        target.stat()
        return False
    except FileNotFoundError:
        pass
    except OSError:
        return False

    try:
        if (pathlib.Path(tempfile.gettempdir()) / target.name).exists():
            read_state(session_id)
            if target.exists():
                return False
        candidates: dict[str, dict] = {}
        for line in _transcript.read_transcript_lines(transcript_path) or []:
            try:
                entry = json.loads(line)
            except json.JSONDecodeError, ValueError:
                continue
            if not isinstance(entry, dict):
                continue
            predecessor = next(
                (
                    value
                    for key in _TRANSCRIPT_SESSION_ID_KEYS
                    if isinstance((value := entry.get(key)), str) and value and value != session_id
                ),
                None,
            )
            if predecessor is None or predecessor in candidates:
                continue
            predecessor_path = state_path(predecessor)
            if predecessor_path.parent != target.parent:
                continue
            try:
                inherited = json.loads(predecessor_path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError, ValueError:
                continue
            if isinstance(inherited, dict):
                candidates[predecessor] = inherited
        if len(candidates) != 1:
            return False
        with _locked_state(target):
            _migrate_legacy_state(target)
            if target.exists():
                return False
            predecessor, inherited = next(iter(candidates.items()))
            updated = {**inherited, _INHERITED_FROM_SESSION_KEY: predecessor}
            _atomic_write(target, json.dumps(updated, ensure_ascii=False))
            return True
    except OSError:
        return False


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


PROCESS_WI_STATE_KEY = "process_wi_skill_invoked"
"""作業`process-wi`の実行中（自律モード）を表す状態の鍵。

ユーザーの入力（入口`atk-mode`への作業の名指し）と、`atk run-skill`・`atk wi process-loop`の起動入力からだけ立てる。
エージェントによる入口の起動からは立てない（エージェントが自分の判断で自律モードへ入れなくする）。
`atk agents-exit-session`の起動で偽へ戻す。
"""


def set_process_wi_invoked(state: dict) -> dict | None:
    if state.get(PROCESS_WI_STATE_KEY, False):
        return None
    state[PROCESS_WI_STATE_KEY] = True
    return state


def increment_warn_notice_count(session_id: str, key: str) -> int:
    """警告原因の発生件数を排他ロック下で増やし、増加後の値を返す。"""
    count = 1

    def _increment(current: dict) -> dict:
        nonlocal count
        counts = current.get(_WARN_NOTICE_COUNTS_KEY)
        if not isinstance(counts, dict):
            counts = {}
        previous = counts.get(key)
        count = previous + 1 if isinstance(previous, int) and previous >= 0 else 1
        counts[key] = count
        current[_WARN_NOTICE_COUNTS_KEY] = counts
        return current

    update_state(session_id, _increment)
    return count


def delete_state(session_id: str) -> bool:
    """有効なセッションの通常状態を安全に削除する。対応するロックは削除しない。"""
    if not isinstance(session_id, str) or not session_id:
        return False
    path = state_path(session_id)
    return _delete_state_paths((path, pathlib.Path(tempfile.gettempdir()) / path.name))


def clear_session_state(session_id: str) -> bool:
    """会話破棄時に状態を削除する。対応するロックは削除しない。"""
    if not isinstance(session_id, str) or not session_id:
        return False
    return delete_state(session_id)


def _delete_state_paths(paths: tuple[pathlib.Path, ...]) -> bool:
    """指定状態を、ロックを使うプロセスがいない間に削除する。対応するロックは削除しない。"""
    succeeded = True
    try:
        with _locked_state(paths[0]):
            for path in paths:
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    succeeded = False
    except OSError:
        succeeded = False
    return succeeded


def _read_locked(path: pathlib.Path) -> dict:
    """ロック取得後に状態ファイルを読み込む。不在・破損時は空辞書を返す。"""
    _migrate_legacy_state(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _migrate_legacy_state(path: pathlib.Path) -> None:
    """現行状態が無い場合だけ、同じsessionの旧JSONを排他下で移す。"""
    if path.exists():
        return
    legacy = pathlib.Path(tempfile.gettempdir()) / path.name
    if not legacy.exists():
        return
    with legacy.with_name(legacy.name + _LOCK_SUFFIX).open("a+", encoding="utf-8") as legacy_lock:
        _acquire_lock(legacy_lock)
        try:
            try:
                content = legacy.read_text(encoding="utf-8")
                value = json.loads(content)
            except FileNotFoundError, json.JSONDecodeError, ValueError:
                return
            if isinstance(value, dict):
                _atomic_write(path, content)
                legacy.unlink(missing_ok=True)
        finally:
            _release_lock(legacy_lock)


def main(argv: list[str] | None = None) -> int:
    """Function hooksへ命名に必要な通常状態を渡し、更新を既存の排他制御で保存する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session-id", required=True)
    fields = parser.add_mutually_exclusive_group()
    fields.add_argument("--kind", choices=("process-wi", "single-lane"))
    fields.add_argument("--title")
    args = parser.parse_args(argv)
    if not args.session_id or any(
        char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in args.session_id
    ):
        parser.error("session-idは英数字・アンダースコア・ハイフンで指定する")
    if args.kind is not None or args.title is not None:
        key = "session_title_kind" if args.kind is not None else "last_hook_session_title"
        value = args.kind if args.kind is not None else args.title
        if not value or not update_state(args.session_id, lambda state: {**state, key: value}):
            print("セッション名の状態を保存できない", file=sys.stderr)
            return 1
    state = read_state(args.session_id)
    print(
        json.dumps(
            {key: state.get(key) for key in ("session_title_kind", "last_hook_session_title", "current_plan_file_path")},
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
