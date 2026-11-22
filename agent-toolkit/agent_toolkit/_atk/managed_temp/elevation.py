"""対話的なupdate-dotfilesからだけ、通常掃引で権限不足だった対象をまとめて再処理する。"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import pathlib
import subprocess
import sys

from agent_toolkit._atk.managed_temp import paths, sweep


def _encoded(value: str, encoding: str = "utf-8") -> str:
    return base64.b64encode(value.encode(encoding)).decode("ascii")


def _windows_command(names: list[str]) -> list[str]:
    # 候補はデータとして渡し、PowerShellやPythonの式へ展開しない。
    payload = _encoded(json.dumps(names, ensure_ascii=True))
    deletion = f"""
import base64, json, os, shutil, sys
failed = False
for name in json.loads(base64.b64decode({payload!r})):
    try:
        if os.path.isdir(name):
            shutil.rmtree(name)
        else:
            os.unlink(name)
    except FileNotFoundError:
        pass
    except OSError as error:
        print(error, file=sys.stderr)
        failed = True
sys.exit(failed)
"""
    command = f"exec(__import__('base64').b64decode('{_encoded(deletion)}'))"
    command = command.replace("'", "''")
    executable = str(pathlib.Path(sys.executable).resolve()).replace("'", "''")
    launch = (
        f"$p = Start-Process -FilePath '{executable}' -ArgumentList '-I -S -c \"{command}\"' "
        "-WorkingDirectory (Get-Location).Path -Verb RunAs -Wait -PassThru -ErrorAction Stop; "
        "exit $p.ExitCode"
    )
    powershell = pathlib.Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", _encoded(launch, "utf-16-le")]


def _command(names: list[str]) -> list[str]:
    if os.name == "nt":
        return _windows_command(names)
    return ["/usr/bin/sudo", "--", "/bin/rm", "-rf", "--one-file-system", "--", *names]


def sweep_for_update(*, no_sudo: bool, logger: logging.Logger) -> None:
    """通常権限で掃引し、TTYのある手動更新でだけ昇格を求める。"""
    if not sys.stdin.isatty() or os.environ.get("AGENT_TOOLKIT_PROCESS_LOOP_SESSION") == "1":
        return
    result = sweep.sweep_managed_temp(now=datetime.datetime.now(datetime.UTC), force=True)
    for target in result.deleted:
        logger.info("管理一時資源を回収: %s", target)
    candidates: list[pathlib.Path] = []
    if not no_sudo and result.permission_candidates:
        root = paths.temp_root_path()
        candidates = [path for path in result.permission_candidates if path.parent == root]
        if candidates:
            logger.warning("権限不足で残った管理一時資源%d件の回収に、管理者の承認が必要です。", len(candidates))
            try:
                completed = subprocess.run(_command([path.name for path in candidates]), cwd=root, check=False)
                if completed.returncode:
                    logger.warning("昇格による回収を完了できませんでした（終了コード%d）。", completed.returncode)
            except OSError as error:
                logger.warning("昇格による回収を起動できませんでした: %s", error)
    removed = {target for target in candidates if not os.path.lexists(target)}
    for target in sorted(removed):
        logger.info("昇格による回収後に対象の不在を確認: %s", target)
    remaining = {
        target: reason for target, reason in result.failed if target not in removed and removed.isdisjoint(target.parents)
    }
    for target, reason in remaining.items():
        logger.warning(
            "管理一時資源を回収できませんでした: %s: %s。解消後に通常のupdate-dotfilesを再実行してください。",
            target,
            reason,
        )
