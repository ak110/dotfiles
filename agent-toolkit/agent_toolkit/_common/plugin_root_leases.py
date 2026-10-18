"""保持した配布物の起動側と回収側で、排他とプロセスの使用記録を共有する。"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import typing
from collections.abc import Generator
from pathlib import Path

import psutil

from agent_toolkit._common import file_lock, state_paths

SHARED_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root-shared"
_process_snapshot: contextvars.ContextVar[dict[str, list[psutil.Process]] | None] = contextvars.ContextVar(
    "plugin_root_processes", default=None
)
_usage_details: contextvars.ContextVar[str] = contextvars.ContextVar("plugin_root_usage", default="")
_usage_basis: contextvars.ContextVar[dict[str, typing.Any] | None] = contextvars.ContextVar("plugin_root_basis", default=None)


@contextlib.contextmanager
def reuse_process_observation() -> Generator[None]:
    """同じ掃引内のMCP列挙を共有し、終了後に観測を解放する。"""
    token = _process_snapshot.set({})
    try:
        yield
    finally:
        _process_snapshot.reset(token)


def usage_details() -> str:
    """直前の保持判定のprocess identityまたは取得不能の理由を返す。"""
    return _usage_details.get()


def usage_basis() -> dict[str, typing.Any] | None:
    """生存再確認で再利用できる保持根拠を返し、判定不能ではNoneを返す。"""
    return _usage_basis.get()


def _metadata_signature(path: Path) -> list[int]:
    metadata = path.stat()
    return [metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size]


def usage_basis_is_current(basis: dict[str, typing.Any]) -> bool:
    """同じprocess identityと使用記録が保たれる間だけ、保持の判定を再利用する。"""
    try:
        if basis["leases"] != _metadata_signature(_directory()):
            return False
        if basis.get("record") is not None:
            path = Path(basis["record"])
            if path.parent != _directory() or basis["record_signature"] != _metadata_signature(path):
                return False
        process = psutil.Process(basis["pid"])
        return process.create_time() == basis["created_at"] and process.status() != psutil.STATUS_ZOMBIE
    except KeyError, TypeError, OSError, ValueError, psutil.Error:
        return False


def _record_process_use(process: psutil.Process, reason: str, *, record: Path | None = None) -> bool:
    created_at = process.create_time()
    _usage_details.set(f"{reason}: pid={process.pid}, created_at={created_at}")
    _usage_basis.set(
        {
            "pid": process.pid,
            "created_at": created_at,
            "leases": _metadata_signature(_directory()),
            "record": str(record) if record is not None else None,
            "record_signature": _metadata_signature(record) if record is not None else None,
        }
    )
    return True


def _key(path: Path) -> str:
    return hashlib.sha256(os.fsencode(os.path.normcase(os.path.abspath(path)))).hexdigest()


def _directory() -> Path:
    directory = state_paths.state_dir() / "plugin-root-leases"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    return directory


@contextlib.contextmanager
def plugin_root_lock(path: Path, *, blocking: bool = True) -> Generator[None]:
    """同じ保持領域の使用登録と回収を直列にする。ロック実体は回収後も残す。

    `blocking=False`では即時取得できない場合に`OSError`を送出する。任意保守の自動掃引が、
    使用登録や明示回収の保持者を待たずに対象を保持して本来の操作へ進むために使う。
    """
    with (_directory() / f"{_key(path)}.lock").open("a+", encoding="utf-8") as handle:
        file_lock.acquire_lock(handle, blocking=blocking)
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


def register_lease_capability() -> None:
    """保持領域を使わないMCPも、lease対応能力とprocess identityを公開する。"""
    process = psutil.Process()
    record = {"pid": process.pid, "created_at": process.create_time(), "lease_capable": True}
    destination = _directory() / f"capability-{process.pid}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(destination)


def _lease_capable(process: psutil.Process) -> bool:
    """領域別leaseも対応能力の証拠として受理し、PID再利用を作成時刻で区分する。"""
    created_at = process.create_time()
    for path in _directory().glob(f"*-{process.pid}.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            raise ValueError(f"lease記録がobjectではない: {path}")
        if (
            record.get("pid") == process.pid
            and record.get("created_at") == created_at
            and (record.get("lease_capable") is True or isinstance(record.get("path"), str))
        ):
            return True
    return False


def plugin_root_in_use(area: Path) -> bool:
    """同じPIDと作成時刻の生存を調べ、取得不能な記録も保持する。ロック下で呼ぶ。"""
    _usage_details.set("")
    _usage_basis.set(None)
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
                _usage_details.set(f"使用記録の形式を確認できない: {path}")
                return True
            try:
                process = psutil.Process(record["pid"])
                if process.create_time() == record["created_at"] and process.status() != psutil.STATUS_ZOMBIE:
                    return _record_process_use(process, "領域別lease", record=path)
            except psutil.NoSuchProcess:
                pass
            path.unlink()
        except (OSError, ValueError, psutil.Error) as error:
            _usage_details.set(f"使用記録を取得できない: {path}: {error}")
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
        snapshot = _process_snapshot.get()
        if snapshot is not None and "processes" not in snapshot:
            snapshot["processes"] = list(psutil.process_iter())
        for process in psutil.process_iter() if snapshot is None else snapshot["processes"]:
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
                    _usage_details.set(f"起動引数を取得できない: pid={process.pid}")
                    return True
                # 内側MCPのlease登録前も、公開launcherが直接参照する保持先を保護する。
                # cleanupの--pathは、MCP targetを持たないためこの候補集合へ入らない。
                if any(Path(argument).is_absolute() and Path(argument).is_relative_to(area) for argument in arguments):
                    return _record_process_use(process, "領域の直接参照")
                # 公開launcherのuv親と、スクリプトを実行するPythonを区分する。
                if (
                    not allow_unattributed
                    or len(arguments) < 2
                    or Path(arguments[1]).parts[-2:] != ("agent_toolkit", "agents_server_mcp.py")
                ):
                    continue
                if _server_namespace_may_match(process) and not _lease_capable(process):
                    return _record_process_use(process, "同じ状態名前空間のlease非対応MCP")
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as error:
                _usage_details.set(f"MCPの使用状態を取得できない: {error}")
                return True
    except (OSError, ValueError, psutil.Error) as error:
        _usage_details.set(f"MCPの列挙または使用記録を取得できない: {error}")
        return True
    return False
