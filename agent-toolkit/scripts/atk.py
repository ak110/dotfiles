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
# ]
# ///
"""agent-toolkitプラグイン提供CLI`atk`のPEP 723 entrypoint。

サブコマンド構成は`atk mq <sub>`・`atk serve`・`atk config <sub>`形式とする。
フィードバックとTBDを平坦なメッセージキューとして扱い、種別はfrontmatterの`type`で識別する。

- mq add/list/show: エントリの投入・一覧・本文表示
- mq grep: 本文全体を正規表現で検索し`<ファイル名>:<行番号>:<該当行>`形式で列挙する
- mq start-processing/return-to-inbox/adopt/reject/rm/commit: エントリの状態遷移・削除・コミット
- mq edit: MESSAGEによる非対話編集又は$EDITORによる保存ファイル全体の編集
- mq answer: TBDへの回答
- mq schedule: 分類メタデータの適用と処理順算出
- mq process-loop: `claude /process-feedbacks`と`/agent-toolkit:exit-session`直接起動で常駐実行する。
  待機中は既定でCI失敗・Dependabotアラートを自動検出しfeedback投入する（`--no-alerts`で無効化）
- config show/get/set: XDG関連パス・codexモデル判定設定の確認・変更

ハンドラ実装は`_atk_mq_add`・`_atk_mq_list`・`_atk_mq_show`・`_atk_mq_mutations`・
`_atk_mq_schedule_cli`・`_atk_mq_process_loop`・`_atk_mq_tbd`の各補助モジュールに分割し、
本モジュールはargparse定義・dispatch・エントリポイントを保持する。
"""

import argparse
import datetime
import pathlib
import sys

# 兄弟モジュール（_atk_mq_*.py）を絶対importで解決するためsys.pathへ同一ディレクトリを挿入する。
# sys.path挿入前の相対解決を避けるため、モジュール内importはこの下に配置する。
# pylint: disable=wrong-import-position,protected-access
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import _atk_config as _config_cmd  # noqa: E402
import _atk_mq_add as _add  # noqa: E402
import _atk_mq_common as _common  # noqa: E402
import _atk_mq_grep as _grep  # noqa: E402
import _atk_mq_list as _list  # noqa: E402
import _atk_mq_mutations as _mutations  # noqa: E402
import _atk_mq_process_loop as _process_loop  # noqa: E402
import _atk_mq_schedule_cli as _schedule_cli  # noqa: E402
import _atk_mq_show as _show  # noqa: E402
import _atk_mq_tbd as _tbd  # noqa: E402
import _atk_serve as _serve  # noqa: E402

_queue_filename_completer = _common.make_filename_completer(_common.MQ_STATES)
_active_filename_completer = _common.make_filename_completer(_common.MQ_ACTIVE_STATES)
_inbox_filename_completer = _common.make_filename_completer((_common.MQ_STATE_INBOX,))
_processing_filename_completer = _common.make_filename_completer((_common.MQ_STATE_PROCESSING,))
_tbd_filename_completer = _common.make_filename_completer(_common.MQ_ACTIVE_STATES, _common.MQ_TYPE_TBD)


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
    value_options = {"--type", "--source", "--scope", "--question-type", "--choices", "--target-repo"}
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


def _build_mq_parser(mq: argparse.ArgumentParser) -> None:
    """`mq`サブパーサ配下にメッセージキュー操作を登録する。"""
    sub = mq.add_subparsers(dest="mq_subcommand", required=True)

    add = sub.add_parser("add", help="エントリをinboxへ投入する")
    add.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="*",
        help=(
            "投入する本文（省略時は$EDITORで編集する）。--type=feedback（既定）・tbdで種別を切り替える。"
            "対象リポジトリは常にカレントディレクトリから解決する。"
            "メッセージ先頭がYAML frontmatter形式の場合はtarget_repo・sourceをCLIオプションより優先する。"
        ),
    )
    add.add_argument("--type", choices=("feedback", "tbd"), default="feedback")
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
        ),
    )
    add.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help=(
            "投入元の識別子（任意。frontmatterに source: <NAME> として記録する。既知値: session-review・plan）。"
            "メッセージ先頭のfrontmatterに source がある場合は本オプションより優先する。"
        ),
    )
    _add_target_repo_arg(
        add,
        help_extra="frontmatterにtarget_repoが明示されていない場合のfallback値として扱う。",
    )
    add.set_defaults(subparser=add)

    list_ = sub.add_parser("list", help="エントリを1件1行（filename・target_repo・状態ラベル・本文冒頭要約）で出力する")
    list_.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターする。",
    )
    list_.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    list_.add_argument(
        "--status",
        choices=("all", "active", "inbox", "processing", "adopted", "rejected"),
        default="active",
        help=(
            "状態フォルダで表示範囲を限定する（既定: active）。"
            "`active`はinbox・processingを指す（feedback・tbd共通）。"
            "回答状況での限定は`--answered`で別途行う。"
        ),
    )
    list_.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="TBDの回答状況で限定する（既定: all）。yes・no指定時はfeedbackを除外する。",
    )
    list_.add_argument(
        "--category",
        default=None,
        help="採用時に付与される再発防止分類ラベル（`## 処理結果`節の`カテゴリ:`行）で、"
        "指定時は同ラベルが付与されたfeedbackのみへ限定する。",
    )
    _add_source_arg(list_)
    list_.add_argument(
        "--count",
        action="store_true",
        help="エントリ件数を整数のみで出力する（種別ヘッダを抑制する）。",
    )
    list_.add_argument(
        "--skip-pull",
        action="store_true",
        help="git pull --ff-onlyをスキップする（ログイン時など軽量参照用）。",
    )

    schedule = sub.add_parser(
        "schedule",
        help="分類メタデータを検証し、依存・上限・競合からセッションの処理順を算出する",
    )
    schedule.add_argument(
        "--classifications",
        metavar="PATH",
        default=None,
        help="未分類項目へ適用する分類結果JSONファイル",
    )
    schedule.add_argument(
        "--record-deferral",
        metavar="REASON:FILENAME",
        nargs="+",
        default=None,
        help="初期選抜後に次回へ送る項目と理由",
    )
    _add_target_repo_arg(schedule, required=True)

    show = sub.add_parser("show", help="指定エントリまたは全件（--all）の本文を表示する")
    show.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="?",
        default=None,
        help="表示する単一のファイル名（省略時は--allの指定が必要）。全状態フォルダを探索する。",
    ).completer = _queue_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    show.add_argument(
        "--all",
        action="store_true",
        help="対象範囲の全件をtarget_repoごとにグループ化して表示する。",
    )
    show.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターする。",
    )
    show.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    show.add_argument(
        "--status",
        choices=("all", "active", "inbox", "processing", "adopted", "rejected"),
        default="active",
        help=(
            "状態フォルダで表示範囲を限定する（既定: active、--all指定時のみ有効）。"
            "`active`はinbox・processingを指す（feedback・tbd共通）。"
            "FILENAME単発指定時は本オプションを迂回し全状態フォルダを探索する。"
        ),
    )
    show.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="TBDの回答状況で限定する（既定: all、--all指定時のみ有効）。yes・no指定時はfeedbackを除外する。",
    )
    _add_source_arg(show)
    show.add_argument(
        "--skip-pull",
        action="store_true",
        help="git pull --ff-onlyをスキップする（ログイン時など軽量参照用）。",
    )
    show.set_defaults(subparser=show)

    start_processing = sub.add_parser(
        "start-processing",
        help="feedbackをinboxからprocessing/へ移動し処理中状態に遷移させコミット・push",
    )
    start_processing.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="処理開始するinboxファイル名（1個以上）。",
    ).completer = _inbox_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _add_target_repo_arg(start_processing, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    return_to_inbox = sub.add_parser(
        "return-to-inbox",
        help="feedbackをprocessing/からinboxへ戻し未処理状態に遷移させコミット・push",
    )
    return_to_inbox.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="差し戻すprocessingファイル名（1個以上）。",
    ).completer = _processing_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    _add_target_repo_arg(return_to_inbox, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    adopt = sub.add_parser("adopt", help="採用としてinboxまたはprocessingからadopted/へ移動しコミット・push")
    adopt.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="採用するファイル名（1個以上。inbox・processingいずれも対象）。"
    ).completer = _active_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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
        help="対応する対象リポジトリのcommit hash（本文末尾の`## 処理結果`節へ追記する）。--commit=VALUE形式で渡すことを推奨。",
    )
    adopt.add_argument(
        "--category",
        default=None,
        help="採用フィードバックの再発防止分類ラベル（任意）。累積カテゴリ集計の対象になる。",
    )
    _add_target_repo_arg(adopt, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    reject = sub.add_parser("reject", help="不採用としてinboxまたはprocessingからrejected/へ移動しコミット・push")
    reject.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="不採用とするファイル名（1個以上。inbox・processingいずれも対象）。"
    ).completer = _active_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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
        help="対応する対象リポジトリのcommit hash（本文末尾の`## 処理結果`節へ追記する）。--commit=VALUE形式で渡すことを推奨。",
    )
    _add_target_repo_arg(reject, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    rm = sub.add_parser(
        "rm",
        help="指定項目、または対象リポジトリの未処理・処理中項目を削除しコミット・push",
    )
    rm.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="削除するファイル名。--allと併用せず、個別削除では1個以上を指定する。",
    ).completer = _active_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    rm.add_argument(
        "--all",
        action="store_true",
        help="--target-repoと完全一致するinbox・processingの全項目を一覧表示後に削除する。",
    )
    rm.add_argument(
        "--yes",
        action="store_true",
        help="--allによる一括削除の確認入力を省略する。一覧表示とprocessing保護は維持する。",
    )
    rm.add_argument(
        "--force",
        action="store_true",
        help="processing状態のファイルも削除する（既定では処理中のファイルを保護し拒否する）。",
    )
    rm.add_argument("--note", metavar="TEXT", default=None)
    _add_target_repo_arg(
        rm,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は削除対象を限定する。",
    )
    rm.set_defaults(subparser=rm)

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
    ).completer = _active_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
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
    _add_target_repo_arg(edit, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")
    edit.set_defaults(subparser=edit)

    grep = sub.add_parser("grep", help="本文全体を正規表現で検索し該当行を列挙する（該当0件はexit 1）")
    grep.add_argument("pattern", metavar="PATTERN", help="Pythonの正規表現（reモジュール）として解釈する検索パターン。")
    grep.add_argument("-i", "--ignore-case", action="store_true", help="大文字小文字を無視して検索する。")
    grep.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    grep.add_argument(
        "--status",
        choices=("all", "active", "inbox", "processing", "adopted", "rejected"),
        default="active",
        help="状態フォルダで検索範囲を限定する（既定: active）。`list`と同じ選択肢・既定値。",
    )
    grep.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        default="all",
        help="TBDの回答状況で限定する（既定: all）。yes・no指定時はfeedbackを除外する。",
    )
    _add_target_repo_arg(grep)
    grep.add_argument("--skip-pull", action="store_true", help="git pull --ff-onlyをスキップする。")
    grep.set_defaults(subparser=grep)

    answer = sub.add_parser(
        "answer",
        help="TBDへ回答する（引数指定時は非対話、省略時は未回答TBDを1件ずつ画面表示し$EDITORで回答）",
    )
    answer.add_argument(
        "filename", nargs="?", help="回答対象のTBDファイル名（省略時は対話モード）"
    ).completer = _tbd_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    answer.add_argument("answer_body", nargs="?", help="回答本文（省略時は対話モード）")
    _add_target_repo_arg(answer)

    sub.add_parser(
        "commit",
        help="外部編集後にinbox・processing配下の未コミット変更をコミット・push（差分なしなら無動作）",
    )

    loop = sub.add_parser(
        "process-loop",
        help="対象リポジトリのfeedback消化をclaudeの常駐起動で反復実行する",
    )
    loop.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）。既定は現在の作業リポジトリ。",
    )
    loop.add_argument(
        "--no-update",
        action="store_true",
        help="セッション完了後・待機中いずれの経路でもupdate-dotfiles実行と自身再起動を抑止する。",
    )
    loop.add_argument(
        "--model",
        default="opus",
        help="claude起動時の--modelオプションの既定はopusとする。",
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
            "初回に過去のClaudeセッションを再開する。"
            "SESSION_ID省略時はセッション選択画面を開き、指定時は該当セッションを直接再開する。"
            "2回目以降は新規セッションとして起動する。"
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    """`atk`トップレベルargparseパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="atk",
        description="agent-toolkitプラグイン提供CLI。",
    )
    top = parser.add_subparsers(dest="command", required=True)
    mq = top.add_parser("mq", help="メッセージキュー操作（フィードバック・TBD）")
    _build_mq_parser(mq)
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
    config = top.add_parser("config", help="XDG関連パス・codexモデル判定設定を確認・変更する")
    _config_cmd.build_parser(config)
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
    _validate_rm_args(args)
    args.repo_path_override = repo_path_override
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
    if home is None:
        home = pathlib.Path.home()
    if now is None:
        now = datetime.datetime.now()
    if args.command == "serve":
        _serve.run(host=args.host, port=args.port, home=home)
        sys.exit(0)
    if args.command == "config":
        _config_cmd.dispatch(args, home)
    if args.command != "mq":
        parser.error(f"未知のトップレベルコマンド: {args.command}")
    sub = args.mq_subcommand
    private_notes = _common._ensure_environment(home)
    dispatch = {
        "add": lambda: _add._cmd_add(args, private_notes, now, home),
        "list": lambda: _list._cmd_list(args, private_notes),
        "show": lambda: _show._cmd_show(args, private_notes),
        "start-processing": lambda: _mutations._cmd_start_processing(args, private_notes),
        "return-to-inbox": lambda: _mutations._cmd_return_to_inbox(args, private_notes),
        "adopt": lambda: _mutations._cmd_adopt(args, private_notes, now),
        "reject": lambda: _mutations._cmd_reject(args, private_notes, now),
        "rm": lambda: _mutations._cmd_rm(args, private_notes),
        "edit": lambda: _mutations._cmd_edit(args, private_notes),
        "grep": lambda: _grep._cmd_grep(args, private_notes),
        "answer": lambda: _tbd._cmd_answer(args, private_notes),
        "commit": lambda: _mutations._cmd_commit(private_notes),
        "schedule": lambda: _schedule_cli.cmd_schedule(args, private_notes),
        "process-loop": lambda: _process_loop._cmd_process_loop(args, private_notes),
    }
    exit_code = dispatch[sub]() or 0
    suppress_notify = (sub == "list" and _list._covers_unanswered_tbds(args)) or (
        sub == "show" and _show._covers_unanswered_tbds(args)
    )
    if not suppress_notify:
        _common.notify_unanswered_tbds_if_any(private_notes, getattr(args, "target_repo", None))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
