"""登録されたWin32コールバックと、復元後のタイトルバー配置の契約。"""

import ctypes
from collections.abc import Callable
from typing import Any

import pytest

from pytools.media_remote import _window_api, _window_uia


class _Function:
    def __init__(self, action: Any) -> None:
        self.action = action
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> Any:
        return self.action(*args)


class _Desktop:
    """API応答を所有し、ウィンドウ位置を実際に更新するテスト境界。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.procedures: dict[str, _Function] = {}
        self.restored = False
        self.iconic = False
        self.zoomed = False
        self.window = (100, 100, 500, 400)
        self.title = (110, 100, 490, 130)
        self.valid = True
        self.failed = ""
        self.mouse: Any = None
        self.key: Any = None
        self.window_event: Any = None
        self.dispatch: Callable[[], None] = lambda: None
        self.hook_count = 0
        self.unhooked: list[int] = []
        self.dpi_changed = False

    def __getattr__(self, name: str) -> _Function:
        if name not in self.procedures:
            self.procedures[name] = _Function(lambda *args: self.invoke(name, args))
        return self.procedures[name]

    def invoke(self, name: str, args: tuple[Any, ...]) -> Any:
        self.calls.append((name, args))
        if name == self.failed:
            return 0
        match name:
            case "SetThreadDpiAwarenessContext":
                return 1
            case "GetModuleHandleW":
                return 0x123456780
            case "GetCurrentThreadId":
                return 71
            case "GetDoubleClickTime":
                return 500
            case "GetAncestor":
                return args[0]
            case "GetClassNameW":
                args[1].value = "Application"
                return 11
            case "GetWindowThreadProcessId":
                ctypes.cast(args[1], ctypes.POINTER(ctypes.c_uint32)).contents.value = 17
                return 18
            case "IsWindow" | "IsWindowVisible":
                return int(self.valid)
            case "IsIconic":
                return int(self.iconic)
            case "IsZoomed":
                return int(self.zoomed)
            case "ShowWindow":
                self.iconic = self.zoomed = False
                self.restored = True
                self.window = (-900, 200, -300, 700)
                self.title = (-890, 200, -310, 240)
                return 0  # 以前不可視でも復元の成功を0で返す。
            case "GetWindowRect":
                for field, value in zip(("left", "top", "right", "bottom"), self.window, strict=True):
                    setattr(ctypes.cast(args[1], ctypes.POINTER(_window_api.Rect)).contents, field, value)
                return 1
            case "GetTitleBarInfo":
                for field, value in zip(("left", "top", "right", "bottom"), self.title, strict=True):
                    setattr(ctypes.cast(args[1], ctypes.POINTER(_window_api.TitleBarInfo)).contents.rcTitleBar, field, value)
                return 1
            case "SetWindowPos":
                dx, dy = args[2] - self.window[0], args[3] - self.window[1]
                self.window = tuple(v + (dx if i % 2 == 0 else dy) for i, v in enumerate(self.window))
                self.title = tuple(v + (dx if i % 2 == 0 else dy) for i, v in enumerate(self.title))
                if self.dpi_changed:
                    self.dpi_changed = False
                    self.title = (self.title[0], self.title[1], self.title[2] + 100, self.title[3] + 10)
                return 1
            case "SetWindowsHookExW":
                if args[0] == 13:
                    self.key = args[1]
                    return 0x12345678A
                self.mouse = args[1]
                return 0x123456781
            case "SetWinEventHook":
                self.hook_count += 1
                self.window_event = args[3]
                return 0x123456781 + self.hook_count
            case "UnhookWindowsHookEx" | "UnhookWinEvent":
                self.unhooked.append(args[0])
                return 1
            case "GetMessageW":
                self.dispatch()
                return 0
            case "CallNextHookEx":
                return 0x123456789
            case _:
                return 1


@pytest.fixture(name="desktop")
def _desktop() -> tuple[_Desktop, _window_api.WindowsAPI]:
    desktop = _Desktop()
    return desktop, _window_api.WindowsAPI(user32=desktop, kernel32=desktop, call_type=ctypes.CFUNCTYPE)


@pytest.mark.parametrize("iconic,zoomed", [(True, False), (False, True), (False, False)])
def test_serve_restores_before_positioning(desktop: Any, iconic: bool, zoomed: bool) -> None:
    system, api = desktop
    system.iconic, system.zoomed = iconic, zoomed
    with api.physical_coordinates():
        api.move(100, (17, 18), (-400, 250))
    assert system.restored == (iconic or zoomed)
    assert (system.title[0] + system.title[2]) // 2 == -400
    assert (system.title[1] + system.title[3]) // 2 == 250
    position_calls = [args for name, args in system.calls if name == "SetWindowPos"]
    assert len(position_calls) == 1
    assert position_calls[0][0] == 100
    assert position_calls[0][-1] == 0x0015


@pytest.mark.parametrize("failed", ["GetWindowRect", "GetTitleBarInfo", "SetWindowPos"])
def test_move_rejects_geometry_or_position_failure(desktop: Any, failed: str) -> None:
    system, api = desktop
    system.failed = failed
    with pytest.raises(OSError, match=failed):
        api.move(100, (17, 18), (-400, 250))
    assert all(args[0] == 100 for name, args in system.calls if name == "SetWindowPos")


def test_move_rejects_disappeared_or_reused_window(desktop: Any) -> None:
    system, api = desktop
    with pytest.raises(OSError, match="no longer exists"):
        api.move(100, (99, 98), (-400, 250))
    system.valid = False
    with pytest.raises(OSError, match="no longer exists"):
        api.move(100, (17, 18), (-400, 250))
    assert not any(name == "SetWindowPos" for name, _ in system.calls)


def test_move_realigns_after_target_monitor_dpi_change(desktop: Any) -> None:
    system, api = desktop
    system.dpi_changed = True
    with api.physical_coordinates():
        api.move(100, (17, 18), (-400, 250))
    assert (system.title[0] + system.title[2]) // 2 == -400
    assert (system.title[1] + system.title[3]) // 2 == 250


def test_registered_callbacks_keep_pointer_width_and_pass_unhandled_input(desktop: Any) -> None:
    system, api = desktop
    windows: list[tuple[int, int, int]] = []
    observed: list[Any] = []
    keys: list[bool] = []

    def mouse(message: int, point: tuple[int, int], time: int, root: str) -> bool:
        observed.append((message, point, time, root))
        return message == _window_api.WM_MBUTTONDOWN

    def dispatch() -> None:
        data = _window_api.MouseInput(pt=_window_uia.Point(-500, 400), time=91)
        address = ctypes.addressof(data)
        assert system.mouse(0, 0x207, address) == 1
        assert system.mouse(-1, 0x207, address) == 0x123456789
        data.flags = 1
        assert system.mouse(0, 0x207, address) == 0x123456789
        system.window_event(0x123456782, 3, 0x123456799, 0, 0, 1, 93)
        system.window_event(0x123456782, 3, 0x123456799, -4, 0, 1, 93)
        assert system.key(0, 0x0104, address) == 0x123456789
        assert system.key(-1, 0x0104, address) == 0x123456789

    system.dispatch = dispatch
    api.run_hooks(mouse, lambda *args: windows.append(args), lambda: keys.append(True), lambda: None)
    assert observed == [(0x207, (-500, 400), 91, "")]
    assert windows == [(3, 0x123456799, 93)]
    assert keys == [True]
    assert system.unhooked == [0x123456784, 0x123456783, 0x123456782, 0x12345678A, 0x123456781]
    assert api.thread_id == 0


def test_partial_registration_failure_releases_mouse_hook(desktop: Any) -> None:
    system, api = desktop
    system.failed = "SetWinEventHook"
    with pytest.raises(OSError, match="SetWinEventHook"):
        api.run_hooks(lambda *args: False, lambda *args: None, lambda: None, lambda: None)
    assert system.unhooked == [0x12345678A, 0x123456781]


def test_restore_failure_never_moves(desktop: Any) -> None:
    system, api = desktop
    system.iconic = True
    system.failed = "ShowWindow"
    with pytest.raises(OSError, match="restored"):
        api.move(100, (17, 18), (400, 250))
    assert not any(name == "SetWindowPos" for name, _ in system.calls)
