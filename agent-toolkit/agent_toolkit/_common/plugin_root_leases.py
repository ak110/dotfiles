"""保持した配布物の起動側と回収側で、排他とプロセスの使用記録を共有する。"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from collections.abc import Generator
from pathlib import Path

import psutil

from agent_toolkit._common import file_lock, state_paths

SHARED_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root-shared"


def _key(path: Path) -> str:
    return hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath(path)))).hexdigest()


def _directory() -> Path:
    directory = state_paths.state_dir() / "plugin-root-leases"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextlib.contextmanager
def plugin_root_lock(path: Path) -> Generator[None]:
    """同じ保持領域の使用登録と回収を直列にする。ロック実体は回収後も残す。"""
    with (_directory() / f"{_key(path)}.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle)
        try:
            yield
        finally:
            file_lock.release_lock(handle)


def register_plugin_root_use(area: Path) -> None:
    """呼出元のPIDと作成時刻を記録する。呼出元は領域のロックを保持する。"""
    try:
        process = psutil.Process()
        record = {"pid": process.pid, "created_at": process.create_time(), "path": str(area)}
    except psutil.Error as error:
        raise OSError(f"配布物を使うプロセスのidentityを取得できない: {error}") from error
    destination = _directory() / f"{_key(area)}-{process.pid}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(destination)


def plugin_root_in_use(area: Path) -> bool:
    """同じPIDと作成時刻の生存を調べ、取得不能な記録も保持する。ロック下で呼ぶ。"""
    for path in _directory().glob(f"{_key(area)}-*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(record, dict)
                or record.get("path") != str(area)
                or not isinstance(record.get("pid"), int)
                or isinstance(record["pid"], bool)
                or record["pid"] <= 0
                or not isinstance(record.get("created_at"), (float, int))
                or isinstance(record["created_at"], bool)
            ):
                return True
            try:
                process = psutil.Process(record["pid"])
                if process.create_time() == record["created_at"] and process.status() != psutil.STATUS_ZOMBIE:
                    return True
            except psutil.NoSuchProcess:
                pass
            path.unlink()
        except OSError, ValueError, psutil.Error:
            return True
    # lease導入前のMCPは、コピー先と起動元の対応を永続化していない。
    # 同じ状態名前空間のMCPが残る間、その帰属が曖昧な旧領域を保持する。
    if not area.name.startswith("agents-server-plugin-root-"):
        return False
    allow_unattributed = (
        not area.name.startswith(f"{SHARED_PLUGIN_ROOT_PREFIX}-") and not (area / ".agent-toolkit-plugin-root.json").exists()
    )
    return _server_may_use_root(area, allow_unattributed=allow_unattributed)


def _server_namespace_may_match(process: psutil.Process) -> bool:
    """実MCPが開いている既存診断ログから保存先を確認し、欠損時は保持する。"""
    # logging_config.configure_loggingがstate_dir直下へ開く永続ログを使う。
    # process.environは起動後の変更を反映しないOSがあるため判定に使わない。
    logs = [
        Path(record.path)
        for record in process.open_files()
        if Path(record.path).parts[-2:] == ("agent-toolkit", "agents-server.log")
    ]
    if len(logs) != 1 or not logs[0].is_file():
        return True
    return logs[0].parent.samefile(state_paths.state_dir())


def _server_may_use_root(area: Path, *, allow_unattributed: bool) -> bool:
    """同じOSアカウント・状態名前空間のMCPをOSの起動引数と参照から探す。"""
    try:
        username = psutil.Process().username()
        for process in psutil.process_iter():
            try:
                try:
                    arguments = process.cmdline()
                except psutil.NoSuchProcess:
                    continue
                except OSError, ValueError, psutil.Error:
                    arguments = None
                if arguments is not None and not any(
                    Path(argument).parts[-2:] == ("agent_toolkit", "agents_server_mcp.py") for argument in arguments
                ):
                    continue
                if process.username() != username or process.status() == psutil.STATUS_ZOMBIE:
                    continue
                if arguments is None:
                    return True
                # 内側MCPのlease登録前も、公開launcherが直接参照する保持先を保護する。
                # cleanupの--pathは、MCP targetを持たないためこの候補集合へ入らない。
                if any(Path(argument).is_absolute() and Path(argument).is_relative_to(area) for argument in arguments):
                    return True
                # 公開launcherのuv親と、スクリプトを実行するPythonを区分する。
                if (
                    not allow_unattributed
                    or len(arguments) < 2
                    or Path(arguments[1]).parts[-2:] != ("agent_toolkit", "agents_server_mcp.py")
                ):
                    continue
                if _server_namespace_may_match(process):
                    return True
            except psutil.NoSuchProcess:
                continue
            except psutil.Error:
                return True
    except OSError, ValueError, psutil.Error:
        return True
    return False
