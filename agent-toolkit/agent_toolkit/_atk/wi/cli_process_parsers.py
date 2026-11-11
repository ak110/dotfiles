"""WIのprocess-loop・投入セッションの引数登録。"""

import argparse
import pathlib
from typing import Any

from agent_toolkit._atk.wi.cli_parser_support import _worktree_name
lazy from agent_toolkit._atk import help_text as _atk_help


def _add_mq_process_loop_parser(sub: Any) -> None:
    """`atk wi process-loop`サブコマンドを登録する。"""
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
    loop.add_argument(
        "--once",
        action="store_true",
        help="登録全体の対象集合を1回処理して終了する。対象0件なら待機せず終了し、未完了対象が残れば非0で終了する。",
    )
    loop.add_argument(
        "--worktree",
        nargs="?",
        const="process-loop",
        default=None,
        type=_worktree_name,
        metavar="NAME",
        help=(
            "対象リポジトリ配下の.claude/worktrees/<NAME>にworktreeを準備してセッションを起動する。"
            "NAME省略時はprocess-loopを使う。dotfilesリポジトリでは未指定でも自動有効となり、"
            "指定時はworktree名だけを上書きする。"
        ),
    )
    loop.add_argument(
        "--no-update",
        action="store_true",
        help="セッション完了後と待機中のどちらの場合もupdate-dotfilesの実行とprocess-loop自身の再起動を行わない。",
    )
    loop.add_argument(
        "--internal-mise-refreshed",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    loop.add_argument(
        "--internal-dotfiles-updated",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    loop.add_argument(
        "--no-alerts",
        action="store_true",
        help=(
            "待機中と各セッションの開始前のCI失敗の検出と、待機中の未判定のDependabotアラートによる"
            "process-wiの実行を無効化する（指定しない場合は有効）。"
        ),
    )
    loop.add_argument(
        "--alert-interval",
        type=float,
        default=1800.0,
        metavar="SECONDS",
        help="アラート確認の最短間隔秒数。省略時は1800秒間隔とする。",
    )
    loop.add_argument(
        "--alert-forge",
        choices=("auto", "github", "gitlab"),
        default="auto",
        help="アラート検出対象のホスティング種別。省略時はautoとしてrepo_idのhostから自動判定する。",
    )
    resume_group = loop.add_mutually_exclusive_group()
    resume_group.add_argument(
        "--resume",
        default=None,
        metavar="SESSION_ID",
        help=(
            "指定した統括SESSION_IDに属する登録先の未完了処理を初回に再開する。"
            "IDは必須。同じprocess-wiの実行の未完了対象は同じ会話で継続する。"
        ),
    )
    resume_group.add_argument(
        "--auto-resume",
        action="store_true",
        help=(
            "登録先の未完了所有記録を初回に再開する。統括IDが1つなら同じ会話を使い、"
            "複数なら新しい統括へ対象別の履歴を渡す。--resumeとは同時指定できない。"
        ),
    )
    loop_sub = _atk_help.add_subcommands(loop, dest="process_loop_subcommand", required=False)
    _atk_help.add_command(loop_sub, "abort", **_atk_help.HELP["atk wi process-loop abort"])
    _atk_help.add_command(loop_sub, "abort-cancel", **_atk_help.HELP["atk wi process-loop abort-cancel"])
    _atk_help.add_command(loop_sub, "status", **_atk_help.HELP["atk wi process-loop status"])
    instruct = _atk_help.add_command(loop_sub, "instruct", **_atk_help.HELP["atk wi process-loop instruct"])
    instruct.add_argument("body", metavar="BODY", help="次に起動する1セッションへ渡す追加指示の本文。")
    _atk_help.add_command(loop_sub, "instruct-cancel", **_atk_help.HELP["atk wi process-loop instruct-cancel"])


def _add_mq_submission_parser(sub: Any) -> None:
    """`atk wi submission`サブコマンドを登録する。"""
    submission = _atk_help.add_command(sub, "submission", **_atk_help.HELP["atk wi submission"])
    submission_sub = _atk_help.add_subcommands(submission, dest="submission_subcommand")
    begin = _atk_help.add_command(submission_sub, "begin", **_atk_help.HELP["atk wi submission begin"])
    begin.add_argument(
        "--target-repo",
        metavar="REPO",
        action="append",
        default=None,
        required=True,
        help="投入先のリポジトリ（ローカル作業ツリーのパスまたは正規化リモートURL）。複数の投入先は繰り返し指定する。",
    )
    end = _atk_help.add_command(submission_sub, "end", **_atk_help.HELP["atk wi submission end"])
    end.add_argument(
        "--findings-file",
        metavar="PATH",
        default=None,
        help="投入を終えた場合の指摘対応記録（JSON）の絶対パス。形式は本helpの末尾に示す。",
    )
    end.add_argument(
        "--review-result",
        metavar="PATH",
        action="append",
        default=None,
        help="指摘の対応を検査する投入前レビューの返却ファイルの絶対パス。複数の返却は繰り返し指定する。",
    )
    end.add_argument("--abort", action="store_true", help="ユーザーが投入作業を中止した場合に、検査せずに記録を削除する。")
    for parser in (begin, end):
        parser.add_argument(
            "--session-id",
            metavar="SESSION_ID",
            default=None,
            help="記録する会話のID。省略時は`CLAUDE_CODE_SESSION_ID`、`CODEX_THREAD_ID`の順に使う。",
        )
    submit = _atk_help.add_command(sub, "submit", **_atk_help.HELP["atk wi submit"])
    operation = submit.add_mutually_exclusive_group(required=True)
    operation.add_argument("--request-file", type=pathlib.Path, help="自己完結した受付JSONのUTF-8ファイル")
    operation.add_argument("--show", metavar="REQUEST_ID", help="受付と同じ実行の処置・未報告を照会する")
    operation.add_argument("--finish", type=pathlib.Path, help="投入専用セッションの要求ごとの結果JSON")
    operation.add_argument("--resume", metavar="REQUEST_ID", help="同じ受付とrun_idの未完了工程を独立監督へ再移管する")
