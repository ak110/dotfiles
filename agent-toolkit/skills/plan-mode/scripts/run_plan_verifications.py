"""計画の変更範囲の検証を順番に実行し、失敗後も残る結果を記録する。"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import TypedDict

from agent_toolkit._atk import run_command
from agent_toolkit._common import next_action
from agent_toolkit._plan.structure.verification import plan_commands


class PlannedCommand(TypedDict):
    """列挙と実行で共用する、起動前に確定した検証条件。"""

    order: int
    command: str
    argv: list[str]
    cwd: str
    timeout: float


def main(argv: list[str] | None = None) -> int:
    """全入力を起動前に確かめ、列挙か保存付きの実行結果を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=pathlib.Path, metavar="PATH", required=True, help="計画ファイルの絶対パス")
    parser.add_argument("--worktree", type=pathlib.Path, metavar="DIR", required=True, help="検証するGit worktreeの絶対パス")
    parser.add_argument("--timeout", type=float, required=True, help="各検証の正の有限上限秒数")
    parser.add_argument("--list", action="store_true", help="子を起動せずコマンドとargvを列挙する")
    args = parser.parse_args(argv)
    try:
        if not args.plan.is_absolute() or not args.worktree.is_absolute() or not args.worktree.is_dir():
            raise ValueError("--planと--worktreeは実在する対象の絶対パスで指定する")
        command_parser = argparse.ArgumentParser(exit_on_error=False)
        run_command.build_parser(command_parser)
        # run-commandと同じ入力契約でtimeout・cwd・wrapper内のargvを確かめる。
        default = command_parser.parse_args(["--cwd", str(args.worktree), "--timeout", str(args.timeout), "--", "true"])
        planned: list[PlannedCommand] = []
        for order, (command, child_argv) in enumerate(plan_commands(args.plan.read_text(encoding="utf-8")), 1):
            cwd, timeout = default.cwd, default.timeout
            if pathlib.Path(child_argv[0]).name in {"atk", "atk.cmd"} and child_argv[1:2] == ["run-command"]:
                wrapped = command_parser.parse_args(child_argv[2:])
                if not wrapped.command_argv or wrapped.command_argv[0] != "--" or len(wrapped.command_argv) == 1:
                    raise ValueError("run-commandの--以後へ子argvを指定する")
                child_argv = wrapped.command_argv[1:]
                cwd, timeout = wrapped.cwd or cwd, wrapped.timeout or timeout
            planned.append({"order": order, "command": command, "argv": child_argv, "cwd": str(cwd), "timeout": timeout})
    except (OSError, ValueError, argparse.ArgumentError) as error:
        next_action.report(
            str(error),
            next_action=error.next_action
            if isinstance(error, next_action.ActionableError)
            else "計画の検証行・絶対パス・正の有限timeoutを確認し、--listで列挙してから再実行する",
        )
        return 2
    if args.list:
        print(json.dumps(planned, ensure_ascii=False))
        return 0
    results = []
    failed = False
    for planned_command in planned:
        record, exit_code, failure = run_command.execute(
            planned_command["argv"], pathlib.Path(planned_command["cwd"]), planned_command["timeout"]
        )
        state = "timeout" if record["timed_out"] else "failure" if exit_code else "success"
        results.append(
            {
                **planned_command,
                "state": state,
                "exit_code": exit_code,
                "record_path": record["record_path"],
                "failure": failure,
            }
        )
        failed = failed or exit_code != 0
    print(json.dumps(results, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
