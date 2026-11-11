"""WIサブパーサーが共有する引数・補完・型検証。"""

import argparse
import math
import re

lazy from agent_toolkit._atk import cli_support as _cli_support
lazy from agent_toolkit._atk.wi import constants as _wi_constants
lazy from agent_toolkit._atk.wi import filenames as _wi_filenames

_queue_filename_completer = _wi_filenames.make_filename_completer(_wi_constants.WI_STATES)

_active_filename_completer = _wi_filenames.make_filename_completer(_wi_constants.WI_ACTIVE_STATES)

_editable_filename_completer = _wi_filenames.make_filename_completer(_wi_constants.WI_EDITABLE_STATES)

_removable_filename_completer = _wi_filenames.make_filename_completer(_wi_constants.WI_STATES)

_hold_filename_completer = _wi_filenames.make_filename_completer((_wi_constants.WI_STATE_HOLD,))

_inbox_filename_completer = _wi_filenames.make_filename_completer((_wi_constants.WI_STATE_INBOX,))

_holdable_filename_completer = _wi_filenames.make_filename_completer(
    (
        _wi_constants.WI_STATE_INBOX,
        _wi_constants.WI_STATE_PROCESSING,
        _wi_constants.WI_STATE_ADOPTED,
        _wi_constants.WI_STATE_REJECTED,
    )
)

_returnable_filename_completer = _wi_filenames.make_filename_completer(
    (_wi_constants.WI_STATE_PROCESSING, _wi_constants.WI_STATE_ADOPTED, _wi_constants.WI_STATE_REJECTED)
)

_uwi_filename_completer = _wi_filenames.make_filename_completer(_wi_constants.WI_ACTIVE_STATES, _wi_constants.WI_TYPE_UWI)


def _cooldown_days(value: str) -> int:
    """3以上の再処理抑制日数をargparse向けに検証する。"""
    try:
        days = int(value)
    except ValueError as error:
        raise _cli_support.argument_type_error(f"整数ではない: {value}", "3以上の整数を指定する") from error
    if days < 3:
        raise _cli_support.argument_type_error(f"3未満の値: {value}", "3以上の整数を指定する")
    return days


def _nonnegative_finite_float(value: str) -> float:
    """0以上の有限な浮動小数点数だけをargparseへ渡す。"""
    try:
        parsed = float(value)
    except ValueError as exc:
        raise _cli_support.argument_type_error(f"数値ではない: {value}", "0以上の有限な数値を指定する") from exc
    if not math.isfinite(parsed) or parsed < 0:
        raise _cli_support.argument_type_error(f"0以上の有限な数値ではない: {value}", "0以上の有限な数値を指定する")
    return parsed


def _worktree_name(value: str) -> str:
    """worktree名としてパス逸脱を起こさない値を検証する。"""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None or ".." in value:
        raise _cli_support.argument_type_error(
            f"worktree名として使えない: {value}",
            "worktree名は英数字で始め、英数字・`.`・`_`・`-`だけで指定し、`..`を含めない",
        )
    return value


def _add_important_filter_arg(parser: argparse.ArgumentParser) -> None:
    """一覧・全文表示・検索へ共通の重要フラグの限定を登録する。"""
    parser.add_argument(
        "--important",
        choices=("all", "yes", "no"),
        default="all",
        help="重要フラグで限定する。yesは重要項目、noは非重要項目、allと省略時は全項目を対象とする。",
    )


def _add_mq_read_sync_args(parser: argparse.ArgumentParser) -> None:
    """`atk wi`の読み取り専用サブコマンドへ同期制御オプションを登録する。"""
    sync = parser.add_mutually_exclusive_group()
    sync.add_argument(
        "--skip-pull",
        action="store_true",
        help="remote同期全体をスキップする（ログイン時など軽量参照用）。",
    )
    sync.add_argument(
        "--pull",
        action="store_true",
        help="直近の同期省略判定を上書きしてremote同期を必ず実行する。",
    )


def _add_draft_comparison_arg(parser: argparse.ArgumentParser) -> None:
    """新規・編集のdry-runと本保存へ同じ原稿冒頭の比較入力を登録する。"""
    parser.add_argument(
        "--compare-opening-file",
        metavar="PATH",
        action="append",
        default=None,
        help=(
            "原稿の冒頭を、受領済みのUTF-8抽出ファイルと改行・末尾空行を含めて比較する。"
            "新規では--body-fileと同じ件数・対応順で反復指定し、編集では本文を置き換える--body-fileに対して1件指定する。"
            "--dry-runでは比較してから冒頭を保存・表示し、本保存では比較が一致した場合だけ保存する。"
            "不一致・読取不能・件数違いは非0で終了し、保存しない。"
        ),
    )
