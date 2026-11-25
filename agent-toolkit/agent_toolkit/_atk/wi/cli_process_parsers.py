import pathlib
from typing import Any

lazy from agent_toolkit._atk import help_text as _atk_help


def _add_mq_process_loop_parser(sub: Any) -> None:
    check_alerts = _atk_help.add_command(sub, "check-alerts", **_atk_help.HELP["atk wi check-alerts"])
    check_alerts.add_argument(
        "--target-repo",
        metavar="LOCAL_WORKTREE",
        default=None,
        help="CI監視対象のローカルGit作業ツリー。省略時は現在の作業ツリーを使う。",
    )
    check_alerts.set_defaults(_target_repo_resolved_by_consumer=True)
    check_alerts.add_argument(
        "--forge",
        choices=("auto", "github", "gitlab"),
        default="auto",
        help="CI取得先。autoはgithub.comならGitHub、それ以外はGitLabを使う。",
    )
    loop = _atk_help.add_command(sub, "process-loop", **_atk_help.HELP["atk wi process-loop"])
    loop.set_defaults(_target_repo_multiple=False, _target_repo_allow_all=False, _target_repo_resolved_by_consumer=True)
    loop.add_argument("--once", action="store_true", help="1セッションを起動して、終了後に再起動せず終了する。")
    loop_sub = _atk_help.add_subcommands(loop, dest="process_loop_subcommand", required=False)
    resume = _atk_help.add_command(loop_sub, "resume", **_atk_help.HELP["atk wi process-loop resume"])
    resume.add_argument(
        "value", nargs="?", metavar="ID", help="再開する会話ID。Claude Codeでは必須。省略できる実行系では会話選択画面を開く。"
    )
    _atk_help.add_command(loop_sub, "resume-cancel", **_atk_help.HELP["atk wi process-loop resume-cancel"])
    _atk_help.add_command(loop_sub, "abort", **_atk_help.HELP["atk wi process-loop abort"])
    _atk_help.add_command(loop_sub, "abort-cancel", **_atk_help.HELP["atk wi process-loop abort-cancel"])
    _atk_help.add_command(loop_sub, "status", **_atk_help.HELP["atk wi process-loop status"])
    instruct = _atk_help.add_command(loop_sub, "instruct", **_atk_help.HELP["atk wi process-loop instruct"])
    instruct.add_argument("body", metavar="BODY", help="次に起動する1セッションへ渡す追加指示の本文。")
    _atk_help.add_command(loop_sub, "instruct-cancel", **_atk_help.HELP["atk wi process-loop instruct-cancel"])


def _add_mq_submit_parser(sub: Any) -> None:
    submit = _atk_help.add_command(sub, "submit", **_atk_help.HELP["atk wi submit"])
    operation = submit.add_mutually_exclusive_group(required=True)
    operation.add_argument("--request-file", type=pathlib.Path, help="自己完結した受付JSONのUTF-8ファイル")
    operation.add_argument("--show", metavar="REQUEST_ID", help="受付と同じ実行の処置・未報告を照会する")
    operation.add_argument("--finish", type=pathlib.Path, help="投入専用セッションの要求ごとの結果JSON")
    operation.add_argument("--resume", metavar="REQUEST_ID", help="同じ受付とrun_idの未完了工程を独立監督へ再移管する")
