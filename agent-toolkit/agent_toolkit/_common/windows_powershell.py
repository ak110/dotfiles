"""Windows PowerShell 5.1で配布スクリプトを案内できるかを判定する。"""

import os
import subprocess
import sys
from pathlib import Path

lazy from agent_toolkit._common import process_tree


def _execution_policy() -> str:
    """通常の設定はレジストリから読み、グループポリシーがある場合と設定が無い場合はPowerShellに照会する。"""
    if sys.platform != "win32":
        return ""
    import winreg  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,import-error

    machine_user = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
    for hive in machine_user:
        try:
            with winreg.OpenKey(hive, r"Software\Policies\Microsoft\Windows\PowerShell") as key:
                winreg.QueryValueEx(key, "EnableScripts")
        except FileNotFoundError:
            continue
        break
    else:
        policy = os.environ.get("PSEXECUTIONPOLICYPREFERENCE", "")
        if policy and policy.casefold() != "undefined":
            return policy
        for hive in reversed(machine_user):
            try:
                with winreg.OpenKey(hive, r"Software\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") as key:
                    policy = str(winreg.QueryValueEx(key, "ExecutionPolicy")[0])
            except FileNotFoundError:
                continue
            if policy and policy.casefold() != "undefined":
                return policy
    system_root = os.environ.get("SYSTEMROOT")
    if not system_root:
        return ""
    shell = Path(system_root) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    result = process_tree.run_with_timeout(
        [str(shell), "-NoProfile", "-NonInteractive", "-Command", "Get-ExecutionPolicy"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=2,
        check=False,
    )
    return result.stdout.decode("ascii", errors="replace").strip() if result.returncode == 0 else ""


def can_run_script(script: Path) -> bool:
    """印の欠如と許可された実効ポリシーを確認できた場合だけ真を返す。"""
    if not script.is_file():
        return False
    try:
        try:
            with Path(f"{script}:Zone.Identifier").open("rb"):
                return False
        except FileNotFoundError:
            return _execution_policy().casefold() in {"remotesigned", "unrestricted", "bypass"}
    except OSError, subprocess.TimeoutExpired:
        return False
