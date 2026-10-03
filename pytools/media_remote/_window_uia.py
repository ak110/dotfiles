"""タスクバー上のアプリ選択をUI Automationで判別する。

COMオブジェクトは作成した監視ワーカーだけで使用し、クリックのHWNDを
アプリのHWNDとして使わない。対象アプリは別途WinEventから取得する。
"""

import ctypes
import uuid
from typing import Any, cast


class Point(ctypes.Structure):
    """Windowsの物理画面座標。"""

    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _Guid(ctypes.Structure):
    _fields_ = [("bytes", ctypes.c_ubyte * 16)]


class _Com:
    """SDKのvtable順序に沿う、所有権付きCOM参照。"""

    def __init__(self, pointer: ctypes.c_void_p, call_type: Any) -> None:
        self.pointer = pointer
        self.call_type = call_type

    def call(self, index: int, result: Any, args: tuple[Any, ...], *values: Any) -> Any:
        table = ctypes.cast(self.pointer, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
        function = self.call_type(result, ctypes.c_void_p, *args)(table[index])
        return function(self.pointer, *values)

    def reference(self, index: int, args: tuple[Any, ...], *values: Any) -> "_Com":
        pointer = ctypes.c_void_p()
        _check(self.call(index, ctypes.c_int32, (*args, ctypes.POINTER(ctypes.c_void_p)), *values, ctypes.byref(pointer)))
        return _Com(pointer, self.call_type)

    def number(self, index: int) -> int:
        result = ctypes.c_int32(0)
        _check(self.call(index, ctypes.c_int32, (ctypes.POINTER(ctypes.c_int32),), ctypes.byref(result)))
        return result.value

    def text(self, index: int, oleaut: Any) -> str:
        result = ctypes.c_void_p()
        try:
            _check(self.call(index, ctypes.c_int32, (ctypes.POINTER(ctypes.c_void_p),), ctypes.byref(result)))
            return ctypes.wstring_at(result) if result.value else ""
        finally:
            if result.value:
                oleaut.SysFreeString(result)

    def close(self) -> None:
        if self.pointer.value:
            self.call(2, ctypes.c_uint32, ())
            self.pointer = ctypes.c_void_p()


def _check(hresult: int) -> None:
    if hresult < 0:
        raise OSError(f"UI Automation HRESULT 0x{hresult & 0xFFFFFFFF:08x}")


class UIAutomation:
    """ElementFromPointと祖先からアプリボタン・サムネイルを識別する。"""

    def __init__(self, *, ole32: Any = None, oleaut: Any = None, call_type: Any = None) -> None:
        self.ole32 = ole32 if ole32 is not None else cast(Any, ctypes).WinDLL("ole32")
        self.oleaut = oleaut if oleaut is not None else cast(Any, ctypes).WinDLL("oleaut32")
        self.call_type = call_type if call_type is not None else cast(Any, ctypes).WINFUNCTYPE
        self.automation: _Com | None = None
        self.walker: _Com | None = None
        self.initialized = False
        self.ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        self.ole32.CoInitializeEx.restype = ctypes.c_int32
        self.ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(_Guid),
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_Guid),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self.ole32.CoCreateInstance.restype = ctypes.c_int32
        self.ole32.CoUninitialize.argtypes = []
        self.ole32.CoUninitialize.restype = None
        self.oleaut.SysFreeString.argtypes = [ctypes.c_void_p]
        self.oleaut.SysFreeString.restype = None

    def __enter__(self) -> "UIAutomation":
        _check(self.ole32.CoInitializeEx(None, 0))  # COINIT_MULTITHREADED
        self.initialized = True
        try:
            clsid = _Guid.from_buffer_copy(uuid.UUID("ff48dba4-60ef-4201-aa87-54103eef594e").bytes_le)
            iid = _Guid.from_buffer_copy(uuid.UUID("30cbe57d-d9d0-452a-ab13-7ac5ac4825ee").bytes_le)
            pointer = ctypes.c_void_p()
            _check(self.ole32.CoCreateInstance(ctypes.byref(clsid), None, 1, ctypes.byref(iid), ctypes.byref(pointer)))
            self.automation = _Com(pointer, self.call_type)
            # IUIAutomation.get_RawViewWalkerのSDK vtable位置。
            self.walker = self.automation.reference(16, ())
        except BaseException:
            self.__exit__(None, None, None)
            raise
        return self

    def __exit__(self, *_: Any) -> None:
        if self.walker is not None:
            self.walker.close()
        if self.automation is not None:
            self.automation.close()
        if self.initialized:
            self.ole32.CoUninitialize()
            self.initialized = False

    def is_task_selection(self, point: tuple[int, int], root_class: str) -> bool:
        """通常のボタンや通知領域をアプリ選択として扱わない。"""
        if root_class not in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "TaskListThumbnailWnd"):
            return False
        assert self.automation is not None and self.walker is not None
        element = self.automation.reference(7, (Point,), Point(*point))
        clickable = False
        task_list = False
        try:
            while element.pointer.value:
                # IUIAutomationElementのCurrentControlType、AutomationId、ClassName。
                control_type = element.number(21)
                automation_id = element.text(29, self.oleaut)
                class_name = element.text(30, self.oleaut)
                if automation_id.lower() in ("close", "closebutton"):
                    return False
                clickable |= control_type in (50000, 50007, 50019)  # Button/ListItem/TabItem
                task_list |= class_name in ("MSTaskListWClass", "MSTaskSwWClass")
                task_list |= automation_id.startswith("Appid:")
                task_list |= class_name in ("Taskbar.TaskListButtonAutomationPeer", "TaskListButton")
                if clickable and (task_list or root_class == "TaskListThumbnailWnd"):
                    return True
                parent = self.walker.reference(3, (ctypes.c_void_p,), element.pointer)
                element.close()
                element = parent
            return False
        finally:
            element.close()
