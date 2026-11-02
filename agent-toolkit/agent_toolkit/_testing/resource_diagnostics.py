"""Windowsの回収失敗時に、失敗パスの保持PIDを診断する。

取得不能は状態とAPIのエラー番号で表し、保持PIDの空集合と区別する。
汎用の削除処理や回収対象には介入しない。
"""

import ctypes
import datetime
import json
import os
import pathlib
import sys
from typing import Any

from agent_toolkit._common import process_tree


class _IoStatusBlock(ctypes.Structure):
    # StatusとPointerのunionはpointer幅。InformationはULONG_PTR。
    _fields_ = [("status", ctypes.c_void_p), ("information", ctypes.c_size_t)]


class _ProcessIdsUsingFile(ctypes.Structure):
    # https://raw.githubusercontent.com/winsiderss/systeminformer/master/phnt/include/ntioapi.h
    # FILE_PROCESS_IDS_USING_FILE_INFORMATIONはULONGとHANDLE配列。
    # HANDLE配列の先頭には64bit環境でalignment paddingがある。
    _fields_ = [("count", ctypes.c_uint32), ("pids", ctypes.c_void_p * 1)]


def file_holders(path: pathlib.Path) -> dict[str, Any]:
    """単一パスの保持者を照会し、自診断用handleのPIDを分離して返す。

    自プロセスは診断handleを開くため列挙され得る。同じPIDの他のhandleの有無は判別できない。
    """
    if sys.platform != "win32":
        return {"state": "unsupported", "platform": sys.platform}
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    kernel.CreateFileW.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.c_void_p,
    ]
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel.CloseHandle.restype = ctypes.c_int
    native.NtQueryInformationFile.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_IoStatusBlock),
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    native.NtQueryInformationFile.restype = ctypes.c_int32
    # 属性照会のみを要求し、共有read/write/deleteを許可する。BACKUP_SEMANTICSでディレクトリも開く。
    handle = kernel.CreateFileW(str(path), 0x80, 0x7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        return {"state": "unavailable", "api": "CreateFileW", "winerror": ctypes.get_last_error()}
    try:
        # 診断のメモリー使用量を限定する。容量不足のNTSTATUSは取得不能として残す。
        buffer = ctypes.create_string_buffer(1024 * 1024)
        status_block = _IoStatusBlock()
        # FILE_INFORMATION_CLASSのFileProcessIdsUsingFileInformationは47。
        # https://learn.microsoft.com/en-us/windows-hardware/drivers/ddi/wdm/ne-wdm-_file_information_class
        status = native.NtQueryInformationFile(handle, ctypes.byref(status_block), buffer, len(buffer), 47)
        if status < 0:
            return {"state": "unavailable", "api": "NtQueryInformationFile", "ntstatus": f"0x{status & 0xFFFFFFFF:08x}"}
        count = ctypes.c_uint32.from_buffer(buffer).value
        offset = _ProcessIdsUsingFile.pids.offset
        required = offset + count * ctypes.sizeof(ctypes.c_void_p) if count else ctypes.sizeof(ctypes.c_uint32)
        if required > len(buffer) or required > status_block.information:
            return {"state": "unavailable", "api": "NtQueryInformationFile", "reason": "incomplete_pid_array"}
        pids = (ctypes.c_void_p * count).from_buffer(buffer, offset)
        observed = sorted({int(pid) for pid in pids if pid})
        return {
            "state": "success",
            "holders": [process_tree.identity(pid) for pid in observed if pid != os.getpid()],
            "diagnostic_handle_pid": os.getpid(),
            "diagnostic_pid_observed": os.getpid() in observed,
            "self_other_handles": "not_distinguished",
        }
    finally:
        if not kernel.CloseHandle(handle):
            raise OSError(ctypes.get_last_error(), "診断用handleを閉じられませんでした")


def report_cleanup_failure(
    error: BaseException,
    *,
    directory: pathlib.Path | None,
    before: list[dict[str, int | float | str]],
    after: list[dict[str, int | float | str]],
) -> None:
    """原因鎖の失敗パスだけを診断し、診断自身の例外で元の失敗を置き換えない。"""
    if sys.platform != "win32":
        return
    next_action = (
        "元の回収例外のtracebackから失敗パス・winerrorを保存し、"
        "同じ実行の終了前後PID記録とCI環境・最終コードを対応付けて原因分析担当へ渡せ"
    )
    try:
        failures = []
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, OSError) and current.filename is not None:
                path = pathlib.Path(os.fsdecode(current.filename))
                try:
                    ownership = file_holders(path)
                except Exception as diagnostic_error:  # noqa: BLE001  # 失敗パスを保持して診断失敗へ変換する
                    ownership = {"state": "unavailable", "error": type(diagnostic_error).__name__}
                failures.append(
                    {
                        "path": str(path),
                        "winerror": getattr(current, "winerror", None),
                        "errno": current.errno,
                        "ownership": ownership,
                    }
                )
            current = current.__cause__ or current.__context__
        record = {
            "event": "cleanup_failure",
            "at": datetime.datetime.now(datetime.UTC).isoformat(),
            "pytest_pid": os.getpid(),
            "directory": None if directory is None else str(directory),
            "before": before,
            "after": after,
            "failures": failures,
            "path_state": "observed" if failures else "unavailable",
            "next_action": next_action,
        }
        print("resource-diagnostics: " + json.dumps(record, ensure_ascii=True), file=sys.stderr, flush=True)
    except Exception as diagnostic_error:  # noqa: BLE001  # 元の回収例外を維持する診断境界
        print(
            f"resource-diagnostics: unavailable={type(diagnostic_error).__name__}; "
            f"pytest_pid={os.getpid()}; next_action={next_action}",
            file=sys.stderr,
            flush=True,
        )
