"""WIの一覧・表示・検索・回答の引数登録。"""

import datetime
import pathlib
from typing import Any

from agent_toolkit._atk.wi.cli_parser_support import (
    _add_important_filter_arg,
    _add_mq_read_sync_args,
    _queue_filename_completer,
    _uwi_filename_completer,
)
lazy from agent_toolkit._atk import help_text as _atk_help
lazy from agent_toolkit._atk.wi import arguments as _arguments
lazy from agent_toolkit._atk.wi import constants as _wi_constants


def _add_mq_read_parsers(sub: Any) -> None:
    """一覧・表示サブコマンドを登録する。"""
    # 旧名`--json`・`--no-json`が改名後の`--jsonl`・`--no-jsonl`の省略形として受理され続けないよう、
    # 省略したオプション名では一致と判定しない。
    list_ = _atk_help.add_command(sub, "list", allow_abbrev=False, **_atk_help.HELP["atk wi list"])
    _arguments.add_target_repo_arg(list_, multiple=True, allow_all=True)
    list_.add_argument(
        "--configured-repos",
        action="store_true",
        help="repositoriesでprocess_loopが有効な登録集合を一括取得する。--target-repoとは併用できない。",
    )
    list_.add_argument(
        "--snapshot-output",
        type=pathlib.Path,
        metavar="JSON",
        help="同じ取得のフィルター適用前の全状態・全種別の対象repoとWI名だけをJSON配列へ保存する。",
    )
    list_.add_argument(
        "--new-since",
        type=pathlib.Path,
        metavar="JSON",
        help="開始時集合のJSONに無い項目だけへ通常の一覧条件を適用する。基準JSONは変更しない。",
    )
    list_.add_argument(
        "--type",
        choices=("all", *_wi_constants.WI_TYPES),
        action="append",
        default=None,
        help="出力対象種別。省略時は全種別を表示する。",
    )
    list_.add_argument(
        "--state",
        "--status",
        dest="status",
        choices=("all", "active", "processable", *_wi_constants.WI_STATES),
        action="append",
        default=None,
        help=(
            "状態フォルダで表示範囲を限定する。省略時はactiveを表示する。"
            "`active`は`inbox`・`processing`・`hold`、`processable`は`inbox`・`processing`を指す。"
            "回答状況での限定は`--answered`で別途行う。"
        ),
    )
    list_.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        action="append",
        default=None,
        help="UWIの回答状況で限定する。省略時は全ての回答状況を含める。`yes`・`no`指定時はAWIを除外する。",
    )
    _arguments.add_source_arg(list_, multiple=True)
    _add_important_filter_arg(list_)
    list_.add_argument(
        "--effect-check",
        choices=("pending", "confirmed", "insufficient", "not_required"),
        help="効果確認の全体判定で限定する。採用済みは--state=adoptedと併用する。",
    )
    output = list_.add_mutually_exclusive_group()
    output.add_argument(
        "--count",
        action="store_true",
        help="エントリ件数を整数のみで出力する（種別ヘッダを抑制する）。",
    )
    output.add_argument(
        "--summary-only",
        action="store_true",
        help="ファイル名と要約だけを持つ1件1行のJSON Lines形式で出力する。",
    )
    output.add_argument(
        "--jsonl",
        action="store_true",
        help="端末幅によらず同じ形の1件1行のJSON Lines形式で出力する。",
    )
    output.add_argument(
        "--no-jsonl",
        action="store_true",
        help="JSON Linesの代わりに従来のテキスト形式で出力する。",
    )
    list_.add_argument(
        "--with-staleness",
        action="store_true",
        help="メイン向けにtarget_commit以後の履歴の鮮度情報をJSONへ加える。",
    )
    _add_mq_read_sync_args(list_)
    list_.set_defaults(subparser=list_)

    show = _atk_help.add_command(sub, "show", **_atk_help.HELP["atk wi show"])
    show.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="表示するファイル名（複数指定可。省略時は--allの指定が必要）。年月配置を探索し、frontmatterの状態を読む。",
    ).completer = _queue_filename_completer  # type: ignore[attr-defined]
    show.add_argument(
        "--all",
        action="store_true",
        help="対象範囲の全件をtarget_repoごとにグループ化して表示する。",
    )
    _arguments.add_target_repo_arg(
        show,
        multiple=True,
        allow_all=True,
        help_extra="FILENAME指定時は明示的照会として扱い、省略時の限定を適用しない。",
    )
    show_output = show.add_mutually_exclusive_group()
    show_output.add_argument(
        "--summary-only",
        action="store_true",
        help=(
            "target_repoとファイル名・状態の見出しに続けて、AWIはH1表題、UWIは質問本文の先頭行だけを"
            "省略せずに1行で表示し、frontmatterと他の本文を省く。"
        ),
    )
    show_output.add_argument(
        "--section",
        metavar="H2",
        action="append",
        default=None,
        help="H2見出し名で指定した節を表示する。複数回指定可。同名の節を全て保持し、フェンス内の見出しは境界としない。",
    )
    show.add_argument(
        "--type",
        choices=("all", *_wi_constants.WI_TYPES),
        action="append",
        default=None,
        help="出力対象種別。省略時は全種別を表示する。",
    )
    show.add_argument(
        "--state",
        "--status",
        dest="status",
        choices=("all", "active", "processable", *_wi_constants.WI_STATES),
        action="append",
        default=None,
        help=(
            "frontmatterの状態で表示範囲を限定する。省略時はactiveを対象とする。--all指定時のみ有効。"
            "`active`は`inbox`・`processing`・`hold`、`processable`は`inbox`・`processing`を指す。"
            "FILENAME指定時は本オプションを適用せず年月配置を探索する。"
        ),
    )
    show.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        action="append",
        default=None,
        help="UWIの回答状況で限定する。省略時は全ての回答状況を対象とする。--all指定時のみ有効。`yes`・`no`指定時はAWIを除外する。",
    )
    _arguments.add_source_arg(show, multiple=True)
    _add_important_filter_arg(show)
    _add_mq_read_sync_args(show)
    show.set_defaults(subparser=show)


def _add_mq_search_and_answer_parsers(sub: Any) -> None:
    """検索・回答・外部差分コミットサブコマンドを登録する。"""
    grep = _atk_help.add_command(sub, "grep", **_atk_help.HELP["atk wi grep"])
    grep.add_argument(
        "pattern",
        metavar="PATTERN",
        nargs="?",
        default=None,
        help="検索パターン。Pythonの正規表現として、`-F`指定時は固定文字列として解釈する。`-e`とは併用できない。",
    )
    grep.add_argument(
        "-e",
        "--regexp",
        metavar="PATTERN",
        action="append",
        default=None,
        help=(
            "検索パターンを反復指定し、いずれかに一致する行を1回だけ計上する。例: -e alpha -e beta。位置引数とは併用できない。"
        ),
    )
    grep.add_argument("-i", "--ignore-case", action="store_true", help="大文字小文字を無視して検索する。")
    mode = grep.add_mutually_exclusive_group()
    mode.add_argument("-E", action="store_true", dest="regex", help="省略時と同じPythonの正規表現で検索する。")
    mode.add_argument(
        "-F",
        "--fixed-strings",
        action="store_true",
        help="PATTERNの全文字を文字そのものとして扱い、その文字列を含む行を一致とする。`-i`と併用できる。",
    )
    output = grep.add_mutually_exclusive_group()
    output.add_argument(
        "-c",
        "--count",
        action="store_true",
        help="一致した項目ごとに`<ファイル名>:<一致行数>`を出力する（値は項目ごとの一致行数）。",
    )
    output.add_argument(
        "-l",
        "--files-with-matches",
        action="store_true",
        help="一致した項目のファイル名だけを出力する。",
    )
    output.add_argument(
        "--summary-only",
        action="store_true",
        help="一致した項目ごとに`filename`・`summary`・`match_count`を持つ1件1行のJSON Linesを出力する。",
    )
    grep.add_argument(
        "--type", choices=("all", *_wi_constants.WI_TYPES), default="all", help="出力対象種別。省略時は全種別を表示する。"
    )
    grep.add_argument(
        "--state",
        "--status",
        dest="status",
        choices=("all", "active", "processable", *_wi_constants.WI_STATES),
        default="active",
        help="状態フォルダで検索範囲を限定する。省略時はactiveを対象とする。`list`と同じ選択肢と省略時の値を使う。",
    )
    grep.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="UWIの回答状況で限定する。省略時は全ての回答状況を含める。`yes`・`no`指定時はAWIを除外する。",
    )
    _arguments.add_source_arg(grep, multiple=True)
    grep.add_argument(
        "--since",
        type=datetime.date.fromisoformat,
        metavar="YYYY-MM-DD",
        help="ファイル名の投入日が指定日以後のWIだけを検索する。投入日を持たない旧名は含めない。",
    )
    _add_important_filter_arg(grep)
    _arguments.add_target_repo_arg(grep, allow_all=True)
    _add_mq_read_sync_args(grep)
    grep.set_defaults(subparser=grep)

    answer = _atk_help.add_command(sub, "answer", **_atk_help.HELP["atk wi answer"])
    answer.add_argument(
        "filename", nargs="?", help="回答対象のUWIファイル名（省略時は対話モード）"
    ).completer = _uwi_filename_completer  # type: ignore[attr-defined]
    answer.add_argument("answer_body", nargs="?", help="回答本文（省略時は対話モード）")
    _arguments.add_target_repo_arg(answer)

    _atk_help.add_command(sub, "commit", **_atk_help.HELP["atk wi commit"])
    _atk_help.add_command(sub, "pull", **_atk_help.HELP["atk wi pull"])
