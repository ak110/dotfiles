#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "argcomplete",
#   "watchdog>=6.0.0",
#   "platformdirs>=4.0",
#   "filelock>=3.30",
#   "pytilpack[quart]>=1.47.0",
#   "pyyaml",
#   "markdown-it-py[linkify]>=4.0.0",
#   "mdit-py-plugins>=0.6",
# ]
# ///
"""agent-toolkitプラグイン提供CLI`atk`のPEP 723 entrypoint。

サブコマンド構成は`atk mq <sub>`・`atk plans <sub>`・`atk serve`・`atk config <sub>`・`atk wait-schedule`・
`atk managed-temp <sub>`・`atk worktree-stash <sub>`・`atk watch`・`atk review-table <sub>`形式とする。
フィードバックとTBDを平坦なメッセージキューとして扱い、種別はfrontmatterの`type`で識別する。

- mq add/list/show: エントリの投入・一覧・本文表示。
  `mq add --batch`は`mq show --all`の出力形式を原文保持で一括取り込みする（移行・復元用途）
- mq grep: 本文全体を正規表現で検索し`<ファイル名>:<行番号>:<該当行>`形式で列挙する
- mq start-processing/return-to-inbox/adopt/reject/rm/commit: エントリの状態遷移・削除・コミット
- mq convert-to-plan/set-dependencies: 既存フィードバックの計画実装型への変換・明示依存の更新
- mq edit: MESSAGEによる非対話編集又は$EDITORによる保存ファイル全体の編集
- mq answer: TBDへの回答
- mq process-loop: `orchestrate_model`設定に従いClaude Code又はCodexの新規セッションへ`/goal`で完遂条件を設定して常駐実行する。
  初回の`--resume`は再開後のプロンプト入力をユーザーへ委ねる。
  待機中は既定でCI失敗・Dependabotアラートを自動検出しフィードバック投入する（`--no-alerts`で無効化）
- config show/get/set: XDG関連パス・工程別モデル設定の確認・変更
- plans commit/migrate: 計画bundleの対象限定commit・pushと旧保存先からの一括移行
- managed-temp create/cleanup: 管理対象一時領域の作成・後始末
- watch: 作業ツリーの差分件数・HEADと成果物ファイルの行数・最終更新からの経過秒を1行で出力する
- wait-schedule: request bucketと公開情報から委譲待機用のcron式を1行で出力する

ハンドラ実装は`_atk_mq_add`・`_atk_mq_batch`・`_atk_mq_list`・`_atk_mq_show`・`_atk_mq_mutations`・
`_atk_mq_process_loop`・`_atk_mq_tbd`の各補助モジュールに分割し、
本モジュールはargparse定義・dispatch・エントリポイントを保持する。
"""

import argparse
import datetime
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

# 兄弟モジュール（_atk_mq_*.py）を絶対importで解決するためsys.pathへ同一ディレクトリを挿入する。
# sys.path挿入前の相対解決を避けるため、モジュール内importはこの下に配置する。
# pylint: disable=wrong-import-position,protected-access
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import _atk_config as _config_cmd  # noqa: E402
import _atk_git_sync  # noqa: E402
import _atk_mq_add as _add  # noqa: E402
import _atk_mq_batch as _batch  # noqa: E402
import _atk_mq_common as _common  # noqa: E402
import _atk_mq_grep as _grep  # noqa: E402
import _atk_mq_list as _list  # noqa: E402
import _atk_mq_mutations as _mutations  # noqa: E402
import _atk_mq_process_loop as _process_loop  # noqa: E402
import _atk_mq_show as _show  # noqa: E402
import _atk_mq_tbd as _tbd  # noqa: E402
import _atk_plans as _plans  # noqa: E402
import _atk_watch as _watch  # noqa: E402
import _atk_worktree_stash as _worktree_stash  # noqa: E402
import _managed_temp  # noqa: E402
import _review_table  # noqa: E402
import _wait_schedule  # noqa: E402

_queue_filename_completer = _common.make_filename_completer(_common.MQ_STATES)
_processable_filename_completer = _common.make_filename_completer(_common.MQ_PROCESSABLE_STATES)
_convert_to_plan_filename_completer = _common.make_filename_completer(
    (_common.MQ_STATE_INBOX, _common.MQ_STATE_PROCESSING, _common.MQ_STATE_PLANNING)
)
_removable_filename_completer = _common.make_filename_completer(
    (_common.MQ_STATE_INBOX, _common.MQ_STATE_PROCESSING, _common.MQ_STATE_PLANNING)
)
_hold_filename_completer = _common.make_filename_completer((_common.MQ_STATE_HOLD,))
_inbox_filename_completer = _common.make_filename_completer((_common.MQ_STATE_INBOX,))
_processing_filename_completer = _common.make_filename_completer((_common.MQ_STATE_PROCESSING,))
_tbd_filename_completer = _common.make_filename_completer(_common.MQ_PROCESSABLE_STATES, _common.MQ_TYPE_TBD)


def _cooldown_days(value: str) -> int:
    """3以上の再処理抑制日数をargparse向けに検証する。"""
    try:
        days = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("3以上の整数を指定してください") from error
    if days < 3:
        raise argparse.ArgumentTypeError("3以上の整数を指定してください")
    return days


def _extract_legacy_repo_path(argv: list[str]) -> tuple[list[str], str | None]:
    """`mq add`のサブコマンド名直後のトークンが実在ディレクトリの場合、argparseへ渡す前に取り除く。

    REPO_PATH位置引数廃止後の後方互換のため、argparse解析前の生argvへ適用する。
    `messages`側のnargs="*"単一positionalでは、オプションで分断され前後2箇所に分かれた
    位置引数を一括で解決できない（argparseの既知の制約）ため、サブコマンド名直後という
    先頭位置に限定して抽出することで後続のオプション・MESSAGE位置を通常解析に委ねる。
    """
    if len(argv) < 2 or (argv[0], argv[1]) != ("mq", "add"):
        return argv, None
    candidate_index = 2
    value_options = {
        "--type",
        "--source",
        "--scope",
        "--question-type",
        "--choices",
        "--target-repo",
        "--plan-file",
        "--depends-on",
        "--body-file",
    }
    while candidate_index < len(argv) and argv[candidate_index].startswith("-"):
        option = argv[candidate_index].split("=", 1)[0]
        if "=" not in argv[candidate_index] and option in value_options:
            candidate_index += 2
        else:
            candidate_index += 1
    if candidate_index >= len(argv):
        return argv, None
    candidate = argv[candidate_index]
    if candidate.startswith("-") or not candidate:
        # 空文字列は`Path("").expanduser()`がカレントディレクトリ（常に実在）へ解決され、
        # 本文としての空メッセージ（TBDの空質問等）を誤ってREPO_PATHと誤認するため除外する。
        return argv, None
    candidate_path = pathlib.Path(candidate).expanduser()
    if not _common.is_existing_dir(candidate_path):
        return argv, None
    new_argv = argv[:candidate_index] + argv[candidate_index + 1 :]
    return new_argv, str(candidate_path)


def _source_filter_type(value: str) -> str:
    """`--source`の値を検証するargparse `type=`コールバック。

    先頭`!`を除いた残りが空文字列の場合（`--source=`・`--source=!`）は`ArgumentTypeError`を送出する。
    """
    remainder = value[1:] if value.startswith("!") else value
    if not remainder:
        raise argparse.ArgumentTypeError("空文字列は指定できません（例: --source=session-review）")
    return value


def _port_type(value: str) -> int:
    """`--port`の値を1から65535までの整数として検証する。"""
    try:
        port = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("portは1から65535までの整数で指定してください") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("portは1から65535までの整数で指定してください")
    return port


def _worktree_name(value: str) -> str:
    """worktree名としてパス逸脱を起こさない値を検証する。"""
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value) is None or ".." in value:
        raise argparse.ArgumentTypeError(
            "worktree名は英数字で始め、英数字・`.`・`_`・`-`だけで指定し、`..`を含めないでください"
        )
    return value


def _add_source_arg(parser: argparse.ArgumentParser) -> None:
    """`--source`オプションを共通形式で登録する。"""
    parser.add_argument(
        "--source",
        metavar="NAME",
        type=_source_filter_type,
        default=None,
        help=(
            "投入元識別子（frontmatterのsource）で限定する。完全一致の値、"
            "または先頭に`!`を付けた否定指定（無指定エントリも対象に含む）を指定する。"
            "空文字列（`--source=`・`--source=!`）は拒否する。"
            "例: --source=session-review、--source=!session-review"
        ),
    )


def _add_target_repo_arg(
    parser: argparse.ArgumentParser,
    *,
    help_extra: str = "",
    required: bool = False,
) -> None:
    """`--target-repo`オプションを共通形式で登録する。"""
    parser.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        required=required,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターまたは検証する。" + help_extra,
    )


def _add_mq_read_sync_args(parser: argparse.ArgumentParser) -> None:
    """読み取り専用`mq`サブコマンドの同期制御オプションを登録する。"""
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


def _add_mq_add_parser(sub: Any) -> None:
    """投入サブコマンドを登録する。"""
    add = sub.add_parser("add", help="エントリをinboxへ投入する")
    add.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="*",
        help=(
            "投入する本文（省略時は$EDITORで編集する）。--type=feedback（既定）・tbdで種別を切り替える。"
            "対象リポジトリは省略時にカレントworktree、ローカルパス指定時に指定worktree、"
            "正規化リモートURL指定時にローカルHEADを持たないリポジトリ識別子として解決する。"
            "メッセージ先頭がYAML frontmatter形式の場合はtarget_repo・sourceをCLIオプションより優先する。"
        ),
    )
    add.add_argument(
        "--body-file",
        metavar="PATH",
        action="append",
        default=None,
        help=(
            "本文を記載したファイルのパス。複数回指定すると複数件を投入する。"
            "MESSAGE位置引数とは併用できない。"
            "引用符・改行を含む長文をシェルのエスケープを介さずに渡す場合に使う。"
        ),
    )
    add.add_argument(
        "--batch",
        action="store_true",
        help=(
            "`atk mq show --all`の出力形式で複数エントリを一括登録する。"
            "移行・復元用途であり、frontmatter・本文を原文保持で取り込み"
            "（target_commitの再取得・TBD見出しの再生成を行わない）、"
            "ファイル名は取り込み先と衝突しない限り元名を維持する。"
            "対象リポジトリは各エントリのfrontmatterのtarget_repoだけを用いる。"
            "--type・--scope・--question-type・--choices・--plan-file・--depends-on・"
            "--target-repo・--sourceとは併用できない。"
            "show形式は可逆な直列化ではないため、本文が完全なshow形式エントリの引用を含む場合に"
            "エントリ境界を誤って分割し得る点と、元ファイル末尾の改行の有無・連続空行・"
            "構造見出し（`# feedback`・`# tbd`・`## target_repo: ...`）と同形の末尾行を"
            "復元できない点は限界として許容する。"
            "改行はCRLF・単独CRを含む入力もLFへ正規化して保存する。"
        ),
    )
    add.add_argument("--type", choices=("feedback", "tbd"), default=None)
    add.add_argument("--scope", metavar="NAME", default=None)
    add.add_argument("--question-type", choices=("free-form", "yes-no", "choice"), default=None)
    add.add_argument("--choices", metavar="A,B,C", default=None)
    add.add_argument(
        "--plan-file",
        metavar="PATH",
        default=None,
        help=(
            "計画ファイルの絶対パス。指定するとフィードバックを計画実装型として確定記録する。"
            "--type=feedback（既定）でのみ指定でき、指定したパスは実在を検証する。"
            "メッセージfrontmatterが対象リポジトリを別の値へ上書きする入力とは併用できない。"
            "計画ファイルのベースコミットは作成時点の参照値として保持し、投入先の`target_commit`とは照合しない。"
        ),
    )
    add.add_argument(
        "--depends-on",
        metavar="FILENAME",
        action="append",
        default=None,
        help="フィードバックが処理完了を待つキュー項目。--type=feedbackでのみ指定でき、複数回指定できる。",
    )
    add.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help=(
            "投入元の識別子（任意。frontmatterに source: <NAME> として記録する。既知値: "
            "session-review・alert-monitor・agent・human・plan）。"
            "メッセージ先頭のfrontmatterに source がある場合は本オプションより優先する。"
        ),
    )
    _add_target_repo_arg(
        add,
        help_extra="frontmatterにtarget_repoが明示されていない場合のfallback値として扱う。",
    )
    add.set_defaults(subparser=add)


def _add_mq_read_parsers(sub: Any) -> None:
    """一覧・表示サブコマンドを登録する。"""
    list_ = sub.add_parser("list", help="エントリを1件1行（ファイル名・`target_repo`・状態ラベル・本文冒頭要約）で出力する")
    _add_target_repo_arg(list_)
    list_.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    list_.add_argument(
        "--status",
        choices=("all", "active", "processable", *_common.MQ_STATES),
        default="active",
        help=(
            "状態フォルダで表示範囲を限定する（既定: active）。"
            "`active`はフィードバックが`inbox`・`planning`・`processing`・`editing`・`hold`、TBDが`inbox`・`processing`、"
            "`processable`は`inbox`・`processing`を指す。"
            "回答状況での限定は`--answered`で別途行う。"
        ),
    )
    list_.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="TBDの回答状況で限定する（既定: all）。`yes`・`no`指定時はフィードバックを除外する。",
    )
    _add_source_arg(list_)
    output = list_.add_mutually_exclusive_group()
    output.add_argument(
        "--count",
        action="store_true",
        help="エントリ件数を整数のみで出力する（種別ヘッダを抑制する）。",
    )
    output.add_argument(
        "--json",
        action="store_true",
        help="端末幅に依存しない1件1行のJSON Lines形式で出力する。",
    )
    output.add_argument(
        "--no-json",
        action="store_true",
        help="JSON Linesの既定を無効にし、従来のテキスト形式で出力する。",
    )
    _add_mq_read_sync_args(list_)

    show = sub.add_parser("show", help="指定エントリまたは全件（--all）の本文を表示する")
    show.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="表示するファイル名（複数指定可。省略時は--allの指定が必要）。全状態フォルダを探索する。",
    ).completer = _queue_filename_completer  # type: ignore[attr-defined]
    show.add_argument(
        "--all",
        action="store_true",
        help="対象範囲の全件をtarget_repoごとにグループ化して表示する。",
    )
    _add_target_repo_arg(show)
    show.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    show.add_argument(
        "--status",
        choices=("all", "active", "processable", *_common.MQ_STATES),
        default="active",
        help=(
            "状態フォルダで表示範囲を限定する（既定: active、--all指定時のみ有効）。"
            "`active`はフィードバックが`inbox`・`planning`・`processing`・`editing`・`hold`、TBDが`inbox`・`processing`、"
            "`processable`は`inbox`・`processing`を指す。"
            "FILENAME指定時は本オプションを迂回し全状態フォルダを探索する。"
        ),
    )
    show.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="TBDの回答状況で限定する（既定: all、--all指定時のみ有効）。`yes`・`no`指定時はフィードバックを除外する。",
    )
    _add_source_arg(show)
    _add_mq_read_sync_args(show)
    show.set_defaults(subparser=show)


def _add_mq_transition_parsers(sub: Any) -> None:
    """状態遷移・削除サブコマンドを登録する。"""
    start_planning = sub.add_parser(
        "start-planning",
        help="通常型フィードバックをinboxからplanning/へ移動し計画作成中にする",
    )
    start_planning.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="計画作成を開始するinboxの通常型フィードバック名（1個以上）。",
    ).completer = _inbox_filename_completer  # type: ignore[attr-defined]
    _add_target_repo_arg(start_planning, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    start_processing = sub.add_parser(
        "start-processing",
        help="フィードバックを`inbox`から`processing/`へ移動し処理中状態に遷移させコミット・push",
    )
    start_processing.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="処理開始するinboxファイル名（1個以上）。",
    ).completer = _inbox_filename_completer  # type: ignore[attr-defined]
    _add_target_repo_arg(start_processing, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    hold = sub.add_parser("hold", help="inboxまたはprocessingの項目を保留する")
    hold.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="保留するファイル名。"
    ).completer = _processable_filename_completer
    _add_target_repo_arg(hold, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    unhold = sub.add_parser("unhold", help="holdの項目をinboxへ戻す")
    unhold.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="保留を解除するファイル名。"
    ).completer = _hold_filename_completer
    _add_target_repo_arg(unhold, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    return_to_inbox = sub.add_parser(
        "return-to-inbox",
        help="フィードバックを`processing/`から`inbox`へ戻し未処理状態に遷移させコミット・push",
    )
    return_to_inbox.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="差し戻すprocessingファイル名（1個以上）。",
    ).completer = _processing_filename_completer  # type: ignore[attr-defined]
    return_to_inbox.add_argument(
        "--cooldown-days",
        type=_cooldown_days,
        default=None,
        metavar="DAYS",
        help="外部条件待ちのフィードバックを指定日数（3以上）だけ再処理対象から除外する。",
    )
    return_to_inbox.add_argument(
        "--state",
        choices=("planning",),
        default=None,
        help="planningから差し戻す場合に指定する。省略時はprocessingから差し戻す。",
    )
    _add_target_repo_arg(return_to_inbox, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    adopt = sub.add_parser("adopt", help="採用としてinboxまたはprocessingからadopted/へ移動しコミット・push")
    adopt.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="採用するファイル名（1個以上。inbox・processingいずれも対象）。"
    ).completer = _processable_filename_completer  # type: ignore[attr-defined]
    adopt.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="採否結果のメモ（本文末尾の`## 処理結果`節へ追記する）。--note=VALUE形式で渡すことを推奨。",
    )
    adopt.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help=(
            "対象リポジトリで解決できるrevision。対応するローカル作業ツリーが判明した場合は、"
            "記録時に完全OIDへ解決する。対応付けできない場合は警告し、指定値を記録する。"
            "--commit=VALUE形式で渡すことを推奨。"
        ),
    )
    adopt.add_argument(
        "--skip-push",
        action="store_true",
        help="管理リポジトリへのpushを省略してcommitだけ行う（連続操作の中間で用い、最後の操作では指定しない）。",
    )
    _add_target_repo_arg(adopt, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    reject = sub.add_parser("reject", help="不採用としてinboxまたはprocessingからrejected/へ移動しコミット・push")
    reject.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="不採用とするファイル名（1個以上。inbox・processingいずれも対象）。"
    ).completer = _processable_filename_completer  # type: ignore[attr-defined]
    reject.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="不採用理由のメモ（本文末尾の`## 処理結果`節へ追記する）。--note=VALUE形式で渡すことを推奨。",
    )
    reject.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help=(
            "対象リポジトリで解決できるrevision。対応するローカル作業ツリーが判明した場合は、"
            "記録時に完全OIDへ解決する。対応付けできない場合は警告し、指定値を記録する。"
            "--commit=VALUE形式で渡すことを推奨。"
        ),
    )
    reject.add_argument(
        "--skip-push",
        action="store_true",
        help="管理リポジトリへのpushを省略してcommitだけ行う（連続操作の中間で用い、最後の操作では指定しない）。",
    )
    reject.add_argument(
        "--if-inbox",
        action="store_true",
        help="pull後も全対象がinboxにある場合だけ不採用とし、processingへ移った対象があれば全体を変更しない。",
    )
    _add_target_repo_arg(reject, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")

    rm = sub.add_parser(
        "rm",
        help="指定項目、または対象リポジトリの未処理・処理中項目を削除しコミット・push",
    )
    rm.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="削除するファイル名。--allと併用せず、個別削除では1個以上を指定する。",
    ).completer = _removable_filename_completer  # type: ignore[attr-defined]
    rm.add_argument(
        "--all",
        action="store_true",
        help="--target-repoと完全一致するinbox・planning・processingの全項目を一覧表示後に削除する。",
    )
    rm.add_argument(
        "--yes",
        action="store_true",
        help="--allによる一括削除の確認入力を省略する。一覧表示とplanning・processing保護は維持する。",
    )
    rm.add_argument(
        "--force",
        action="store_true",
        help="planning・processing状態のファイルも削除する（既定では保護し拒否する）。",
    )
    rm.add_argument(
        "--skip-pull",
        action="store_true",
        help=("削除対象の選定・確認をremote同期せずローカル状態で行う（削除直前は毎回同期する）。--all指定時のみ有効。"),
    )
    rm.add_argument("--note", metavar="TEXT", default=None)
    _add_target_repo_arg(
        rm,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は削除対象を限定する。",
    )
    rm.set_defaults(subparser=rm)


def _add_mq_edit_parsers(sub: Any) -> None:
    """本文編集・計画変換・依存更新サブコマンドを登録する。"""
    edit = sub.add_parser("edit", help="対象エントリをMESSAGE又は$EDITORで編集しコミット・push")
    edit.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="?",
        default=None,
        help=(
            "編集対象のファイル名（inbox・processingいずれも対象）。"
            "MESSAGEとともに指定すると非対話で編集する。"
            "省略時はinbox配下で最終追加のファイル（ファイル名順で最大）を$EDITORで編集する。"
        ),
    ).completer = _processable_filename_completer  # type: ignore[attr-defined]
    edit.add_argument(
        "message",
        metavar="MESSAGE",
        nargs="?",
        default=None,
        help=(
            "置換する論理本文。省略時は$EDITORで保存ファイル全体を編集する。"
            "先頭frontmatterで明示したメタデータだけを更新し、未指定メタデータを保持する。"
        ),
    )
    edit.add_argument(
        "--append",
        action="store_true",
        help="FILENAMEの元のraw bytesを保ち、MESSAGEをUTF-8で末尾へ追記する。TBDは対象外。",
    )
    edit.add_argument(
        "--plan-file",
        metavar="ABS_PATH",
        default=None,
        help="planning項目を計画型feedbackへ編集しinboxへ移す実在する計画ファイルの絶対パス。",
    )
    edit.add_argument(
        "--depends-on",
        metavar="FILENAME",
        action="append",
        default=None,
        help="計画型feedbackへ統合する外部依存先。複数回指定できる。",
    )
    _add_target_repo_arg(edit, help_extra="指定時は対象ファイル名のfrontmatterと一致するか検証する。")
    edit.set_defaults(subparser=edit)

    convert_to_plan = sub.add_parser(
        "convert-to-plan",
        help="既存フィードバックを計画実装型へ変換し、planning項目は1件へ統合してコミット・pushする",
    )
    convert_to_plan.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="+",
        help="変換する同一状態のフィードバックファイル名（1個以上）。planningでは--messageを指定する。",
    ).completer = _convert_to_plan_filename_completer  # type: ignore[attr-defined]
    convert_to_plan.add_argument(
        "--message",
        metavar="MESSAGE",
        default=None,
        help="planning項目を統合する計画型フィードバック本文。inbox・processingでは指定しない。",
    )
    convert_to_plan.add_argument(
        "--plan-file",
        metavar="PLAN_FILE",
        required=True,
        help=(
            "新規計画は$(atk config get private_notes)/plans/から始まるportable値を指定する。"
            "既存の絶対パスも読み取り互換として受理する。"
        ),
    )
    convert_to_plan.add_argument(
        "--depends-on",
        metavar="FILENAME",
        action="append",
        default=None,
        help="処理完了を待つキュー項目。複数回指定でき、重複は初出順で除去する。",
    )
    convert_to_plan.add_argument(
        "--skip-push",
        action="store_true",
        help="管理リポジトリへのpushを省略してcommitだけ行う。",
    )
    _add_target_repo_arg(convert_to_plan, help_extra="省略時は現在の作業リポジトリと照合する。")

    set_dependencies = sub.add_parser(
        "set-dependencies",
        help="既存フィードバックの明示依存だけを更新してコミット・push",
    )
    set_dependencies.add_argument(
        "filename",
        metavar="FILENAME",
        help="更新する`inbox`または`processing`のフィードバックファイル名。",
    ).completer = _processable_filename_completer  # type: ignore[attr-defined]
    set_dependencies.add_argument(
        "--depends-on",
        metavar="FILENAME",
        action="append",
        default=None,
        help="処理完了を待つキュー項目。複数回指定でき、省略時は依存を全て解除する。",
    )
    _add_target_repo_arg(set_dependencies, help_extra="省略時は現在の作業リポジトリと照合する。")


def _add_mq_search_and_answer_parsers(sub: Any) -> None:
    """検索・回答・外部差分コミットサブコマンドを登録する。"""
    grep = sub.add_parser("grep", help="本文全体を正規表現で検索し該当行を列挙する（該当0件はexit 1）")
    grep.add_argument("pattern", metavar="PATTERN", help="Pythonの正規表現（reモジュール）として解釈する検索パターン。")
    grep.add_argument("-i", "--ignore-case", action="store_true", help="大文字小文字を無視して検索する。")
    grep.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    grep.add_argument(
        "--status",
        choices=("all", "active", "processable", *_common.MQ_STATES),
        default="active",
        help="状態フォルダで検索範囲を限定する（既定: active）。`list`と同じ選択肢・既定値。",
    )
    grep.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="TBDの回答状況で限定する（既定: all）。`yes`・`no`指定時はフィードバックを除外する。",
    )
    _add_target_repo_arg(grep)
    _add_mq_read_sync_args(grep)
    grep.set_defaults(subparser=grep)

    answer = sub.add_parser(
        "answer",
        help="TBDへ回答する（引数指定時は非対話、省略時は未回答TBDを1件ずつ画面表示し$EDITORで回答）",
    )
    answer.add_argument(
        "filename", nargs="?", help="回答対象のTBDファイル名（省略時は対話モード）"
    ).completer = _tbd_filename_completer  # type: ignore[attr-defined]
    answer.add_argument("answer_body", nargs="?", help="回答本文（省略時は対話モード）")
    _add_target_repo_arg(answer)

    sub.add_parser(
        "commit",
        help="外部編集後にinbox・processing配下の未コミット変更をコミット・push（差分がなくても滞留commitをpush）",
    )


def _add_mq_process_loop_parser(sub: Any) -> None:
    """常駐処理サブコマンドを登録する。"""
    loop = sub.add_parser(
        "process-loop",
        help=(
            "対象リポジトリのフィードバック消化をオーケストレーターの常駐起動で反復実行する。"
            "オーケストレーター・model・effortはatk configのorchestrate_model設定"
            "（既定claude:opus[1m]/medium、書式<claude|codex>:<model>[/<effort>]）で決まる。"
        ),
    )
    loop.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）。既定は現在の作業リポジトリ。",
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
        help="セッション完了後・待機中いずれの経路でもupdate-dotfiles実行と自身再起動を抑止する。",
    )
    loop.add_argument(
        "--internal-mise-refreshed",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    loop.add_argument(
        "--no-alerts",
        action="store_true",
        help="CI失敗・Dependabotアラートの自動検出を無効化する（既定は有効）。",
    )
    loop.add_argument(
        "--alert-interval",
        type=float,
        default=1800.0,
        metavar="SECONDS",
        help="アラート確認の最短間隔秒数（既定1800）。",
    )
    loop.add_argument(
        "--alert-forge",
        choices=("auto", "github", "gitlab"),
        default="auto",
        help="アラート検出対象のホスティング種別（既定auto。repo_idのhostから自動判定）。",
    )
    loop.add_argument(
        "--resume",
        nargs="?",
        const="",
        default=None,
        metavar="SESSION_ID",
        help=(
            "初回にorchestrate_model設定で決まったオーケストレーターの過去セッションを再開する。"
            "SESSION_ID省略時はセッション選択画面を開き、指定時は該当セッションを直接再開する。"
            "2回目以降は新規セッションとして起動する。"
        ),
    )


def _build_mq_parser(mq: argparse.ArgumentParser) -> None:
    """`mq`サブパーサ配下にメッセージキュー操作を登録する。"""
    sub = mq.add_subparsers(dest="mq_subcommand", required=True)
    for register in (
        _add_mq_add_parser,
        _add_mq_read_parsers,
        _add_mq_transition_parsers,
        _add_mq_edit_parsers,
        _add_mq_search_and_answer_parsers,
        _add_mq_process_loop_parser,
    ):
        register(sub)


def _build_parser() -> argparse.ArgumentParser:
    """`atk`トップレベルargparseパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="atk",
        description="agent-toolkitプラグイン提供CLI。",
    )
    top = parser.add_subparsers(dest="command", required=True)
    mq = top.add_parser("mq", help="メッセージキュー操作（フィードバック・TBD）")
    _build_mq_parser(mq)
    plans = top.add_parser("plans", help="計画ファイルのcommit・移行")
    _plans.build_parser(plans)
    serve = top.add_parser("serve", help="フィードバック管理Web UIを起動する")
    serve.add_argument(
        "--host",
        default=None,
        help="待受ホスト（環境変数 AGENT_TOOLKIT_SERVE_HOST、設定ファイルからも参照）",
    )
    serve.add_argument(
        "--port",
        default=None,
        type=_port_type,
        help="待受ポート（環境変数 AGENT_TOOLKIT_SERVE_PORT、設定ファイルからも参照）",
    )
    config = top.add_parser("config", help="XDG関連パス・工程別モデル設定を確認・変更する")
    _config_cmd.build_parser(config)
    wait_schedule = top.add_parser("wait-schedule", help="委譲待機に使うcron式を公開情報から判定する")
    wait_schedule.add_argument(
        "--request-bucket",
        choices=("main", "subagent"),
        required=True,
        help="判定対象のrequest bucket（mainまたはsubagent）。",
    )
    managed_temp = top.add_parser("managed-temp", help="管理対象一時領域を作成・列挙・後始末する")
    _managed_temp.build_parser(managed_temp, command_dest="managed_temp_subcommand")
    worktree_stash = top.add_parser("worktree-stash", help="worktree固有refへ変更を退避する")
    _worktree_stash.build_parser(worktree_stash, command_dest="worktree_stash_subcommand")
    watch = top.add_parser("watch", help="委譲先の成果物側の状況を1行で出力する")
    _watch.build_parser(watch)
    _review_table.build_parser(top)
    return parser


def _validate_rm_args(args: argparse.Namespace) -> None:
    """`mq rm`の個別指定と一括指定が排他的であることを検証する。"""
    if args.command != "mq" or args.mq_subcommand != "rm":
        return
    if args.all:
        if args.filenames:
            args.subparser.error("FILENAMEと--allは同時に指定できません。")
        if args.target_repo is None:
            args.subparser.error("--allには--target-repoが必要です。")
        return
    if not args.filenames:
        args.subparser.error("削除するFILENAME、または--allを指定してください。")
    if args.yes:
        args.subparser.error("--yesは--allとともに指定してください。")
    if args.skip_pull:
        args.subparser.error("--skip-pullは--allとともに指定してください。")


def _validate_add_args(args: argparse.Namespace) -> None:
    """`mq add`の`--batch`併用制約を検証し、種別の既定値を確定する。

    `--type`の既定値を`None`とすることで、`--batch`との併用判定で明示指定
    （`--type=feedback`を含む）を区別する。検証後に通常add経路の既定値`feedback`へ正規化する。
    """
    if args.batch:
        conflicting = [
            name
            for name, value in (
                ("--type", args.type),
                ("--scope", args.scope),
                ("--question-type", args.question_type),
                ("--choices", args.choices),
                ("--plan-file", args.plan_file),
                ("--depends-on", args.depends_on),
                ("--target-repo", args.target_repo),
                ("--source", args.source),
                ("REPO_PATH", args.repo_path_override),
            )
            if value is not None
        ]
        if conflicting:
            args.subparser.error(f"{'・'.join(conflicting)}は--batchと併用できません。")
    if args.type is None:
        args.type = "feedback"


def main(
    argv: list[str] | None = None,
    *,
    home: pathlib.Path | None = None,
    now: datetime.datetime | None = None,
) -> None:
    """エントリポイント。"""
    # Windowsのcp932環境で日本語出力が文字化けする事象を根本回避するためUTF-8を強制する。
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")
    parser = _build_parser()
    # bash補完（argcomplete）は配布物内で直接遅延importして呼び出す。
    # `pytools._internal.cli`依存を避け、agent-toolkitプラグインの独立性を保つため。
    import argcomplete  # noqa: PLC0415  # pylint: disable=import-outside-toplevel  # 補完起動時のみ必要なので遅延importする

    argcomplete.autocomplete(parser)
    raw_argv = argv if argv is not None else sys.argv[1:]
    _common.warn_space_separated_option(raw_argv)
    raw_argv, repo_path_override = _extract_legacy_repo_path(raw_argv)
    args = parser.parse_args(raw_argv)
    if now is None:
        now = datetime.datetime.now()
    automatically_cleaned: list[pathlib.Path] = []
    try:
        automatically_cleaned = _managed_temp.sweep_expired_managed_temp(now=now)
    except Exception as error:  # noqa: BLE001  # 自動削除の失敗で本来のサブコマンドを失敗させない
        print(f"warning: 管理対象一時領域の自動削除に失敗しました: {error}", file=sys.stderr)
    _validate_rm_args(args)
    args.repo_path_override = repo_path_override
    if args.command == "mq" and args.mq_subcommand == "add":
        _validate_add_args(args)
    if args.command == "mq" and args.mq_subcommand == "add" and args.type != "tbd":
        tbd_only = [
            name
            for name, value in (
                ("--scope", args.scope),
                ("--question-type", args.question_type),
                ("--choices", args.choices),
            )
            if value is not None
        ]
        if tbd_only:
            args.subparser.error(f"{'・'.join(tbd_only)}は--type=tbdでのみ指定できます。")
    if args.command == "mq" and args.mq_subcommand == "add" and args.type == "tbd" and args.question_type is None:
        args.question_type = "free-form"
    if (
        args.command == "mq"
        and args.mq_subcommand == "add"
        and args.type == "tbd"
        and args.question_type == "choice"
        and not args.choices
    ):
        args.subparser.error("--question-type=choice のときは --choices を指定してください。")
    if args.command == "wait-schedule":
        print(_wait_schedule.get_schedule(args.request_bucket))
        sys.exit(0)
    if home is None:
        home = pathlib.Path.home()
    if args.command == "serve":
        import _atk_serve as _serve  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        _serve.run(host=args.host, port=args.port, home=home)
        sys.exit(0)
    if args.command == "managed-temp":
        if (
            args.managed_temp_subcommand == "cleanup"
            and args.path.is_absolute()
            and pathlib.Path(os.path.abspath(args.path)) in automatically_cleaned
        ):
            sys.exit(0)
        sys.exit(_managed_temp.dispatch(args, command_dest="managed_temp_subcommand"))
    if args.command == "worktree-stash":
        sys.exit(_worktree_stash.dispatch(args, command_dest="worktree_stash_subcommand"))
    if args.command == "watch":
        sys.exit(_watch.dispatch(args, now=now))
    if args.command == "config":
        _config_cmd.dispatch(args, home)
    if args.command == "plans":
        private_notes = _common._ensure_environment(home)
        try:
            sys.exit(_plans.dispatch(args, private_notes, home))
        except (_common.WebInputError, _atk_git_sync.RebaseInProgressError) as error:
            print(f"操作を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
        except subprocess.CalledProcessError as error:
            print(f"Git操作に失敗しました: {error}", file=sys.stderr)
            sys.exit(1)
    if args.command == "review-table":
        try:
            sys.exit(_review_table.dispatch(args))
        except ValueError as error:
            print(f"操作を拒否しました: {error}", file=sys.stderr)
            sys.exit(1)
    if args.command != "mq":
        parser.error(f"未知のトップレベルコマンド: {args.command}")
    sub = args.mq_subcommand
    private_notes = _common._ensure_environment(home)
    dispatch = {
        "add": lambda: (
            _batch._cmd_add_batch(args, private_notes, now, home)
            if args.batch
            else _add._cmd_add(args, private_notes, now, home)
        ),
        "list": lambda: _list._cmd_list(args, private_notes),
        "show": lambda: _show._cmd_show(args, private_notes),
        "start-planning": lambda: _mutations._cmd_start_planning(args, private_notes, now),
        "start-processing": lambda: _mutations._cmd_start_processing(args, private_notes, now),
        "hold": lambda: _mutations._cmd_hold(args, private_notes, now),
        "unhold": lambda: _mutations._cmd_unhold(args, private_notes, now),
        "return-to-inbox": lambda: _mutations._cmd_return_to_inbox(args, private_notes, now),
        "adopt": lambda: _mutations._cmd_adopt(args, private_notes, now),
        "reject": lambda: _mutations._cmd_reject(args, private_notes, now),
        "rm": lambda: _mutations._cmd_rm(args, private_notes),
        "edit": lambda: _mutations._cmd_edit(args, private_notes),
        "convert-to-plan": lambda: _mutations._cmd_convert_to_plan(args, private_notes),
        "set-dependencies": lambda: _mutations._cmd_set_dependencies(args, private_notes),
        "grep": lambda: _grep._cmd_grep(args, private_notes),
        "answer": lambda: _tbd._cmd_answer(args, private_notes),
        "commit": lambda: _mutations._cmd_commit(private_notes),
        "process-loop": lambda: _process_loop._cmd_process_loop(args, private_notes),
    }
    try:
        exit_code = dispatch[sub]() or 0
    except _common.WebInputError as error:
        print(f"操作を拒否しました: {error}", file=sys.stderr)
        sys.exit(1)
    except subprocess.CalledProcessError as error:
        print(f"Git操作に失敗しました: {error}", file=sys.stderr)
        sys.exit(1)
    suppress_notify = (sub == "list" and _list._covers_unanswered_tbds(args)) or (
        sub == "show" and _show._covers_unanswered_tbds(args)
    )
    if not suppress_notify:
        _common.notify_unanswered_tbds_if_any(private_notes, getattr(args, "target_repo", None))
    sys.exit(exit_code)


def _run_cli() -> None:
    """実プロセスの終了コードとstdoutの寿命を所有する。"""
    try:
        try:
            main()
        except SystemExit as error:
            exit_code = error.code
        else:
            exit_code = 0
        sys.stdout.flush()
    except BrokenPipeError:
        # Python公式の推奨どおり、終了時flushで同じ例外を再送出しないようstdoutを破棄する。
        with open(os.devnull, "w", encoding="utf-8") as devnull:
            os.dup2(devnull.fileno(), sys.stdout.fileno())
        exit_code = 1
    raise SystemExit(exit_code)


if __name__ == "__main__":
    _run_cli()
