"""Codexの管理対象一時領域cleanupだけを承認するPermissionRequest hook。"""

from __future__ import annotations

import ctypes
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import typing
from ctypes import wintypes

import _managed_temp


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
            raise ValueError("Windows commandを解析できない")
        try:
            return [arguments[index] for index in range(argument_count.value)]
        finally:
            kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))
    return shlex.split(command, posix=True)


def _payload_object(payload_text: str) -> dict[str, typing.Any] | None:
    try:
        value = json.loads(payload_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _is_trusted_atk_launcher(plugin_root: pathlib.Path) -> bool:
    """PATH上のatkが配布済みの安定ランチャーか判定する。"""
    executable = shutil.which("atk")
    if executable is None:
        return False
    launcher_name = "atk.cmd" if os.name == "nt" else "atk"
    candidates = (
        pathlib.Path.home() / ".local" / "bin" / launcher_name,
        plugin_root / "bin" / launcher_name,
    )
    resolved = pathlib.Path(executable).resolve(strict=True)
    for candidate in candidates:
        try:
            if resolved == candidate.resolve(strict=True):
                return True
        except OSError:
            continue
    return False


def _cleanup_target(
    tokens: list[str],
    *,
    plugin_root: pathlib.Path,
    helper: pathlib.Path,
) -> pathlib.Path | None:
    """許可対象の新旧cleanupコマンドから対象パスを返す。"""
    if tokens[:4] == ["atk", "managed-temp", "cleanup", "--path"] and len(tokens) == 5:
        return pathlib.Path(tokens[4]) if _is_trusted_atk_launcher(plugin_root) else None
    if len(tokens) != 8:
        return None
    if tokens[:5] != ["uv", "run", "--no-project", "--script", str(helper)]:
        return None
    if tokens[5:7] != ["cleanup", "--path"]:
        return None
    return pathlib.Path(tokens[7])


def main(payload_text: str) -> int:
    """厳密に一致して所有権検証を通過したcleanupだけを承認する。"""
    payload = _payload_object(payload_text)
    if payload is None or payload.get("hook_event_name") != "PermissionRequest":
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict) or not isinstance(tool_input.get("command"), str):
        return 0
    plugin_root_value = os.environ.get("PLUGIN_ROOT")
    if not plugin_root_value:
        return 0
    try:
        plugin_root = pathlib.Path(plugin_root_value).resolve(strict=True)
        helper = (plugin_root / "scripts" / "_managed_temp.py").resolve(strict=True)
        command = tool_input["command"]
        tokens = _tokens(command)
        if os.name == "nt" and command != subprocess.list2cmdline(tokens):
            return 0
        target = _cleanup_target(tokens, plugin_root=plugin_root, helper=helper)
        if target is None:
            return 0
        if not target.is_absolute():
            return 0
        _managed_temp.validate_managed_temp(target)
    except (OSError, ValueError, _managed_temp.ManagedTempError):
        return 0
    _allow()
    return 0
