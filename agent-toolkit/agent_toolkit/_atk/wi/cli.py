"""`atk wi`の登録入口、引数検証とコマンド実行。"""

import argparse
import datetime
import pathlib
import subprocess
import sys

from agent_toolkit._atk.wi.cli_mutation_parsers import _add_mq_edit_parsers, _add_mq_transition_parsers, _add_wi_add_parser
from agent_toolkit._atk.wi.cli_process_parsers import _add_mq_process_loop_parser, _add_mq_submission_parser
from agent_toolkit._atk.wi.cli_read_parsers import _add_mq_read_parsers, _add_mq_search_and_answer_parsers
lazy from agent_toolkit._atk import cli_support as _cli_support
lazy from agent_toolkit._atk import git_sync as _atk_git_sync
lazy from agent_toolkit._atk import help_text as _atk_help
lazy from agent_toolkit._atk import outcome as _outcome
lazy from agent_toolkit._atk.wi import add as _add
lazy from agent_toolkit._atk.wi import alerts as _alerts
lazy from agent_toolkit._atk.wi import batch as _batch
lazy from agent_toolkit._atk.wi import constants as _wi_constants
lazy from agent_toolkit._atk.wi import effect_check as _effect_check
lazy from agent_toolkit._atk.wi import grep as _grep
lazy from agent_toolkit._atk.wi import listing as _listing
lazy from agent_toolkit._atk.wi import process_loop as _process_loop
lazy from agent_toolkit._atk.wi import process_loop_control as _process_loop_control
lazy from agent_toolkit._atk.wi import repo as _wi_repo
lazy from agent_toolkit._atk.wi import show as _show
lazy from agent_toolkit._atk.wi import submission as _submission
lazy from agent_toolkit._atk.wi import submit as _submit
lazy from agent_toolkit._atk.wi import sync as _wi_sync
lazy from agent_toolkit._atk.wi import uwi as _uwi
lazy from agent_toolkit._atk.wi.mutations import content as _mutation_content
lazy from agent_toolkit._atk.wi.mutations import dependencies as _mutation_dependencies
lazy from agent_toolkit._atk.wi.mutations import targets as _mutation_targets
lazy from agent_toolkit._atk.wi.mutations import transitions as _mutation_transitions
lazy from agent_toolkit._common import private_notes as _private_notes


def build_parser(mq: argparse.ArgumentParser) -> None:
    """`atk wi`のサブパーサ配下にキュー操作のサブコマンドを登録する。"""
    sub = _atk_help.add_subcommands(
        mq,
        dest="wi_subcommand",
        required=False,
        show_help_when_missing=True,
    )
    for register in (
        _add_wi_add_parser,
        _add_mq_read_parsers,
        _add_mq_transition_parsers,
        _add_mq_edit_parsers,
        _add_mq_search_and_answer_parsers,
        _add_mq_process_loop_parser,
        _add_mq_submission_parser,
    ):
        register(sub)


_BULK_TRANSITION_SUBCOMMANDS = (
    "start-processing",
    "hold",
    "unhold",
    "return-to-inbox",
    "adopt",
    "reject",
    "rm",
)

"""`--all`とフィルター系引数を受理する状態遷移サブコマンド。"""


def _validate_bulk_transition_args(args: argparse.Namespace) -> None:
    """状態遷移サブコマンドの個別指定と一括指定が排他的であることを検証する。"""
    if args.command != "wi" or args.wi_subcommand not in _BULK_TRANSITION_SUBCOMMANDS:
        return
    if args.all:
        if args.filenames:
            args.subparser.error("FILENAMEと--allは同時に指定できません。")
        if args.target_repo is None:
            args.subparser.error(
                "--allの対象リポジトリを確定できません。"
                "カレントディレクトリを対象リポジトリの作業ツリーへ移すか、--target-repoを指定してください。"
            )
        if getattr(args, "state", None) is not None:
            args.subparser.error("--stateは--allと併用できません。")
        if getattr(args, "if_inbox", False):
            args.subparser.error("--if-inboxは--allと併用できません。")
        return
    if not args.filenames:
        args.subparser.error("対象のFILENAME、または--allを指定してください。")
    if args.yes:
        args.subparser.error("--yesは--allとともに指定してください。")
    if args.skip_pull:
        args.subparser.error("--skip-pullは--allとともに指定してください。")
    non_all_filters = args.type != ["all"] or args.status != ["active"] or args.answered != ["all"] or args.source is not None
    if non_all_filters:
        args.subparser.error("--type・--status・--answered・--sourceは--allとともに指定してください。")


def _resolve_wi_target_repo(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """`--target-repo`を指定しない場合の対象と`all`の受理可否を確定する。

    確定後の`args.target_repo`は、Noneが対象の非限定を、値が対象リポジトリを表す。
    未指定のときはカレントディレクトリが属するリポジトリを対象とし、解決できない場合は非限定とする。
    `all`は非限定の指定として受理し、単一の対象リポジトリを確定するサブコマンドでは拒否する。
    未指定時の解決を自ら行うサブコマンドへはこの関数で対象を補完せず、`all`の拒否だけを適用する。
    対象を補完した実行では`args.target_repo_defaulted`を真にする。
    明示指定と対象の補完を区別できないと、対象を一意に指定した照会まで通常の除外処理が適用される。
    """
    if args.command != "wi" or not hasattr(args, "target_repo"):
        return
    if getattr(args, "configured_repos", False):
        if args.target_repo is not None:
            parser.error("--configured-reposと--target-repoは同時に指定できません。")
        return
    raw = args.target_repo
    values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
    if _wi_repo.TARGET_REPO_ALL in values:
        if not getattr(args, "_target_repo_allow_all", False):
            parser.error(
                f"--target-repo={_wi_repo.TARGET_REPO_ALL}は、単一の対象リポジトリを確定する"
                f"`atk wi {args.wi_subcommand}`では指定できません。"
            )
        args.target_repo = None
        return
    if values or getattr(args, "_target_repo_resolved_by_consumer", False):
        return
    current = _wi_repo.detect_current_repo_id()
    if current is None:
        return
    args.target_repo = [current] if getattr(args, "_target_repo_multiple", False) else current
    args.target_repo_defaulted = True


def _normalize_repeatable_wi_filters(args: argparse.Namespace) -> None:
    """一覧・表示・状態遷移の反復可能フィルターへ、指定がない場合に使う従来の値を設定する。"""
    if args.command != "wi" or args.wi_subcommand not in {"list", "show", *_BULK_TRANSITION_SUBCOMMANDS}:
        return
    args.type = args.type or ["all"]
    args.status = args.status or ["active"]
    args.answered = args.answered or ["all"]


def _resolve_note_file(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """メモファイルを状態変更より前にUTF-8として解決する。"""
    note_file = getattr(args, "note_file", None)
    if note_file is None:
        return
    try:
        args.note = note_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        parser.error(
            f"--note-fileをUTF-8として読み込めません: {note_file}: {error}。"
            "対象ファイルの存在・読取権限・UTF-8の文字コードを確認し、"
            "修正後に同じ--note-file付きコマンドを再実行してください"
        )


def _validate_add_args(args: argparse.Namespace) -> None:
    """`atk wi add`の`--batch`併用制約を検証し、種別を指定しない場合の値を確定する。

    `--type`を省略した場合は`None`を設定することで、`--batch`との併用判定で明示指定
    （`--type=awi`を含む）を区別する。検証後に通常のadd処理が省略時に使う`awi`へ正規化する。
    """
    if args.batch and args.dry_run:
        args.subparser.error("--dry-runは--batchと併用できません。")
    if args.batch:
        conflicting = [
            name
            for name, value in (
                ("--type", args.type),
                ("--scope", args.scope),
                ("--question-type", args.question_type),
                ("--choices", args.choices),
                ("--depends-on", args.depends_on),
                ("--cooldown-until", args.cooldown_until),
                ("--target-repo", args.target_repo),
                ("--source", args.source),
                ("REPO_PATH", args.repo_path_override),
            )
            if value is not None
        ]
        if conflicting:
            args.subparser.error(f"{'・'.join(conflicting)}は--batchと併用できません。")
    if args.type is None:
        args.type = _wi_constants.WI_TYPE_AWI


def _pull_command(private_notes: pathlib.Path) -> None:
    """private-notesを排他ロック内で明示的に同期する。"""
    with _wi_sync.repo_lock(private_notes):
        _wi_sync.pull(private_notes)
        has_remote = _atk_git_sync.has_remote(private_notes)
    result = "remoteと同期した" if has_remote else "remoteかupstreamが無いため同期していない"
    _outcome.report_success(f"private-notesを{result}: {private_notes.resolve()}")


def prepare_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """反復可能なフィルターの省略時の値、対象リポジトリおよびメモファイルを確定する。

    managed-tempの掃引より前に呼ぶ。引数の誤りを掃引の警告より先に報告するためである。
    """
    if args.command == "wi" and args.wi_subcommand == "list" and args.snapshot_output and args.new_since:
        args.subparser.error("--snapshot-outputと--new-sinceは併用できません。開始時集合は初回だけ保存する。")
    if args.command == "wi" and args.wi_subcommand == "grep":
        if args.pattern is not None and args.regexp is not None:
            args.subparser.error("PATTERNと-eは併用できません。すべての検索語を-e PATTERNで反復指定する。")
        if args.pattern is None and args.regexp is None:
            args.subparser.error("検索パターンがありません。PATTERNを1個、または-e PATTERNを1回以上指定する。")
    _normalize_repeatable_wi_filters(args)
    _resolve_wi_target_repo(args, parser)
    _resolve_note_file(args, parser)


def _validate_uwi_add_args(args: argparse.Namespace) -> None:
    """`atk wi add`のUWI専用の引数と問いの形式を検証する。"""
    if args.type != _wi_constants.WI_TYPE_UWI:
        uwi_only = [
            name
            for name, value in (
                ("--scope", args.scope),
                ("--question-type", args.question_type),
                ("--choices", args.choices),
            )
            if value is not None
        ]
        if uwi_only:
            args.subparser.error(f"{'・'.join(uwi_only)}は--type=uwiでのみ指定できます。")
        return
    if args.question_type not in _wi_constants.NEW_QUESTION_TYPES:
        # 選択肢の検証をargparseへ任せると、不正値の案内に問いの分け方と投入の形が現れない。
        _outcome.report_failure(
            f"--type=uwiの--question-typeが不正か未指定のため保存しなかった（指定値: {args.question_type}）",
            next_action=(
                "問いごとに個別のUWIとし、選択肢から選ぶ問いは`--question-type=choice --choices A,B,C`、"
                "2択の可否を問う問いは`--question-type=yes-no`で投入する。選択肢に無い回答は回答欄で受け取る"
            ),
        )
        sys.exit(2)
    if args.question_type == _wi_constants.QUESTION_TYPE_CHOICE and not args.choices:
        args.subparser.error("--question-type=choice のときは --choices を指定してください。")


def validate_args(args: argparse.Namespace) -> None:
    """一括指定の排他と`atk wi add`の引数の組み合わせを検証する。managed-tempの掃引の後に呼ぶ。"""
    _validate_bulk_transition_args(args)
    if args.wi_subcommand == "edit" and args.compare_opening_file is not None and (args.body_file is None or args.append):
        args.subparser.error(
            "--compare-opening-fileは本文を置き換える編集（FILENAMEと--body-file、--appendなし）で指定してください。"
        )
    if args.wi_subcommand == "add":
        _validate_add_args(args)
        _validate_uwi_add_args(args)


def dispatch(
    args: argparse.Namespace,
    *,
    home: pathlib.Path,
    now: datetime.datetime,
) -> int:
    """`atk wi`のサブコマンドを実行し、終了コードを返す。"""
    sub = args.wi_subcommand
    process_loop_subcommand = getattr(args, "process_loop_subcommand", None)
    if sub == "process-loop" and process_loop_subcommand is not None:
        # これらの操作はprivate-notesを必要としないため、環境の用意より前で処理する。
        process_loop_state_dispatch = {
            "abort": _process_loop_control.cmd_process_loop_abort,
            "abort-cancel": _process_loop_control.cmd_process_loop_abort_cancel,
            "status": _process_loop_control.cmd_process_loop_status,
            "instruct": lambda: _process_loop_control.cmd_process_loop_instruct(args.body),
            "instruct-cancel": _process_loop_control.cmd_process_loop_instruct_cancel,
        }
        process_loop_state_dispatch[process_loop_subcommand]()
        return 0
    if sub == "submission":
        # 会話別の状態ディレクトリだけを扱い、private-notesを必要としない。
        return _submission.cmd_submission(args)
    if sub == "submit":
        return _submit.dispatch(args)
    private_notes = (
        _private_notes.default_private_notes(home)
        if sub in ("add", "edit") and args.dry_run
        else _wi_sync.ensure_environment(home)
    )
    commands = {
        "add": lambda: (
            _batch.cmd_add_batch(args, private_notes, now, home) if args.batch else _add.cmd_add(args, private_notes, now, home)
        ),
        "list": lambda: _listing.cmd_list(args, private_notes),
        "show": lambda: _show.cmd_show(args, private_notes),
        "start-processing": lambda: _mutation_transitions.cmd_start_processing(args, private_notes, now),
        "hold": lambda: _mutation_transitions.cmd_hold(args, private_notes, now),
        "unhold": lambda: _mutation_transitions.cmd_unhold(args, private_notes, now),
        "return-to-inbox": lambda: _mutation_transitions.cmd_return_to_inbox(args, private_notes, now),
        "adopt": lambda: _mutation_transitions.cmd_adopt(args, private_notes, now),
        "reject": lambda: _mutation_transitions.cmd_reject(args, private_notes, now),
        "rm": lambda: _mutation_transitions.cmd_rm(args, private_notes),
        "edit": lambda: _mutation_content.cmd_edit(args, private_notes),
        "effect-check": lambda: _effect_check.cmd_effect_check(args, private_notes),
        "set-dependencies": lambda: _mutation_dependencies.cmd_set_dependencies(args, private_notes),
        "grep": lambda: _grep.cmd_grep(args, private_notes),
        "answer": lambda: _uwi.cmd_answer(args, private_notes),
        "commit": lambda: _mutation_targets.cmd_commit(private_notes),
        "pull": lambda: _pull_command(private_notes),
        "process-loop": lambda: _process_loop.cmd_process_loop(args, private_notes),
        "check-alerts": lambda: _alerts.cmd_check_alerts(args, private_notes, now),
    }
    _atk_git_sync.reset_sync_attempt()
    try:
        exit_code = commands[sub]() or 0
    except (ValueError, _atk_git_sync.RebaseInProgressError) as error:
        _cli_support.report_rejected(args, error)
        return 1
    except subprocess.CalledProcessError as error:
        _cli_support.report_git_failure(error, private_notes)
        return 1
    return _wi_sync.sync_exit_code(
        exit_code,
        private_notes,
        # サブコマンド名では決めない。同じサブコマンドでも、保存しない検証のように何も書き込まずに終わる実行がある。
        should_check=_atk_git_sync.sync_attempted() and not getattr(args, "skip_push", False),
    )
