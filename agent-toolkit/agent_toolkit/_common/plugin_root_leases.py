"""保持した配布物の起動側と回収側で、排他とプロセスの使用記録を共有する。"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import sys
import typing
from collections.abc import Generator
from pathlib import Path

import psutil

from agent_toolkit._common import file_lock, private_directory, process_identity, state_paths

SHARED_PLUGIN_ROOT_PREFIX = "agents-server-plugin-root-shared"
_process_snapshot: contextvars.ContextVar[dict[str, dict[int, psutil.Process]] | None] = contextvars.ContextVar(
    "plugin_root_processes", default=None
)
_usage_details: contextvars.ContextVar[str] = contextvars.ContextVar("plugin_root_usage", default="")
_usage_basis: contextvars.ContextVar[dict[str, typing.Any] | None] = contextvars.ContextVar("plugin_root_basis", default=None)


@contextlib.contextmanager
def reuse_process_observation() -> Generator[None]:
    token = _process_snapshot.set({})
    try:
        yield
    finally:
        _process_snapshot.reset(token)


def usage_details() -> str:
    return _usage_details.get()


def usage_basis() -> dict[str, typing.Any] | None:
    return _usage_basis.get()


def _metadata_signature(path: Path) -> list[int]:
    metadata = path.stat()
    return [metadata.st_dev, metadata.st_ino, metadata.st_mtime_ns, metadata.st_ctime_ns, metadata.st_size]


def usage_basis_is_current(basis: dict[str, typing.Any]) -> bool:
    try:
        if basis["leases"] != _metadata_signature(_directory()):
            return False
        if basis.get("record") is not None:
            path = Path(basis["record"])
            if path.parent != _directory() or basis["record_signature"] != _metadata_signature(path):
                return False
        process = psutil.Process(basis["pid"])
        return process_identity.matches(
            process, basis["created_at"], basis.get("start_id"), conservative=True
        ) and process.status() not in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        )
    except KeyError, TypeError, OSError, ValueError, psutil.Error:
        return False


def _record_process_use(process: psutil.Process, reason: str, *, record: Path | None = None) -> bool:
    created_at = process.create_time()
    _usage_details.set(f"{reason}: pid={process.pid}, created_at={created_at}")
    _usage_basis.set(
        {
            "pid": process.pid,
            "created_at": created_at,
            "start_id": process_identity.start_id(process),
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
    directory.mkdir(mode=private_directory.MODE, parents=True, exist_ok=True)
    return directory


@contextlib.contextmanager
def plugin_root_lock(path: Path, *, blocking: bool = True) -> Generator[None]:
    with _lock(_directory() / f"{_key(path)}.lock", blocking=blocking):
        yield


@contextlib.contextmanager
def _lock(path: Path, *, blocking: bool) -> Generator[None]:
    """待機中に掃引されたinodeでは登録せず、現在のロック実体を取り直す。"""
    while True:
        with path.open("a+", encoding="utf-8") as handle:
            file_lock.acquire_lock(handle, blocking=blocking)
            try:
                try:
                    current = os.path.samestat(os.fstat(handle.fileno()), path.stat())
                except FileNotFoundError:
                    current = False
                if current:
                    yield
                    return
            finally:
                file_lock.release_lock(handle)


def register_plugin_root_use(area: Path) -> None:
    """呼出元のPIDと作成時刻を記録する。呼出元は領域のロックを保持する。"""
    try:
        process = psutil.Process()
        record = {
            "pid": process.pid,
            "created_at": process.create_time(),
            "start_id": process_identity.start_id(process),
            "path": str(area),
        }
    except psutil.Error as error:
        raise OSError(f"配布物を使うプロセスのidentityを取得できない: {error}") from error
    destination = _directory() / f"{_key(area)}-{process.pid}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(destination)


def _record_is_live(record: typing.Any) -> bool:
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("pid"), int)
        or isinstance(record["pid"], bool)
        or record["pid"] <= 0
        or not isinstance(record.get("created_at"), (float, int))
        or isinstance(record["created_at"], bool)
    ):
        raise ValueError("使用記録のprocess identityが不正")
    try:
        snapshot = _process_snapshot.get()
        process = None if snapshot is None else _observed_processes().get(record["pid"])
        if process is None or not process_identity.matches(
            process, record["created_at"], record.get("start_id"), conservative=True
        ):
            process = psutil.Process(record["pid"])
        return process_identity.matches(
            process, record["created_at"], record.get("start_id"), conservative=True
        ) and process.status() not in (
            psutil.STATUS_ZOMBIE,
            psutil.STATUS_DEAD,
        )
    except psutil.NoSuchProcess:
        return False


def sweep_plugin_root_records() -> None:
    """終了したプロセスの記録と、使用記録が無く即時取得できるロックを回収する。

    対応能力の記録（`capability-*.json`）は変更前の版のサーバーだけが起動時に書く。
    変更前の版はロックを開いてから取得した後、開いたファイルがまだ同じパスにあるかを確かめないため、その間にロックを消すと、
    新旧の版が別のinodeで同時に排他を得る。変更前の版の生存を否定できない間はロックファイルを残す。
    """
    directory = _directory()
    legacy_holder = False
    for path in directory.glob("*.json"):
        try:
            if path.name.startswith("capability-"):
                if _record_is_live(json.loads(path.read_text(encoding="utf-8"))):
                    legacy_holder = True
                else:
                    path.unlink(missing_ok=True)
                continue
            key, separator, pid = path.stem.rpartition("-")
            if not separator or len(key) != 64 or any(char not in "0123456789abcdef" for char in key) or not pid.isdecimal():
                continue
            with _lock(directory / f"{key}.lock", blocking=False):
                if not _record_is_live(json.loads(path.read_text(encoding="utf-8"))):
                    path.unlink(missing_ok=True)
        except OSError, ValueError, psutil.Error:
            if path.name.startswith("capability-"):
                legacy_holder = True
            continue
    if legacy_holder:
        return
    for path in directory.glob("*.lock"):
        try:
            with _lock(path, blocking=False):
                if any(directory.glob(f"{path.stem}-*.json")):
                    continue
                # POSIXでは排他中に消す。Windowsは開いたファイルの削除を拒否するため、
                # 閉じた後に消す（他の取得者が開いていればその削除も拒否される）。
                if os.name != "nt":
                    path.unlink(missing_ok=True)
            if os.name == "nt":
                path.unlink(missing_ok=True)
        except OSError:
            continue


def plugin_root_in_use(area: Path) -> bool:
    """同じPIDと作成時刻の生存を調べ、取得不能な記録も保持する。ロック下で呼ぶ。"""
    _usage_details.set("")
    _usage_basis.set(None)
    for run_path in (state_paths.state_dir() / "wi-submit" / "runs").glob("*/run.json"):
        run = json.loads(run_path.read_text(encoding="utf-8"))
        if not isinstance(run, dict):
            raise ValueError(f"独立WI受付の実行記録がobjectではない: {run_path}")
        root = run.get("runtime_root")
        if (
            isinstance(root, str)
            and Path(root).parent == area
            and (run.get("status") != "completed" or not run.get("report_synced"))
        ):
            _usage_details.set(f"未完了または未報告の独立WI受付: {run_path}")
            return True
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
                if process_identity.matches(
                    process, record["created_at"], record.get("start_id"), conservative=True
                ) and process.status() not in (
                    psutil.STATUS_ZOMBIE,
                    psutil.STATUS_DEAD,
                ):
                    return _record_process_use(process, "領域別lease", record=path)
            except psutil.NoSuchProcess:
                pass
            path.unlink()
        except (OSError, ValueError, psutil.Error) as error:
            _usage_details.set(f"使用記録を取得できない: {path}: {error}")
            return True
    if not area.name.startswith("agents-server-plugin-root-"):
        return False
    return _server_may_use_root(area)


def _current_processes() -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for listed in psutil.process_iter():
        try:
            processes.append(psutil.Process(listed.pid))
        except psutil.NoSuchProcess:
            continue
    return processes


def _observed_processes() -> dict[int, psutil.Process]:
    snapshot = _process_snapshot.get()
    if snapshot is None:
        return {process.pid: process for process in _current_processes()}
    if "processes" not in snapshot:
        snapshot["processes"] = {process.pid: process for process in _current_processes()}
    return snapshot["processes"]


def _windows_secure_system(process: psutil.Process) -> bool:
    """通常のWindowsプロセスと区別できた場合だけ、組み込みのSecure Systemとみなす。"""
    if sys.platform != "win32":
        return False
    try:
        if process.name() != "Secure System" or process.ppid() != 4:
            return False
        system = psutil.Process(4)

        return system.name() == "System" and system.ppid() == 0
    except OSError, psutil.Error:
        return False


def _server_may_use_root(area: Path) -> bool:
    try:
        username = psutil.Process().username()
        for process in _observed_processes().values():
            try:
                try:
                    arguments = process.cmdline()
                except psutil.NoSuchProcess:
                    continue
                except psutil.AccessDenied:
                    if _windows_secure_system(process):
                        continue
                    arguments = None
                except OSError, ValueError, psutil.Error:
                    arguments = None
                if arguments is not None and not any(
                    Path(argument).parts[-2:] == ("agent_toolkit", "agents_server_mcp.py") for argument in arguments
                ):
                    continue
                if process.username() != username or process.status() in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    continue
                if arguments is None:
                    _usage_details.set(f"起動引数を取得できない: pid={process.pid}")
                    return True

                if any(Path(argument).is_absolute() and Path(argument).is_relative_to(area) for argument in arguments):
                    return _record_process_use(process, "領域の直接参照")
            except psutil.NoSuchProcess:
                continue
            except psutil.Error as error:
                _usage_details.set(f"MCPの使用状態を取得できない: {error}")
                return True
    except (OSError, ValueError, psutil.Error) as error:
        _usage_details.set(f"MCPの列挙または使用記録を取得できない: {error}")
        return True
    return False
