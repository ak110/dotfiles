"""`atk weekly`: 日曜14時の週枠の作業を判定して実行し、OSのスケジューラへ定時起動を登録する。  pyfltr: allow-comment

`run`は最新の週枠で未完了の作業（メタ分析、外部情報の調査）を前景で1件ずつ実行する。
`atk serve`の定期の判定とOSのスケジューラの両方がこの入口を呼び、判定はセッションの開始・終了時の追いつきと共通にする。
`install`はLinuxではsystemdのユーザータイマー、Windowsではタスクスケジューラへ日曜14時の`atk weekly run`を登録する。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import time

from agent_toolkit._atk import outcome
from agent_toolkit._atk.managed_temp import paths as managed_temp_paths

UNIT_NAME = "atk-weekly"
TASK_NAME = "agent-toolkit-weekly"
CALENDAR = "Sun *-*-* 14:00:00"


def build_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "weekly_action",
        choices=("run", "install", "uninstall"),
        help="run: 未完了の週次の作業を実行する。install・uninstall: OSのスケジューラへの登録と解除。",
    )
    parser.add_argument(
        "--atk-command",
        metavar="PATH",
        default=None,
        help="installで登録する`atk`の絶対パス。省略時はPATHから、プラグインの一時配置以外の`atk`を選ぶ。",
    )


def dispatch(args: argparse.Namespace) -> int:
    if args.weekly_action == "run":
        return run_due()
    if args.weekly_action == "install":
        return install(args.atk_command)
    return uninstall()


def run_due() -> int:
    from agent_toolkit._atk import run_skill, run_skill_batch, weekly_schedule  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    due = [work for work in weekly_schedule.WORKS if run_skill_batch.weekly_auto_start_due(work, now=time.time())]
    if not due:
        outcome.report_success("起動する週次の作業は無い（最新の週枠は完了済みか、このホストで無効）")
        return 0
    code = 0
    for work in due:
        parser = argparse.ArgumentParser()
        run_skill.build_parser(parser)
        args = parser.parse_args(["--args", work, "agent-toolkit:review-session"])
        args.scheduled = True
        result = run_skill.dispatch(args)
        code = code or result
    return code


def _stable_atk(explicit: str | None) -> pathlib.Path:
    if explicit is not None:
        path = pathlib.Path(explicit).expanduser()
        if not path.is_absolute() or not path.is_file():
            raise ValueError(f"--atk-commandには実在する絶対パスを指定する: {explicit}")
        return path
    temporary = managed_temp_paths.temp_root_path()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        found = shutil.which("atk", path=directory)
        if found is None:
            continue
        resolved = pathlib.Path(found).resolve()
        if resolved.is_relative_to(temporary) or ("plugins" in resolved.parts and "cache" in resolved.parts):
            continue
        return pathlib.Path(found)
    raise ValueError("登録できる`atk`がPATHに無い（プラグインの一時配置だけが見つかった）")


def _systemd_directory() -> pathlib.Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(pathlib.Path.home() / ".config")
    return pathlib.Path(base) / "systemd" / "user"


def _systemd_escape(value: str) -> str:
    return value.replace("%", "%%")


def systemd_units(atk: pathlib.Path, path_value: str) -> dict[str, str]:
    quoted = '"' + _systemd_escape(str(atk)).replace("\\", "\\\\").replace('"', '\\"') + '"'
    return {
        f"{UNIT_NAME}.service": (
            "[Unit]\n"
            "Description=agent-toolkit weekly meta analysis and research\n\n"
            "[Service]\n"
            "Type=oneshot\n"
            f'Environment="PATH={_systemd_escape(path_value)}"\n'
            f"ExecStart={quoted} weekly run\n"
        ),
        f"{UNIT_NAME}.timer": (
            "[Unit]\n"
            "Description=Run agent-toolkit weekly works on Sunday 14:00\n\n"
            "[Timer]\n"
            f"OnCalendar={CALENDAR}\n"
            "Persistent=true\n\n"
            "[Install]\n"
            "WantedBy=timers.target\n"
        ),
    }


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def windows_register_script(atk: pathlib.Path) -> str:
    return (
        f"$action = New-ScheduledTaskAction -Execute {_powershell_quote(str(atk))} -Argument 'weekly run'\n"
        "$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '14:00'\n"
        "$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12)\n"
        f"Register-ScheduledTask -TaskName {_powershell_quote(TASK_NAME)} -Action $action -Trigger $trigger "
        "-Settings $settings -Force | Out-Null\n"
    )


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"{' '.join(command[:3])} が終了{result.returncode}: {(result.stderr or result.stdout).strip()[:500]}"
        )


def install(explicit: str | None) -> int:
    try:
        atk = _stable_atk(explicit)
        if sys.platform == "win32":
            _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", windows_register_script(atk)])
            outcome.report_success(f"タスクスケジューラへ日曜14時の`{atk} weekly run`を登録した（{TASK_NAME}）")
            return 0
        directory = _systemd_directory()
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in systemd_units(atk, os.environ.get("PATH", "")).items():
            (directory / name).write_text(content, encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        _run(["systemctl", "--user", "enable", "--now", f"{UNIT_NAME}.timer"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        outcome.report_failure(
            f"週次の定時起動を登録できない: {error}",
            next_action="`--atk-command`へ更新で消えない`atk`の絶対パスを指定するか、診断の原因を直して再実行する",
        )
        return 1
    outcome.report_success(f"systemdのユーザータイマー{UNIT_NAME}.timerへ日曜14時の`{atk} weekly run`を登録した")
    return 0


def uninstall() -> int:
    try:
        if sys.platform == "win32":
            _run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    f"Unregister-ScheduledTask -TaskName {_powershell_quote(TASK_NAME)} -Confirm:$false",
                ]
            )
        else:
            directory = _systemd_directory()
            timer = directory / f"{UNIT_NAME}.timer"
            if timer.exists():
                _run(["systemctl", "--user", "disable", "--now", f"{UNIT_NAME}.timer"])
            for name in (f"{UNIT_NAME}.timer", f"{UNIT_NAME}.service"):
                (directory / name).unlink(missing_ok=True)
            _run(["systemctl", "--user", "daemon-reload"])
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        outcome.report_failure(f"週次の定時起動を解除できない: {error}", next_action="診断の原因を直して再実行する")
        return 1
    outcome.report_success("週次の定時起動の登録を解除した")
    return 0


SERVE_CHECK_SECONDS = 15 * 60


class ServeLauncher:
    def __init__(self) -> None:
        self._process: subprocess.Popen[bytes] | None = None

    def launch(self) -> bool:
        from agent_toolkit._atk import orchestrator  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        from agent_toolkit._common import (  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
            diagnostic_process,
            plugin_environment,
            session_review,
        )

        if self._process is not None and self._process.poll() is None:
            return False
        root = pathlib.Path(__file__).resolve().parents[2]
        log_path = session_review.directory() / "weekly" / "serve-run.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = plugin_environment.direct_environment(root, orchestrator.child_env(drop=(session_review.BATCH_ENV,)))
        with log_path.open("ab") as log:
            self._process = subprocess.Popen(  # noqa: S603  # pylint: disable=consider-using-with  # 自身の配布物のCLIを固定の引数で起動する。
                diagnostic_process.command([sys.executable, str(root / "agent_toolkit" / "atk.py"), "weekly", "run"]),
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
            )
        return True
