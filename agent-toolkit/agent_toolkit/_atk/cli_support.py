"""`atk`の各サブコマンドのCLIが共有する、引数の型エラーと拒否・Git失敗の報告。"""

import argparse
import pathlib
import subprocess
from collections.abc import Callable

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._common import next_action as _next_action


def argument_type_error(reason: str, next_action: str) -> argparse.ArgumentTypeError:
    """理由と次の操作の行を持つargparse向けの型エラーを返す。"""
    return argparse.ArgumentTypeError(_next_action.with_next_action(reason, next_action))


_SUBCOMMAND_DESTS = (
    "wi_subcommand",
    "process_loop_subcommand",
    "plans_subcommand",
    "review_table_subcommand",
    "review_audit_subcommand",
)
"""リーフサブコマンドの表記を組み立てるときに、トップレベルコマンドへ続けて読むdest名。"""


def command_label(args: argparse.Namespace) -> str:
    """ヘルプを案内するリーフサブコマンドの表記（例: `atk wi add`）を返す。"""
    parts = ["atk", args.command]
    for dest in _SUBCOMMAND_DESTS:
        value = getattr(args, dest, None)
        if isinstance(value, str) and value:
            parts.append(value)
    return " ".join(parts)


def report_rejected(args: argparse.Namespace, error: ValueError | _atk_git_sync.RebaseInProgressError) -> None:
    """入力・状態のエラーを失敗行と次の操作の行で出力する。

    共通の例外型と`RebaseInProgressError`は発生源が決めた次の操作を使う。
    それ以外の`ValueError`は発生源が次の操作を持たないため、受理形式の確認と不具合の報告を案内する。
    """
    if isinstance(error, (_next_action.ActionableError, _atk_git_sync.RebaseInProgressError)):
        next_action = error.next_action
    else:
        next_action = (
            f"`{command_label(args)} --help`で受理形式を確かめて再実行する。"
            "解消しない場合はagent-toolkitの不具合としてユーザーへ報告する"
        )
    _outcome.report_failure(f"操作を拒否した: {error}", next_action=next_action)


def report_git_failure(error: subprocess.CalledProcessError, private_notes: pathlib.Path) -> None:
    """Git操作の失敗を出力する。同期基盤が原因と次の操作を出力済みなら重ねない。"""
    if _atk_git_sync.is_reported(error):
        return
    _outcome.report_failure(
        f"Git操作に失敗した: {error}",
        next_action=f"`git -C {private_notes.resolve()} status`で確認し、元の操作を再実行する",
    )


def run_rejecting_value_error(args: argparse.Namespace, dispatch: Callable[[argparse.Namespace], int]) -> int:
    """`dispatch`を実行し、`ValueError`を失敗行と次の操作の行で報告して終了コード1を返す。"""
    try:
        return dispatch(args)
    except ValueError as error:
        report_rejected(args, error)
        return 1
