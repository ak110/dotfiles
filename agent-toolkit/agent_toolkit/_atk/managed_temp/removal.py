"""managed-tempの対象の検証・列挙・回収。

回収は一時root配下の包含判定を通した対象だけを`shutil.rmtree`で削除する。
root自身・root外・リンクを経由する経路と、使用中の保持配布物は回収しない。不在は成功とする。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import stat
import typing

from agent_toolkit._atk.managed_temp import paths, permissions
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._common import plugin_root_leases

PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
"""稼働中のagents_serverが版固定の配布物を複製する領域の接頭辞。旧形式と共有形式の両方がこの名前で始まる。"""


def validate_managed_temp(path_arg: pathlib.Path | str) -> pathlib.Path:
    """一時root配下の実在するディレクトリであることを確かめ、削除せずに絶対パスを返す。"""
    path = paths.contained_target(path_arg)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ManagedTempError(f"管理対象を検証できない: {path}: {error}") from error
    if not stat.S_ISDIR(metadata.st_mode):
        raise ManagedTempError(
            f"管理対象が通常ディレクトリではない: {path}", next_action=ManagedTempError.PERMISSION_NEXT_ACTION
        )
    return path


def list_managed_temp(prefix: str | None = None) -> list[pathlib.Path]:
    """一時root直下の非リンクのディレクトリを名前順に返す。prefixを指定すると`<prefix>-`から始まるものに限る。"""
    if prefix is not None:
        paths.require_valid_prefix(prefix)
    root = paths.temp_root_path()
    try:
        children = list(os.scandir(root))
    except FileNotFoundError:
        return []
    return sorted(
        root / child.name
        for child in children
        if (prefix is None or child.name.startswith(f"{prefix}-"))
        and child.is_dir(follow_symlinks=False)
        and not paths.is_link(child.stat(follow_symlinks=False))
    )


def top_level_area(path: pathlib.Path) -> pathlib.Path:
    """一時root配下の対象が属する、一時root直下の領域を返す。"""
    root = paths.temp_root_path()
    return root / path.relative_to(root).parts[0]


def plugin_root_in_use(area: pathlib.Path) -> bool:
    """保持配布物の領域を使用中か、使用状態を確認できない場合に真を返す。

    使用登録と回収を直列にするロックを即時取得できない場合も、別の主体が登録・回収中として真を返す。
    """
    if not area.name.startswith(PLUGIN_ROOT_PREFIX):
        return False
    try:
        with plugin_root_leases.plugin_root_lock(area, blocking=False):
            return plugin_root_leases.plugin_root_in_use(area)
    except OSError:
        return True


def _retry_writable(function: typing.Callable[[str], object], path: str, error: BaseException) -> None:
    """削除できない読み取り専用のエントリに所有者の書込権限を補い、同じ削除を再試行する。"""
    if isinstance(error, FileNotFoundError):
        return
    if not isinstance(error, PermissionError):
        raise error
    target = path if os.name == "nt" else os.path.dirname(path)
    metadata = os.lstat(target)
    if os.name == "posix" and metadata.st_uid != os.geteuid():
        raise error
    os.chmod(target, stat.S_IMODE(metadata.st_mode) | (stat.S_IWRITE if os.name == "nt" else 0o700))
    function(path)


def remove_tree(path: pathlib.Path) -> None:
    """包含判定済みの対象を削除する。ディレクトリは配下ごと削除し、不在は成功とする。"""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(metadata.st_mode) or paths.is_link(metadata):
        path.unlink(missing_ok=True)
        return
    try:
        shutil.rmtree(path, onexc=_retry_writable)
    except PermissionError:
        if os.name != "posix":
            raise
        # 走査できない配下（所有者の読取・実行権限を外したディレクトリ）だけが残る。所有者の権限を補って再削除する。
        permissions.prepare_tree(path)
        shutil.rmtree(path, onexc=_retry_writable)


def cleanup_managed_temp(path_arg: pathlib.Path | str) -> None:
    """一時root配下の対象を回収する。使用中の保持配布物は回収せずに拒否する。"""
    path = paths.contained_target(path_arg)
    if not os.path.lexists(path):
        return
    area = top_level_area(path)
    if plugin_root_in_use(area):
        raise ManagedTempError(
            f"使用中または使用状態を確認できない配布物のため回収しない: {area}。{plugin_root_leases.usage_details()}",
            next_action="利用プロセスの終了後に再実行する。期限後の自動掃引でも再判定する",
        )
    try:
        remove_tree(path)
    except OSError as error:
        raise ManagedTempError(f"管理対象を回収できない: {path}: {error}") from error
