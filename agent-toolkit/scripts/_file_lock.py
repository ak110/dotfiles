"""Claude Code agent-toolkit: ファイルロック・ログローテーションの共通ヘルパー。

`_session_state.py`のセッション状態排他ロックと`_stop_gate.py`の常時ログローテーションが
同一実装を個別に持っていたため、本モジュールへ集約する。
POSIX/NT両対応のロック取得・解放と、サイズ超過時の1世代ローテーションを提供する。
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import IO

PLAN_LOCK_IGNORE_PATTERN = "/plans/**/*.lock"
"""計画保存先リポジトリでagent-toolkitの管理ロックを除外するパターン。

`ensure_plan_lock_ignored`が受理するロックはリポジトリroot直下の`plans/`配下に限るため、
除外範囲も同じ範囲へ合わせる。書き込み先はGit common directory配下の`info/exclude`であり、
Gitの版管理の対象外にある。除外設定は利用者のcloneごとに閉じ、他のcloneとは共有されない。
"""

_GITIGNORE_UPDATE_LOCK = "agent-toolkit-plan-lock-gitignore.lock"


def ensure_plan_lock_ignored(lock_path: Path) -> bool:
    """`plans/`配下の管理ロックをリポジトリの`info/exclude`へ追加する。

    Gitリポジトリ外または`plans/`外のロックは変更しない。
    変更した場合だけ真を返す。
    版管理の対象である`.gitignore`ではなく`info/exclude`へ書くため、除外の保証が
    commitとpushを伴わない。既に`.gitignore`へ同じパターンを持つcloneでも、
    当該行は除去せずそのまま残す。
    """
    resolved_lock = lock_path.expanduser().resolve(strict=False)
    repository = _repository_for_plan_lock(resolved_lock)
    if repository is None:
        return False
    _, git_directory = repository
    exclude_path = _git_common_directory(git_directory) / "info" / "exclude"
    pattern = PLAN_LOCK_IGNORE_PATTERN.encode("utf-8")
    update_lock = git_directory / _GITIGNORE_UPDATE_LOCK
    with update_lock.open("a+", encoding="utf-8") as lock_file:
        acquire_lock(lock_file)
        try:
            current = exclude_path.read_bytes() if exclude_path.exists() else b""
            if pattern in current.splitlines():
                return False
            separator = b"" if not current or current.endswith((b"\n", b"\r")) else b"\n"
            exclude_path.parent.mkdir(parents=True, exist_ok=True)
            exclude_path.write_bytes(current + separator + pattern + b"\n")
            return True
        finally:
            release_lock(lock_file)


def _git_common_directory(git_directory: Path) -> Path:
    """worktreeのGitディレクトリから、同じリポジトリで共有されるGitディレクトリを返す。

    Gitは`info/exclude`を共有側のGitディレクトリから読むため、worktreeでは
    `commondir`が指す先へ書く必要がある。`commondir`を持たない通常のcloneでは
    引数のGitディレクトリ自身が共有側となる。
    """
    try:
        raw = (git_directory / "commondir").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return git_directory
    if not raw:
        return git_directory
    common = Path(raw)
    if not common.is_absolute():
        common = git_directory / common
    return common.resolve(strict=False)


def _repository_for_plan_lock(lock_path: Path) -> tuple[Path, Path] | None:
    """計画ロックを含むGitリポジトリrootと管理ディレクトリを返す。"""
    for candidate in lock_path.parents:
        git_directory = _resolve_git_directory(candidate)
        if git_directory is None:
            continue
        relative = lock_path.relative_to(candidate)
        if relative.parts and relative.parts[0] == "plans" and lock_path.name.endswith(".lock"):
            return candidate, git_directory
        return None
    return None


def _resolve_git_directory(root: Path) -> Path | None:
    """通常cloneとworktreeの`.git`から実体ディレクトリを解決する。"""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if not dot_git.is_file():
        return None
    try:
        marker, raw_path = dot_git.read_text(encoding="utf-8").strip().split(":", maxsplit=1)
    except (OSError, UnicodeError, ValueError):
        return None
    if marker != "gitdir":
        return None
    git_directory = Path(raw_path.strip())
    if not git_directory.is_absolute():
        git_directory = root / git_directory
    resolved = git_directory.resolve(strict=False)
    return resolved if resolved.is_dir() else None


def acquire_lock(fh: IO, *, blocking: bool = True) -> None:
    """ファイルハンドル`fh`へ排他ロックを取得する。

    POSIXは`fcntl.flock`、Windowsは`msvcrt.locking`を使う。
    `blocking=True`（既定）は取得できるまで待機する。
    `blocking=False`時は即時取得できない場合に`OSError`を送出する。
    """
    _acquire_lock_impl(fh, blocking=blocking)


def release_lock(fh: IO) -> None:
    """`acquire_lock`で取得したロックを解放する。解放失敗はベストエフォートで無視する。"""
    _release_lock_impl(fh)


def rotate_if_needed(path: Path, max_bytes: int, generations: int = 1) -> None:
    """`path`のサイズが`max_bytes`を超えた場合に世代ローテーションする。

    `generations=1`（現行唯一の対応値）は`path`のsuffixへ`.1`を付加したパスへリネームする
    （既存の`.1`ファイルは上書き）。ファイルが存在しない、サイズが上限未満の場合は何もしない。
    `generations`引数は将来の多世代対応に向けた拡張点として残すが、
    1以外の値は現行呼び出し元に存在しないため`NotImplementedError`とする。
    """
    if generations != 1:
        raise NotImplementedError("generations>1は未対応")
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    path.replace(path.with_suffix(path.suffix + ".1"))


def locked_rotate_and_append(path: Path, line: str, max_bytes: int) -> None:
    """兄弟ロックファイルの排他下で1世代ローテーションと1行追記を行う。

    ロックファイルはログ本体とinodeを分離するため削除しない。
    ディレクトリ作成・ローテーション・追記の失敗は、ログ利用側の処理を妨げないよう無視する。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(path.name + ".lock")
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            acquire_lock(lock_file)
            try:
                rotate_if_needed(path, max_bytes)
                with path.open("a", encoding="utf-8") as log_file:
                    log_file.write(line)
            finally:
                release_lock(lock_file)
    except OSError:
        pass


if os.name == "nt":
    import msvcrt  # type: ignore[import-not-found]  # pylint: disable=import-error

    def _acquire_lock_impl(fh: IO, *, blocking: bool) -> None:
        """Windows: バイト範囲ロックを取得する。

        `blocking=True`時、空ファイルでも`LK_LOCK`はブロッキング取得可能。
        `LK_LOCK`は最大10秒で再試行する仕様のため、長時間の競合に備えてOSError時はループで再試行する。
        `blocking=False`時は`LK_NBLCK`で即時判定し、取得不能なら`OSError`を送出する。
        """
        fh.seek(0)
        if not blocking:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
            return
        while True:
            try:
                msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # type: ignore[attr-defined]
                return
            except OSError:
                continue

    def _release_lock_impl(fh: IO) -> None:
        """Windows: バイト範囲ロックを解放する。"""
        fh.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _acquire_lock_impl(fh: IO, *, blocking: bool) -> None:
        """POSIX: ファイル全体への排他ロックを取得する。"""
        flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
        fcntl.flock(fh.fileno(), flags)

    def _release_lock_impl(fh: IO) -> None:
        """POSIX: ファイル全体への排他ロックを解放する。"""
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
