"""メディアキーをWindowsへ送出するモジュール。

`user32.SendInput`を経由してキーボードイベントを注入する。
非Windowsでは`RuntimeError`を送出する。
"""

import ctypes
import sys
from ctypes import wintypes
from typing import Any

# 操作対象キーと仮想キーコード。
# 値はMicrosoft Learn "Virtual-Key Codes"のVK_MEDIA_*/VK_VOLUME_*に対応する。
VK_CODES: dict[str, int] = {
    "play_pause": 0xB3,
    "next": 0xB0,
    "prev": 0xB1,
    "stop": 0xB2,
    "mute": 0xAD,
    "vol_down": 0xAE,
    "vol_up": 0xAF,
}

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", _KEYBDINPUT),
        ("mi", _MOUSEINPUT),
        ("hi", _HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    """`user32.SendInput`の入力配列要素。"""

    _anonymous_ = ("u",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("u", _INPUT_UNION),
    ]


def _build_input(vk: int, flags: int) -> "INPUT":
    """`INPUT`構造体を1件構築する（unionフィールド`ki`を初期化する）。

    `INPUT._anonymous_ = ("u",)`によりunion`u`のサブ`ki`を直接キーワード引数で渡せる。
    """
    return INPUT(
        type=INPUT_KEYBOARD,
        ki=_KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None),
    )


def send_key(name: str, *, user32: Any | None = None) -> None:
    """`name`に対応するメディアキーの押下+解放を1回の`SendInput`で送出する。

    Args:
        name: `VK_CODES`に登録されたキー名。
        user32: `user32.dll`相当のオブジェクトを差し替える注入点（テスト用）。
            既定の`None`では実Windowsの`user32`をロードする。

    Raises:
        RuntimeError: 非Windowsで`user32`未指定のとき。
        KeyError: 未知のキー名のとき。
        OSError: `SendInput`が想定外の戻り値を返したとき。
    """
    if name not in VK_CODES:
        raise KeyError(name)
    if user32 is None:
        if sys.platform != "win32":
            raise RuntimeError("send_key requires Windows")
        user32 = ctypes.WinDLL("user32", use_last_error=True)

    vk = VK_CODES[name]
    # メディアキー（VK_MEDIA_*）と音量キー（VK_VOLUME_*）はいずれも拡張キー扱いで、
    # OSが期待するスキャン形式に揃えるためKEYEVENTF_EXTENDEDKEYを付ける。
    down = _build_input(vk, KEYEVENTF_EXTENDEDKEY)
    up = _build_input(vk, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP)
    inputs = (INPUT * 2)(down, up)
    # ctypesは配列実引数を自動でポインタへ変換するため`byref`不要。
    # テスト用モック側ではPython配列としてそのまま受け取れる利点もある。
    sent = user32.SendInput(2, inputs, ctypes.sizeof(INPUT))
    if sent != 2:
        raise OSError(f"SendInput sent {sent} of 2 events")
