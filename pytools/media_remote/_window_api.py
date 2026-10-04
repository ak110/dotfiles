"""ローカルマウス監視とウィンドウ移動のWin32境界。

ポインター幅の引数・戻り値とWindows固定幅構造体を明示する。
DLLを差し替えれば、Linuxでも登録されたコールバックから検証できる。
"""

import contextlib
import ctypes
import logging
from collections.abc import Callable, Iterator
from typing import Any, cast

from pytools.media_remote import _window_uia

logger = logging.getLogger(__name__)

WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_MBUTTONDOWN = 0x0207
WM_MBUTTONUP = 0x0208
EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_SYSTEM_MINIMIZESTART = 0x0016
EVENT_OBJECT_DESTROY = 0x8001


class Rect(ctypes.Structure):
    """Windowsの画面上の矩形。"""

    _fields_ = [(name, ctypes.c_int32) for name in ("left", "top", "right", "bottom")]


class MouseInput(ctypes.Structure):
    """WH_MOUSE_LLが渡すMSLLHOOKSTRUCT。"""

    _fields_ = [
        ("pt", _window_uia.Point),
        ("mouseData", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("time", ctypes.c_uint32),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _Message(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt", _window_uia.Point),
        ("lPrivate", ctypes.c_uint32),
    ]


class TitleBarInfo(ctypes.Structure):
    """タイトルバー情報のWin32構造体TITLEBARINFO。"""

    _fields_ = [("cbSize", ctypes.c_uint32), ("rcTitleBar", Rect), ("rgstate", ctypes.c_uint32 * 6)]


class WindowsAPI:
    """フックの登録スレッドと位置計算ワーカーが使うAPI。"""

    def __init__(self, *, user32: Any = None, kernel32: Any = None, call_type: Any = None) -> None:
        self.user32 = user32 if user32 is not None else cast(Any, ctypes).WinDLL("user32", use_last_error=True)
        self.kernel32 = kernel32 if kernel32 is not None else cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
        factory = call_type if call_type is not None else cast(Any, ctypes).WINFUNCTYPE
        self.mouse_type = factory(ctypes.c_ssize_t, ctypes.c_int, ctypes.c_size_t, ctypes.c_ssize_t)
        self.event_type = factory(
            None,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.c_int32,
            ctypes.c_int32,
            ctypes.c_uint32,
            ctypes.c_uint32,
        )
        self.thread_id = 0
        self._configure()
        self.click_interval_ms = self.user32.GetDoubleClickTime()

    def _configure(self) -> None:
        pointer = ctypes.c_void_p
        uint = ctypes.c_uint32
        integer = ctypes.c_int32
        signatures = {
            "SetWindowsHookExW": (pointer, [integer, self.mouse_type, pointer, uint]),
            "GetDoubleClickTime": (uint, []),
            "CallNextHookEx": (ctypes.c_ssize_t, [pointer, integer, ctypes.c_size_t, ctypes.c_ssize_t]),
            "UnhookWindowsHookEx": (integer, [pointer]),
            "SetWinEventHook": (pointer, [uint, uint, pointer, self.event_type, uint, uint, uint]),
            "UnhookWinEvent": (integer, [pointer]),
            "GetMessageW": (integer, [ctypes.POINTER(_Message), pointer, uint, uint]),
            "TranslateMessage": (integer, [ctypes.POINTER(_Message)]),
            "DispatchMessageW": (ctypes.c_ssize_t, [ctypes.POINTER(_Message)]),
            "PeekMessageW": (integer, [ctypes.POINTER(_Message), pointer, uint, uint, uint]),
            "PostThreadMessageW": (integer, [uint, uint, ctypes.c_size_t, ctypes.c_ssize_t]),
            "SetThreadDpiAwarenessContext": (pointer, [pointer]),
            "WindowFromPoint": (pointer, [_window_uia.Point]),
            "GetAncestor": (pointer, [pointer, uint]),
            "GetClassNameW": (integer, [pointer, ctypes.c_wchar_p, integer]),
            "IsWindow": (integer, [pointer]),
            "IsWindowVisible": (integer, [pointer]),
            "IsIconic": (integer, [pointer]),
            "IsZoomed": (integer, [pointer]),
            "ShowWindow": (integer, [pointer, integer]),
            "GetWindowRect": (integer, [pointer, ctypes.POINTER(Rect)]),
            "GetTitleBarInfo": (integer, [pointer, ctypes.POINTER(TitleBarInfo)]),
            "GetWindowThreadProcessId": (uint, [pointer, ctypes.POINTER(uint)]),
            "SetWindowPos": (integer, [pointer, pointer, integer, integer, integer, integer, uint]),
        }
        for name, (result, args) in signatures.items():
            function = getattr(self.user32, name)
            function.restype = result
            function.argtypes = args
        self.kernel32.GetCurrentThreadId.argtypes = []
        self.kernel32.GetCurrentThreadId.restype = uint
        self.kernel32.GetModuleHandleW.argtypes = [ctypes.c_wchar_p]
        self.kernel32.GetModuleHandleW.restype = pointer

    @contextlib.contextmanager
    def physical_coordinates(self) -> Iterator[None]:
        """座標を扱うスレッドだけをPer Monitor V2へ切り替える。"""
        previous = self.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise OSError("SetThreadDpiAwarenessContext failed")
        try:
            yield
        finally:
            self.user32.SetThreadDpiAwarenessContext(previous)

    def root_class(self, point: tuple[int, int]) -> str:
        """クリック前の最上位HWNDのクラスを取得する。"""
        hwnd = self.user32.WindowFromPoint(_window_uia.Point(*point))
        root = self.user32.GetAncestor(hwnd, 2) if hwnd else None
        return self.class_name(root) if root else ""

    def class_name(self, hwnd: int | None) -> str:
        """Windowクラス名を取得する。"""
        buffer = ctypes.create_unicode_buffer(256)
        self.user32.GetClassNameW(hwnd, buffer, len(buffer))
        return buffer.value

    def identity(self, hwnd: int) -> tuple[int, int] | None:
        """Shell以外の最上位アプリウィンドウの所有者を取得する。"""
        if not self.user32.IsWindow(hwnd) or not self.user32.IsWindowVisible(hwnd):
            return None
        if self.user32.GetAncestor(hwnd, 2) != hwnd:
            return None
        if self.class_name(hwnd) in ("Shell_TrayWnd", "Shell_SecondaryTrayWnd", "TaskListThumbnailWnd", "Progman", "WorkerW"):
            return None
        process = ctypes.c_uint32(0)
        thread = self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process))
        return (process.value, thread) if thread and process.value else None

    def move(self, hwnd: int, identity: tuple[int, int], point: tuple[int, int]) -> None:
        """復元後のタイトルバー中央を物理画面座標へ配置する。"""
        if self.identity(hwnd) != identity:
            raise OSError("selected window no longer exists")
        if self.user32.IsIconic(hwnd) or self.user32.IsZoomed(hwnd):
            # ShowWindowの戻り値は成功ではなく変更前の可視状態。
            self.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            if self.user32.IsIconic(hwnd) or self.user32.IsZoomed(hwnd):
                raise OSError("selected window could not be restored")
        self._position(hwnd, identity, point)
        # 移動先のDPIによるWM_DPICHANGEDで寸法が変わる場合は、更新された矩形で補正する。
        self._position(hwnd, identity, point)

    def _position(self, hwnd: int, identity: tuple[int, int], point: tuple[int, int]) -> None:
        title = TitleBarInfo(cbSize=ctypes.sizeof(TitleBarInfo))
        window = Rect()
        if not self.user32.GetWindowRect(hwnd, ctypes.byref(window)):
            raise OSError("GetWindowRect failed")
        if not self.user32.GetTitleBarInfo(hwnd, ctypes.byref(title)):
            raise OSError("GetTitleBarInfo failed")
        title_rect = title.rcTitleBar
        if title.rgstate[0] & 0x8001 or title_rect.right <= title_rect.left or title_rect.bottom <= title_rect.top:
            raise OSError("selected window has no visible title bar")
        dx = point[0] - (title_rect.left + title_rect.right) // 2
        dy = point[1] - (title_rect.top + title_rect.bottom) // 2
        if dx == 0 and dy == 0:
            return
        x, y = window.left + dx, window.top + dy
        if self.identity(hwnd) != identity:
            raise OSError("selected window changed during positioning")
        # NOSIZE | NOZORDER | NOACTIVATE。入力先と前後関係を変えない。
        if not self.user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0015):
            raise OSError("SetWindowPos failed")

    def run_hooks(
        self,
        on_mouse: Callable[[int, tuple[int, int], int, str], bool],
        on_window: Callable[[int, int, int], None],
        on_key: Callable[[], None],
        ready: Callable[[], None],
    ) -> None:
        """同じスレッドでフックを登録・受信・解除する。"""
        mouse_hook = None
        keyboard_hook = None
        event_hooks: list[int] = []

        def mouse(code: int, message: int, data: int) -> int:
            if code >= 0 and message in (WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MBUTTONDOWN, WM_MBUTTONUP):
                event = ctypes.cast(data, ctypes.POINTER(MouseInput)).contents
                # LLフックの注入入力を、dotfilesユーザーの選択操作と混ぜない。
                if not event.flags & 1:
                    point = (event.pt.x, event.pt.y)
                    root = self.root_class(point) if message == WM_LBUTTONDOWN else ""
                    if on_mouse(message, point, event.time, root):
                        return 1
            return self.user32.CallNextHookEx(mouse_hook, code, message, data)

        def keyboard(code: int, message: int, data: int) -> int:
            if code >= 0 and message in (0x0100, 0x0104):  # KEYDOWN / SYSKEYDOWN
                on_key()
            return self.user32.CallNextHookEx(keyboard_hook, code, message, data)

        def window(hook: int, event: int, hwnd: int, obj: int, child: int, thread: int, time: int) -> None:
            del hook, thread
            if hwnd and obj == 0 and child == 0:
                on_window(event, hwnd, time)

        mouse_callback = self.mouse_type(mouse)
        keyboard_callback = self.mouse_type(keyboard)
        window_callback = self.event_type(window)
        with self.physical_coordinates():
            message = _Message()
            # PostThreadMessage用のキューを作成してからreadyを通知する。
            self.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 0)
            self.thread_id = self.kernel32.GetCurrentThreadId()
            try:
                module = self.kernel32.GetModuleHandleW(None)
                mouse_hook = self.user32.SetWindowsHookExW(14, mouse_callback, module, 0)
                if not mouse_hook:
                    raise OSError("SetWindowsHookExW failed")
                keyboard_hook = self.user32.SetWindowsHookExW(13, keyboard_callback, module, 0)
                if not keyboard_hook:
                    raise OSError("SetWindowsHookExW for keyboard failed")
                for event in (EVENT_SYSTEM_FOREGROUND, EVENT_SYSTEM_MINIMIZESTART, EVENT_OBJECT_DESTROY):
                    hook = self.user32.SetWinEventHook(event, event, None, window_callback, 0, 0, 2)
                    if not hook:
                        raise OSError("SetWinEventHook failed")
                    event_hooks.append(hook)
                ready()
                while True:
                    result = self.user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                    if result == -1:
                        raise OSError("GetMessageW failed")
                    if result == 0:
                        break
                    self.user32.TranslateMessage(ctypes.byref(message))
                    self.user32.DispatchMessageW(ctypes.byref(message))
            finally:
                for hook in reversed(event_hooks):
                    if not self.user32.UnhookWinEvent(hook):
                        logger.error("ウィンドウ監視の解除に失敗しました。serveを再起動してください。")
                for hook in (keyboard_hook, mouse_hook):
                    if hook and not self.user32.UnhookWindowsHookEx(hook):
                        logger.error("入力監視の解除に失敗しました。serveを再起動してください。")
                self.thread_id = 0

    def stop_hooks(self) -> None:
        """登録スレッド自身にメッセージループの終了を依頼する。"""
        if self.thread_id and not self.user32.PostThreadMessageW(self.thread_id, 0x0012, 0, 0):
            raise OSError("PostThreadMessageW failed")
