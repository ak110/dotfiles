"""Codexでmanaged-temp配下の作業領域を回収する`rm -rf -- <1パス>`だけを承認するPermissionRequest hook。

一時rootの配下にあり、root自身でもリンクを経由する経路でもなく、使用中の保持配布物に属さない対象に限る。
"""

from __future__ import annotations

import json
import os
import pathlib

# 関数の実行時にだけ使う依存は、使用時まで読み込まない。
lazy import ctypes
lazy import shlex
lazy import subprocess
lazy import typing
lazy from ctypes import wintypes

lazy from agent_toolkit._atk import managed_temp as _managed_temp


def _windows_dll(name: str) -> typing.Any:
    """Windows専用DLLを遅延取得する。"""
    return typing.cast(typing.Any, ctypes).WinDLL(name, use_last_error=True)


def _allow() -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": {"behavior": "allow"},
                }
            }
        )
    )


def _tokens(command: str) -> list[str]:
    if os.name == "nt":
        argument_count = ctypes.c_int(0)
        shell32 = _windows_dll("shell32")
        kernel32 = _windows_dll("kernel32")
        command_line_to_argv = shell32.CommandLineToArgvW
        command_line_to_argv.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
        command_line_to_argv.restype = ctypes.POINTER(wintypes.LPWSTR)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        arguments = command_line_to_argv(command, ctypes.byref(argument_count))
        if not arguments:
            raise ValueError("Windowsのコマンド行を解析できない")
        try:
            return [arguments[index] for index in range(argument_count.value)]
        finally:
            kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))
    return shlex.split(command, posix=True)


def _payload_object(payload_text: str) -> dict[str, typing.Any] | None:
    try:
        value = json.loads(payload_text)
    except json.JSONDecodeError, TypeError:
        return None
    return value if isinstance(value, dict) else None


def _cleanup_target(tokens: list[str]) -> pathlib.Path | None:
    """`rm -rf -- <1パス>`の形に厳密に一致する場合だけ対象パスを返す。"""
    if len(tokens) != 4 or tokens[:3] != ["rm", "-rf", "--"]:
        return None
    return pathlib.Path(tokens[3])


def main(payload_text: str) -> int:
    """厳密に一致して回収対象の検証を通過した削除だけを承認する。"""
    payload = _payload_object(payload_text)
    if payload is None or payload.get("hook_event_name") != "PermissionRequest":
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return 0
    try:
        command = tool_input["command"]
        tokens = _tokens(command)
        if os.name == "nt" and command != subprocess.list2cmdline(tokens):
            return 0
        target = _cleanup_target(tokens)
        if target is None or not target.is_absolute():
            return 0
        # root自身・root外・リンクを経由する経路を拒否し、使用中の保持配布物も許可しない。
        if _managed_temp.plugin_root_in_use(_managed_temp.top_level_area(_managed_temp.contained_target(target))):
            return 0
    except OSError, ValueError, _managed_temp.ManagedTempError:
        return 0
    _allow()
    return 0
