"""計画の変更範囲の検証を順番に実行し、失敗した後も残りのコマンドを実行して全件の結果を記録する。"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import TypedDict

from agent_toolkit._atk import managed_temp, run_command
from agent_toolkit._common import next_action
from agent_toolkit._common.atomic_file import atomic_write
from agent_toolkit._common.shell_segments import resolve_execution_segment
from agent_toolkit._common.uv_arguments import executable_name
from agent_toolkit._plan.structure.verification import plan_commands


class PlannedCommand(TypedDict):
    """列挙と実行で共用する、起動前に確定した検証条件。"""

    order: int
    command: str
    argv: list[str]
    cwd: str
    timeout: float


def _pyfltr_completion_failure(command: PlannedCommand, record: dict) -> str | None:
    """pyfltrの検証では、終了コードと別に公開summaryの完了区分を検収する。"""
    invocation = resolve_execution_segment(command["argv"], runtime_options=True)
    if not invocation.resolved:
        return "実行位置を確定できません。起動オプションを確認して検証条件を書き直す"
    if not invocation.tokens or invocation.invocation_kind in {"terminal", "code"}:
        return None
    program, *argv = invocation.tokens
    name = program if invocation.invocation_kind == "module" else executable_name(program)
    if name != "pyfltr" or argv[:1] not in (["run"], ["run-for-agent"], ["fast"], ["ci"]):
        return None
    cwd = pathlib.Path(command["cwd"])
    for directory in invocation.directories:
        cwd = cwd / directory
    output = record.get("stdout_path")
    for index, word in enumerate(argv):
        if word == "--":
            break
        if word.startswith("--output-file="):
            output = word.partition("=")[2]
        elif word == "--output-file" and index + 1 < len(argv):
            output = argv[index + 1]
    try:
        path = pathlib.Path(output) if output else None
        if path is not None and not path.is_absolute():
            path = cwd / path
        summaries = []
        for line in path.read_text(encoding="utf-8").splitlines() if path is not None else []:
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict) and value.get("kind") == "summary":
                summaries.append(value)
    except (OSError, UnicodeError) as error:
        return f"pyfltrの完了結果を読めません: {error}。保存した両出力を確認して検証を再実行する"
    if len(summaries) != 1:
        return "pyfltrの完了結果を一意に取得できません。--output-format=jsonlを付けて検証を再実行する"
    summary = summaries[0]
    if (
        summary.get("completion") != "completed"
        or not isinstance(summary.get("files_reached"), int)
        or summary["files_reached"] < 1
        or not summary.get("completed_commands")
        or any(summary.get(key) for key in ("missing_targets", "incomplete_commands", "fully_excluded_files"))
    ):
        return "pyfltrの検証が対象へ到達して完了していません。summaryの未到達対象と未完了コマンドを解消して再実行する"
    return None


def _summary(payload: dict) -> dict:
    """保存した詳細の所在と、実行順の結果一覧を返す。"""
    return {
        **{key: value for key, value in payload.items() if key != "results"},
        "results": [
            {key: row[key] for key in ("order", "command", "state", "exit_code", "record_path", "failure")}
            for row in payload["results"]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    """全入力を起動前に確かめ、列挙か保存付きの実行結果を返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=pathlib.Path, metavar="PATH", required=True, help="計画ファイルの絶対パス")
    parser.add_argument(
        "--verification-plan",
        type=pathlib.Path,
        metavar="PATH",
        help="訂正後の検証表を持つファイルの絶対パス。指定時は凍結した--planの検証表に優先する",
    )
    parser.add_argument("--worktree", type=pathlib.Path, metavar="DIR", required=True, help="検証するGit worktreeの絶対パス")
    parser.add_argument("--timeout", type=float, required=True, help="各検証の正の有限上限秒数")
    parser.add_argument("--list", action="store_true", help="子を起動せずコマンドとargvを列挙する")
    parser.add_argument("--summary-only", action="store_true", help="詳細を保存し、実行結果の一覧と保存先を表示する")
    parser.add_argument(
        "--records-output",
        type=pathlib.Path,
        metavar="PATH",
        help="保存結果のまとまりの絶対パス。省略するとmanaged-tempへ保存する",
    )
    args = parser.parse_args(argv)
    try:
        if not args.plan.is_absolute() or not args.worktree.is_absolute() or not args.worktree.is_dir():
            raise ValueError("--planと--worktreeは実在する対象の絶対パスで指定する")
        if args.records_output is not None and not args.records_output.is_absolute():
            raise ValueError("--records-outputは絶対パスで指定する")
        if args.verification_plan is not None and not args.verification_plan.is_absolute():
            raise ValueError("--verification-planは絶対パスで指定する")
        command_parser = argparse.ArgumentParser(exit_on_error=False)
        run_command.build_parser(command_parser)
        # run-commandと同じ入力契約でtimeout・cwd・wrapper内のargvを確かめる。
        default = command_parser.parse_args(["--cwd", str(args.worktree), "--timeout", str(args.timeout), "--", "true"])
        planned: list[PlannedCommand] = []
        current_content = args.verification_plan.read_text(encoding="utf-8") if args.verification_plan else None
        for order, (command, child_argv) in enumerate(
            plan_commands(args.plan.read_text(encoding="utf-8"), current_content=current_content), 1
        ):
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
        completion_failure = _pyfltr_completion_failure(planned_command, record) if state == "success" else None
        if completion_failure:
            state, failure = "failure", completion_failure
        results.append(
            {
                **planned_command,
                "state": state,
                "exit_code": exit_code,
                "record_path": record["record_path"],
                "failure": failure,
                "record": record,
            }
        )
        failed = failed or state != "success"
    try:
        output = args.records_output or managed_temp.create_output_temp("plan-verify-results") / "results.json"
        payload = {
            "version": 1,
            "plan_path": str(args.plan),
            "verification_plan_path": str(args.verification_plan or args.plan),
            "results": results,
            "records": [result["record_path"] for result in results if result["record_path"] is not None],
            "unrecorded": [result["order"] for result in results if result["record_path"] is None],
            "results_path": str(output),
        }
        atomic_write(output, json.dumps(payload, ensure_ascii=False) + "\n")
    except (OSError, managed_temp.ManagedTempError) as error:
        next_action.report(str(error), next_action="各実行の保存先を保持し、結果の保存先を直して記録を回復する")
        recovery: dict = {"results": results, "records": [row["record_path"] for row in results if row["record_path"]]}
        if args.summary_only:
            recovery = _summary(recovery)
            recovery.update(
                results_path=None,
                unrecorded=[row["order"] for row in results if row["record_path"] is None],
                # 個別記録も保存できなかった行は、実行条件と両出力の所在をこの返却から回復する。
                recovery=[row for row in results if row["record_path"] is None],
            )
        print(json.dumps(recovery, ensure_ascii=False))
        return 2
    print(json.dumps(_summary(payload) if args.summary_only else payload, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
