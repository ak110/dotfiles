"""managed-tempの一時rootと、旧版が作成した状態を置く状態rootの解決、prefixの規則、回収対象の包含判定。"""

from __future__ import annotations

import os
import pathlib
import re
import stat
import sys

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import platformdirs

from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._common import state_paths as _state_paths

MAX_AGE_DAYS = 7
"""一時root直下の対象を自動削除するまでの日数。配下を含む最終更新からの経過で判定する。"""

LEGACY_MARKER_NAME = ".agent-toolkit-managed-temp.json"
"""旧版が領域の内側へ書いた管理情報のファイル名。旧版プロセスとの混在期間の判定にだけ読む。"""

_PREFIX_RULES = (
    ("空にできない", lambda value: value != ""),
    ("英小文字・数字・ハイフンだけを使える", lambda value: re.fullmatch(r"[a-z0-9-]+", value) is not None),
    ("先頭と末尾をハイフンにできない", lambda value: not value.startswith("-") and not value.endswith("-")),
    ("ハイフンを連続させられない", lambda value: "--" not in value),
)
"""prefixの受理条件と、条件ごとの説明文。"""


def require_valid_prefix(prefix: str) -> None:
    """prefixが命名規則に合わなければ、違反した最初の条件を示して拒否する。"""
    for description, satisfied in _PREFIX_RULES:
        if not satisfied(prefix):
            raise ManagedTempError(f"prefixが条件を満たしていません（{description}）: {prefix}")


def _temp_root_path() -> pathlib.Path:
    """rootを指定しない場合に使うOS別のユーザーキャッシュ領域を返す。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            raise ManagedTempError("LOCALAPPDATAが設定されていない")
        return pathlib.Path(base) / "agent-toolkit" / "managed-temp"
    return pathlib.Path(platformdirs.user_cache_dir("agent-toolkit", appauthor=False)) / "managed-temp"


def _state_root_path() -> pathlib.Path:
    """旧版の登録簿・参照・ロックと、掃引の予定表を置く状態ディレクトリ配下の`managed-temp`を返す。"""
    return _state_paths.state_dir() / "managed-temp"


def temp_root_path() -> pathlib.Path:
    """一時rootの絶対パスを、作成せずに返す。"""
    return pathlib.Path(os.path.abspath(_temp_root_path()))


def state_root_path() -> pathlib.Path:
    """状態rootの絶対パスを、作成せずに返す。"""
    return pathlib.Path(os.path.abspath(_state_root_path()))


def _ensure_private_directory(path: pathlib.Path, label: str) -> pathlib.Path:
    """同一ユーザー所有で0700のディレクトリとして準備する。リンクと他者所有は拒否する。"""
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = path.lstat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ManagedTempError(
                f"{label}が通常ディレクトリではない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
            )
        if os.name == "posix":
            if metadata.st_uid != os.geteuid():
                raise ManagedTempError(
                    f"{label}の所有者が実行中のOSアカウントではない: {path}",
                    next_action=ManagedTempError.PERMISSION_NEXT_ACTION,
                )
            if stat.S_IMODE(metadata.st_mode) != 0o700:
                path.chmod(0o700)
    except OSError as error:
        raise ManagedTempError(f"{label}を準備できない: {path}: {error}") from error
    return path


def temp_root() -> pathlib.Path:
    """一時rootを準備して絶対パスを返す。"""
    return _ensure_private_directory(temp_root_path(), "一時ディレクトリのルート")


def state_root() -> pathlib.Path:
    """状態rootを準備して絶対パスを返す。"""
    return _ensure_private_directory(state_root_path(), "外部状態ディレクトリ")


def is_link(metadata: os.stat_result) -> bool:
    """シンボリックリンクとWindowsのreparse point（junctionなど）を真とする。"""
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def contained_target(path_arg: pathlib.Path | str) -> pathlib.Path:
    """一時root配下の回収対象として、root自身・root外・リンクを経由する経路を拒否した絶対パスを返す。

    対象自身がリンクの場合も拒否する。リンクを辿った先はroot外の実体であり得るためである。
    """
    path = pathlib.Path(path_arg)
    if not path.is_absolute():
        raise ManagedTempError(f"pathは絶対パスで指定する: {path}")
    path = pathlib.Path(os.path.abspath(path))
    root = temp_root_path()
    if path == root or not path.is_relative_to(root):
        raise ManagedTempError(f"一時rootの配下ではない: {path}（一時root: {root}）")
    current = root
    for part in path.relative_to(root).parts:
        current = current / part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            break
        if is_link(metadata):
            raise ManagedTempError(f"リンクを経由するため回収しない: {current}")
    return path
