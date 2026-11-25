from __future__ import annotations

import logging
import os
import pathlib
import shutil
import stat
import typing

from agent_toolkit._atk.managed_temp import paths, permissions
from agent_toolkit._atk.managed_temp.errors import ManagedTempError
from agent_toolkit._common import plugin_root_leases

_LOG = logging.getLogger(__name__)
PLUGIN_ROOT_PREFIX = "agents-server-plugin-root"
"""稼働中のagents_serverが版固定の配布物を複製する領域の接頭辞。旧形式と共有形式の両方がこの名前で始まる。"""


def validate_managed_temp(path_arg: pathlib.Path | str) -> pathlib.Path:
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
    # rmtreeはopenやscandirの失敗も渡す。走査関数の再実行はprepare_treeによる木全体の補修へ任せる。
    if function not in (os.unlink, os.remove, os.rmdir):
        raise error
    target = path if os.name == "nt" else os.path.dirname(path)
    metadata = os.lstat(target)
    if os.name == "posix" and metadata.st_uid != os.geteuid():
        raise error
    os.chmod(target, stat.S_IMODE(metadata.st_mode) | (stat.S_IWRITE if os.name == "nt" else 0o700))
    function(path)


def remove_tree(path: pathlib.Path) -> None:
    area = top_level_area(path)
    if area.name.startswith(PLUGIN_ROOT_PREFIX):
        with plugin_root_leases.plugin_root_lock(area, blocking=False):
            if plugin_root_leases.plugin_root_in_use(area):
                raise ManagedTempError(f"使用中または使用状態を確認できない配布物のため回収しない: {area}")
            _remove_tree(path)
        return
    _remove_tree(path)


def _remove_tree(path: pathlib.Path) -> None:
    """使用登録との排他が必要な対象は、呼び出し側がロックを保持する。"""
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


def release_stable_plugin_root(root: pathlib.Path) -> None:
    area = root.parent
    if area.parent != paths.temp_root_path() or plugin_root_leases.completed_plugin_copy(area) != root:
        return
    try:
        validate_managed_temp(area)
        plugin_root_leases.release_plugin_root_use(area)
        cleanup_managed_temp(area)
    except ManagedTempError as exc:
        _LOG.debug("配布物rootを保持します: root=%s error=%s", root, exc)
    except (OSError, ValueError) as exc:
        _LOG.warning("配布物rootの解放に失敗しました: root=%s error=%s", root, exc)
