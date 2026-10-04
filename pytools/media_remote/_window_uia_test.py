"""UI AutomationのCOM境界を通したタスクバー選択の判別。"""

import ctypes
from typing import Any
from unittest import mock

import pytest

from pytools.media_remote import _window_uia


class _Interface(ctypes.Structure):
    _fields_ = [("vtable", ctypes.POINTER(ctypes.c_void_p))]


class _COMEnvironment:
    """SDKのABIで応答するCOMオブジェクトを保持する。"""

    def __init__(self, nodes: list[tuple[int, str, str]]) -> None:
        self.owned: list[Any] = []
        self.released: list[int] = []
        self.parents: dict[int, int] = {}
        self.ole32 = mock.Mock()
        self.oleaut = mock.Mock()
        self.ole32.CoInitializeEx.return_value = 0
        self.pointers: list[int] = []
        for number, aid, name in nodes:
            functions = {
                21: (ctypes.c_int32, (ctypes.POINTER(ctypes.c_int32),), self.integer(number)),
                29: (ctypes.c_int32, (ctypes.POINTER(ctypes.c_void_p),), self.string(aid)),
                30: (ctypes.c_int32, (ctypes.POINTER(ctypes.c_void_p),), self.string(name)),
            }
            self.pointers.append(self.interface(37, functions))
        for index, pointer in enumerate(self.pointers):
            self.parents[pointer] = self.pointers[index + 1] if index + 1 < len(self.pointers) else 0

        def parent(this: int, element: int, out: Any) -> int:
            del this
            out[0] = self.parents[element]
            return 0

        walker = self.interface(4, {3: (ctypes.c_int32, (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)), parent)})

        def from_point(this: int, point: _window_uia.Point, out: Any) -> int:
            del this
            assert (point.x, point.y) == (-10, 30)
            out[0] = self.pointers[0]
            return 0

        automation = self.interface(
            17,
            {
                7: (ctypes.c_int32, (_window_uia.Point, ctypes.POINTER(ctypes.c_void_p)), from_point),
                16: (ctypes.c_int32, (ctypes.POINTER(ctypes.c_void_p),), self.reference(walker)),
            },
        )

        def create(clsid: Any, outer: Any, context: int, iid: Any, out: Any) -> int:
            del clsid, outer, iid
            assert context == 1
            ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p)).contents.value = automation
            return 0

        self.ole32.CoCreateInstance.side_effect = create

    def interface(self, size: int, functions: dict[int, Any]) -> int:
        table = (ctypes.c_void_p * size)()

        def release(this: int) -> int:
            self.released.append(this)
            return 0

        functions[2] = (ctypes.c_uint32, (), release)
        for index, (result, args, function) in functions.items():
            callback = ctypes.CFUNCTYPE(result, ctypes.c_void_p, *args)(function)
            table[index] = ctypes.cast(callback, ctypes.c_void_p).value
            self.owned.append(callback)
        obj = _Interface(table)
        self.owned.extend((table, obj))
        return ctypes.addressof(obj)

    def integer(self, value: int) -> Any:
        def read(this: int, out: Any) -> int:
            del this
            out[0] = value
            return 0

        return read

    def string(self, value: str) -> Any:
        buffer = ctypes.create_unicode_buffer(value)
        self.owned.append(buffer)

        def read(this: int, out: Any) -> int:
            del this
            out[0] = ctypes.addressof(buffer)
            return 0

        return read

    @staticmethod
    def reference(pointer: int) -> Any:
        def read(this: int, out: Any) -> int:
            del this
            out[0] = pointer
            return 0

        return read


@pytest.mark.parametrize(
    "root,nodes,expected",
    [
        ("Shell_TrayWnd", [(50000, "Appid:example", "Button")], True),
        ("Shell_SecondaryTrayWnd", [(50020, "", "Text"), (50000, "", "Button"), (50033, "", "MSTaskListWClass")], True),
        ("TaskListThumbnailWnd", [(50007, "", "Thumbnail")], True),
        ("Shell_TrayWnd", [(50000, "Start", "Button"), (50033, "", "Shell_TrayWnd")], False),
        ("Shell_TrayWnd", [(50000, "NotifyIcon", "Button"), (50033, "", "TrayNotifyWnd")], False),
        ("Shell_TrayWnd", [(50033, "", "MSTaskListWClass")], False),
        ("TaskListThumbnailWnd", [(50000, "CloseButton", "Button")], False),
        ("Application", [(50000, "Appid:example", "Button")], False),
    ],
)
def test_uia_distinguishes_task_selection_and_releases_references(root: str, nodes: Any, expected: bool) -> None:
    environment = _COMEnvironment(nodes)
    with _window_uia.UIAutomation(ole32=environment.ole32, oleaut=environment.oleaut, call_type=ctypes.CFUNCTYPE) as uia:
        assert uia.is_task_selection((-10, 30), root) is expected
    environment.ole32.CoUninitialize.assert_called_once()
    assert len(environment.released) >= 2
    if root != "Application":
        assert environment.pointers[0] in environment.released
        assert environment.oleaut.SysFreeString.call_count >= 2


def test_uia_creation_failure_balances_com_initialization() -> None:
    environment = _COMEnvironment([(50000, "Appid:example", "Button")])
    environment.ole32.CoCreateInstance.side_effect = None
    environment.ole32.CoCreateInstance.return_value = -2147467259
    with (
        pytest.raises(OSError, match="HRESULT"),
        _window_uia.UIAutomation(ole32=environment.ole32, oleaut=environment.oleaut, call_type=ctypes.CFUNCTYPE),
    ):
        pytest.fail("COM生成の失敗を成功として扱った")
    environment.ole32.CoUninitialize.assert_called_once()
