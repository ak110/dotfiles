"""開始時に所有するWindows Jobの所属を固定し、短命な親から生まれた子孫も回収する。

PIDの列挙・後からのhandle取得では、列挙から保持までに終了した親の子を所有へ戻せない。
所有rootをCREATE_SUSPENDEDで開始し、元世代のPopen handleでJobへ登録してから再開する。
明示したCREATE_BREAKAWAY_FROM_JOBは、独立workerを親の終了から分離する既存契約として保つ。
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import Any

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
# 端末から切り離して背景で動かす子の生成フラグ。DETACHED_PROCESSはコンソール自体を持たせないため、
# その子が起動するコンソールアプリ（venvのpython.exeが中継する本体を含む）へWindowsが新しい可視コンソールを割り当てる。
# CREATE_NO_WINDOWは非表示の新しいコンソールを持たせ、子孫も同じ非表示コンソールを継承する。
HIDDEN_DETACHED_CREATIONFLAGS = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64),
        ("job_time", ctypes.c_int64),
        ("flags", ctypes.c_uint32),
        ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t),
        ("active_process_limit", ctypes.c_uint32),
        ("affinity", ctypes.c_size_t),
        ("priority", ctypes.c_uint32),
        ("scheduling", ctypes.c_uint32),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits),
        ("io", ctypes.c_uint64 * 6),
        ("process_memory", ctypes.c_size_t),
        ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t),
        ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("times", ctypes.c_int64 * 4),
        ("faults", ctypes.c_uint32),
        ("total", ctypes.c_uint32),
        ("active", ctypes.c_uint32),
        ("terminated", ctypes.c_uint32),
    ]


class _ThreadEntry(ctypes.Structure):
    _fields_ = [
        ("size", ctypes.c_uint32),
        ("usage", ctypes.c_uint32),
        ("thread_id", ctypes.c_uint32),
        ("owner_pid", ctypes.c_uint32),
        ("base_priority", ctypes.c_int32),
        ("delta_priority", ctypes.c_int32),
        ("flags", ctypes.c_uint32),
    ]


class WindowsJob:
    """1つの所有rootと、明示離脱を除く全子孫の寿命を所有するJob。"""

    def __init__(self, *, kill_on_close: bool = True) -> None:
        # WinDLL等はWindows専用属性。DLLの公開関数signatureをここだけで定義する。
        self._ctypes: Any = ctypes
        self._kernel = self._ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "GetProcessId": ([wintypes.HANDLE], ctypes.c_uint32),
            "CreateToolhelp32Snapshot": ([ctypes.c_uint32, ctypes.c_uint32], wintypes.HANDLE),
            "Thread32First": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "Thread32Next": ([wintypes.HANDLE, ctypes.POINTER(_ThreadEntry)], wintypes.BOOL),
            "OpenThread": ([ctypes.c_uint32, wintypes.BOOL, ctypes.c_uint32], wintypes.HANDLE),
            "GetProcessIdOfThread": ([wintypes.HANDLE], ctypes.c_uint32),
            "ResumeThread": ([wintypes.HANDLE], ctypes.c_uint32),
            "TerminateJobObject": ([wintypes.HANDLE, ctypes.c_uint32], wintypes.BOOL),
            "QueryInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p],
                wintypes.BOOL,
            ),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self._kernel, name)
            function.argtypes, function.restype = arguments, result
        self._handle = self._kernel.CreateJobObjectW(None, None)
        if not self._handle:
            raise self._error()
        try:
            limits = _ExtendedLimits()
            # 最後の所有handleを閉じた場合の回収と、明示的な独立workerへの離脱を両立する。
            limits.basic.flags = (0x00002000 if kill_on_close else 0) | 0x00000800
            if not self._kernel.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise self._error()
        except BaseException:
            self.close()
            raise

    def __enter__(self) -> WindowsJob:
        return self

    def __exit__(self, *_exception: object) -> None:
        self.close()

    def assign_suspended(self, process_handle: int, pid: int) -> None:
        """元世代のhandleをJobへ登録してから、その世代だけを再開する。"""
        if self._kernel.GetProcessId(process_handle) != pid:
            raise OSError("開始したプロセスのhandleとPIDが一致しません")
        if not self._kernel.AssignProcessToJobObject(self._handle, process_handle):
            raise self._error()
        # Popenがprimary threadのhandleを閉じるため、停止中の元世代のthreadを列挙する。
        # process handleを保持している間はPIDが再利用されず、再開前には子も生成されない。
        snapshot = self._kernel.CreateToolhelp32Snapshot(0x00000004, 0)  # TH32CS_SNAPTHREAD
        if snapshot == ctypes.c_void_p(-1).value:
            raise self._error()
        try:
            entry = _ThreadEntry(ctypes.sizeof(_ThreadEntry))
            found = self._kernel.Thread32First(snapshot, ctypes.byref(entry))
            resumed = False
            while found:
                if entry.owner_pid == pid:
                    # THREAD_SUSPEND_RESUME | THREAD_QUERY_LIMITED_INFORMATION。
                    thread = self._kernel.OpenThread(0x0802, False, entry.thread_id)
                    if not thread:
                        raise self._error()
                    try:
                        # 外部からrootが終了されthread IDが再利用されても、別プロセスを再開しない。
                        if self._kernel.GetProcessIdOfThread(thread) != pid:
                            raise OSError("開始したプロセスとthreadの所属PIDが一致しません")
                        if self._kernel.ResumeThread(thread) == 0xFFFFFFFF:
                            raise self._error()
                        resumed = True
                    finally:
                        if not self._kernel.CloseHandle(thread):
                            raise self._error()
                entry = _ThreadEntry(ctypes.sizeof(_ThreadEntry))
                found = self._kernel.Thread32Next(snapshot, ctypes.byref(entry))
            if self._ctypes.get_last_error() != 18:  # ERROR_NO_MORE_FILES
                raise self._error()
            if not resumed:
                raise OSError("開始したプロセスの停止中threadが見つかりません")
        finally:
            if not self._kernel.CloseHandle(snapshot):
                raise self._error()

    def terminate(self, *, timeout: float = 5.0) -> None:
        """Jobの全所属を終了させ、OSのActiveProcessesが0になるまで確認する。"""
        if not self._kernel.TerminateJobObject(self._handle, 1):
            raise self._error()
        deadline = time.monotonic() + timeout
        while True:
            info = _Accounting()
            if not self._kernel.QueryInformationJobObject(self._handle, 1, ctypes.byref(info), ctypes.sizeof(info), None):
                raise self._error()
            if info.active == 0:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Windows Jobに{info.active}件のプロセスが残っています")
            time.sleep(0.01)

    def close(self) -> None:
        """所有するJob handleを1回だけ解放する。"""
        if self._handle:
            if not self._kernel.CloseHandle(self._handle):
                raise self._error()
            self._handle = None

    def _error(self) -> OSError:
        return self._ctypes.WinError(self._ctypes.get_last_error())
