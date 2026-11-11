"""レーン資源の公開CLI。"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import time

lazy import psutil

from agent_toolkit._atk import help_text, managed_temp, outcome, run_command
from agent_toolkit._atk import lane_cleanup as _lane_cleanup
from agent_toolkit._atk import lane_create as _lane_create
from agent_toolkit._atk import lane_integration as _lane_integration
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._atk.lane_activity import process_activity as _process_activity
from agent_toolkit._atk.lane_activity import session_activity as _session_activity
from agent_toolkit._atk.lane_activity import worktree_activity as _worktree_activity
from agent_toolkit._atk.lane_cleanup import cleanup_session
from agent_toolkit._atk.lane_create import prepare as _prepare
from agent_toolkit._atk.lane_integration_input import atk_script as _atk_script
from agent_toolkit._atk.lane_records import SESSION_ENV, current_session_id, list_resources
from agent_toolkit._atk.lane_records import git as _git
from agent_toolkit._atk.managed_temp import permissions as temp_permissions
from agent_toolkit._common.wait_schedule import get_wait_timeout
from agent_toolkit._git import command as git_command

__all__ = [
    "SESSION_ENV",
    "_atk_script",
    "_git",
    "_prepare",
    "_process_activity",
    "_session_activity",
    "_worktree_activity",
    "cleanup_session",
    "current_session_id",
    "get_wait_timeout",
    "list_resources",
    "psutil",
    "run_command",
    "time",
    "temp_permissions",
    "git_command",
]


def build_parser(parser: argparse.ArgumentParser) -> None:
    """レーンの作成と、指定sessionの回収を公開する。"""
    children = help_text.add_subcommands(parser, dest="lane_subcommand", required=True)
    create_parser = help_text.add_command(children, "create", **help_text.HELP["atk lane create"])
    create_parser.add_argument("--repo", type=pathlib.Path, required=True, help="対象repoの絶対パス。")
    create_parser.add_argument("--base-branch", required=True, help="統合先branch。")
    create_parser.add_argument("--lane", required=True, help="lane-NN。")
    create_parser.add_argument("--selection-file", type=pathlib.Path, required=True, help="統合状態を読む選定YAML。")
    create_parser.add_argument("--branch", help="専用branch名。省略するとsessionとlaneから決める。")
    create_parser.add_argument(
        "--worktree", type=pathlib.Path, help="作成先の絶対パス。省略すると主作業ツリーの固定作成先を使う。"
    )
    create_parser.add_argument("--commands-file", type=pathlib.Path, help="create・prepare・deleteのargvを持つJSON。")
    create_parser.add_argument("--reuse-record", type=pathlib.Path, help="終了済みの担当が使っていた資源のrecord_path。")
    create_parser.add_argument(
        "--timeout",
        type=_lane_records.positive_timeout,
        default=600,
        help="固有手順・timeoutの無いhook・統合の最終化操作の正の上限秒数。省略すると600秒。",
    )
    create_parser.add_argument("--session-id", help="所有session。省略すると実行環境から解決する。")
    delete_parser = help_text.add_command(children, "delete", **help_text.HELP["atk lane delete"])
    delete_parser.add_argument("--session-id", help="回収する所有session。省略すると実行環境から解決する。")
    delete_parser.add_argument("--list", action="store_true", help="全sessionの登録と前回結果を読み、削除しない。")
    recover_parser = help_text.add_command(children, "recover", **help_text.HELP["atk lane recover"])
    recover_parser.add_argument("--session-id", required=True, help="回復する旧登録の所有session。")
    recover_parser.add_argument("--lane", action="append", default=None, help="対象のlane-NN。省略すると全登録。複数指定可能。")
    integrate_parser = help_text.add_command(children, "integrate", **help_text.HELP["atk lane integrate"])
    integrate_parser.add_argument("--record", type=pathlib.Path, required=True, help="所有登録の絶対パス。")
    integrate_parser.add_argument("--reviewed-head", required=True, help="受理済みレビュー対象HEAD。")
    integrate_parser.add_argument("--target-worktree", type=pathlib.Path, required=True, help="統合先worktreeの絶対パス。")
    integrate_parser.add_argument("--target-branch", required=True, help="所有記録と同じ統合先branch。")
    integrate_parser.add_argument(
        "--manifest",
        type=pathlib.Path,
        required=True,
        help=(
            "JSONのplans/wisは{name,commands:[argv,...]}の順序付き配列、verificationはargv配列。"
            "任意のverification_recordsは同じHEAD・argv・cwd・実行条件で成功した実行記録JSONの絶対パス配列。"
            "計画なしはplans:[]とhandoff:ABS。保存済みplansはcommands:[]とsaved:ABS、延期wisはcommands:[]。"
            "lane_started_atは最初のレーン開始時刻（時差付きISO 8601）。plans/wisのargvの"
            "{{integrated_head_short}}・{{completed_at}}・{{lane_seconds}}と{{expand-file:ABS}}を確定値で展開する。"
            "実装なしのplans/wisにはno_merge:trueを指定する。"
            "adoptを含むwisの行にはcommit_record:ABSで計画か引き継ぎ記録Markdownを指定する。"
        ),
    )
    integrate_parser.add_argument("--no-merge", action="store_true", help="Gitを変更せず計画保存・WI終了だけ行う。")
    integrate_parser.add_argument("--accept-rebased-head", help="対応表と再検証の診断を受理したHEADの完全なOID。")
    integrate_parser.add_argument(
        "--restart-review",
        action="store_true",
        help="Git統合前の入力是正から再開し、最新統合先へ追随する。同条件成功を再利用し、必要な検証後に検収待ちへ戻る。",
    )
    integrate_parser.add_argument("--session-id", help="所有登録のsession（atk lane createの所有session）。")
    integrate_parser.add_argument(
        "--resolved-conflict",
        action="append",
        default=None,
        metavar="OID",
        help=(
            "rebase --continueの後にまず指定なしで再開し、history-compareが変更内容の不一致を示した旧commitだけを指定する。"
            "旧変更を新しい親へ3-way適用した結果と異なるcommitが対象で、周辺の文脈行だけの変化は含まない。"
            "統合先で書き換えられた同一変更として省略を示された旧commitも、成立を確かめた場合だけ指定する。複数指定可能。"
        ),
    )
    integrate_parser.add_argument(
        "--amend-finalization", action="store_true", help="全計画・WIの集合を変えずに、未完了の最終化操作のargvを補完する。"
    )
    integrate_parser.add_argument(
        "--writer-session",
        action="append",
        default=None,
        help=(
            "判定が数える稼働中の担当（両worktreeをcwdとし、書き込む役割か宣言の無い起動）のうち、統合に合意した担当のsession。"
            "呼出元とその委譲元の連鎖、explore、書き込まない役割の担当は指定しなくても数えない。複数指定可能。"
        ),
    )


def dispatch(args: argparse.Namespace) -> int:
    """機械可読な資源と、残存理由を出力する。"""
    if args.lane_subcommand == "delete" and getattr(args, "list", False):
        try:
            listed = _lane_records.list_resources(args.session_id)
            print(json.dumps(listed, ensure_ascii=False))
            return 1 if listed["errors"] else 0
        except (OSError, ValueError) as error:
            outcome.report_failure(str(error), next_action="所有登録の保存先と読取権限を確認する")
            return 2
    session_id = args.session_id or _lane_records.current_session_id()
    if session_id is None:
        outcome.report_failure("所有sessionを解決できない", next_action="--session-idで作成したsessionを指定する")
        return 2
    try:
        if args.lane_subcommand == "create":
            result = _lane_create.create(args, session_id)
        elif args.lane_subcommand == "integrate":
            result = _lane_integration.integrate(args, session_id)
        elif args.lane_subcommand == "recover":
            result = _lane_cleanup.recover(session_id, args.lane)
        else:
            result = _lane_cleanup.cleanup_session(session_id)
        print(json.dumps(result, ensure_ascii=False))
        if result.get("phase") == "awaiting-validation":
            outcome.report_warning("rebase後の検収を待っている", next_action=result["next_action"])
            return 1
        if result.get("target_advanced"):
            outcome.report_warning(
                f"統合先が準備の時点から進んだ: {result['target_advanced']['actual']}", next_action=result["next_action"]
            )
            return 1
        if result.get("retained"):
            if args.lane_subcommand == "recover":
                outcome.report_warning(
                    "回復できない登録を保持した", next_action="retainedの理由を解消し同じlane recoverを再実行せよ"
                )
            else:
                outcome.report_warning(
                    "未回収のレーン資源を保持した", next_action="result_pathの理由を解消し同じlane deleteを再実行せよ"
                )
            return 1
        for missing in result.get("missing", []):
            outcome.report_warning(
                f"実体の無いレーン登録を回収した: lane={missing['lane']}, last_head={missing['last_head']}",
                next_action="消失の原因を確認し、統合されていない成果があれば記録したHEADから復元する",
            )
        outcome.report_success("レーン資源の操作が完了した", outcome.ResultKind.VALUE_OUTPUT)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, managed_temp.ManagedTempError) as error:
        outcome.report_failure(str(error), next_action="入力と所有記録、残った資源を確認して同じ操作を再実行する")
        return 2
