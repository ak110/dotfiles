"""~/private-notesのフィードバック項目を操作するCLIエントリポイント。

サブコマンド構成。
- add: inboxへフィードバックを投入する
- list: feedback/tbd inbox全件を1件1行（filename・target_repo・本文冒頭要約）で出力する
  （--typeで種別絞込、--statusでtbd側のみ回答状況を絞込、--countで件数のみ出力）
- show: feedback/tbd inboxの1件または全件（--all）の本文を表示する
  （--typeで種別絞込、--statusでtbd側のみ回答状況を絞込）
- adopt: 採用としてinboxからadopted/へ移動しコミット・push
- reject: 不採用としてinboxからrejected/へ移動しコミット・push
- rm: inboxから単純削除しコミット・push
- edit: $EDITORで対象ファイルを編集しコミット・push
- commit: 外部編集後のinbox配下未コミット変更をコミット・push
- enable: feedback-inboxフラグファイルを作成する
- disable: feedback-inboxフラグファイルを削除する
- status: feedback-inboxの有効状態を判定する（正常0・無効1）
- process-loop: 対象リポジトリのfeedback消化を`claude /process-feedbacks`＋`/agent-toolkit:exit-session`直接起動で常駐実行する
- tbd-add: 新規TBD項目を追加する
- tbd-list: TBD項目一覧を状態フィルターで出力する
- tbd-answer: 未回答のTBD項目へ回答を書き込む
- tbd-edit: `$EDITOR`でTBD項目を直接編集する
- tbd-adopt: 回答済みTBD項目をtbd/inboxからtbd/adopted/へ移動しコミット・push

ハンドラ実装は`_add`・`_list`・`_show`・`_mutations`・`_process_loop`・`_tbd`の各モジュールに分割し、
本モジュールはargparse定義・dispatch・エントリポイントと`enable`・`disable`・`status`の軽量ハンドラを保持する。
"""

import argparse
import datetime
import pathlib
import sys
import typing

from pytools._internal.cli import enable_completion
from pytools.dotfiles_fb._add import _cmd_add
from pytools.dotfiles_fb._common import _check_environment, _ensure_environment, _flag_path
from pytools.dotfiles_fb._list import _cmd_list
from pytools.dotfiles_fb._mutations import _cmd_adopt, _cmd_commit, _cmd_edit, _cmd_reject, _cmd_rm
from pytools.dotfiles_fb._process_loop import _cmd_process_loop
from pytools.dotfiles_fb._show import _cmd_show
from pytools.dotfiles_fb._tbd import (
    _cmd_tbd_add,
    _cmd_tbd_adopt,
    _cmd_tbd_answer,
    _cmd_tbd_edit,
    _cmd_tbd_list,
    _tbd_filename_completer,
)


def _build_parser() -> argparse.ArgumentParser:
    """サブコマンド付きargparseパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        description="~/private-notesのフィードバック項目を操作する。",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    add = sub.add_parser("add", help="フィードバックをinboxへ投入する")
    add.add_argument(
        "repo_path",
        metavar="REPO_PATH",
        help="フィードバック対象リポジトリのローカルパス（リモートURLを自動取得して格納）。",
    )
    add.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="*",
        help=(
            "投入するフィードバックメッセージ（省略時は$EDITORで編集する）。"
            "メッセージ先頭がYAML frontmatter形式の場合は`target_repo`・`source`をCLIオプションより優先する。"
        ),
    )
    add.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help=(
            "投入元の識別子（任意。frontmatterに source: <NAME> として記録する。既知値: session-review）。"
            "メッセージ先頭のfrontmatterに source がある場合は本オプションより優先する。"
        ),
    )

    list_ = sub.add_parser("list", help="feedback/tbd inbox全件を1件1行（filename・target_repo・本文冒頭要約）で出力する")
    list_.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターする。",
    )
    list_.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    list_.add_argument(
        "--status",
        choices=("all", "answered", "unanswered"),
        default="all",
        help="回答状況でtbd側のみ絞り込む（既定: all、feedback側には作用しない）。",
    )
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

    show = sub.add_parser("show", help="feedback/tbd inboxの1件または全件（--all）の本文を表示する")
    show.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="?",
        default=None,
        help="表示する単一のinboxファイル名（省略時は--allの指定が必要）。",
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    show.add_argument(
        "--all",
        action="store_true",
        help="inbox全件をtarget_repoごとにグループ化して表示する。",
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
        choices=("all", "answered", "unanswered"),
        default="all",
        help="回答状況でtbd側のみ絞り込む（既定: all、feedback側には作用しない）。",
    )
    show.add_argument(
        "--skip-pull",
        action="store_true",
        help="git pull --ff-onlyをスキップする（ログイン時など軽量参照用）。",
    )

    adopt = sub.add_parser("adopt", help="採用としてinboxからadopted/へ移動しコミット・push")
    adopt.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="採用するinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    adopt.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="採否結果のメモ（本文末尾の`## 処理結果`節へ追記する）。",
    )
    adopt.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help="対応する対象リポジトリのcommit hash（本文末尾の`## 処理結果`節へ追記する）。",
    )

    reject = sub.add_parser("reject", help="不採用としてinboxからrejected/へ移動しコミット・push")
    reject.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="不採用とするinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    reject.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="不採用理由のメモ（本文末尾の`## 処理結果`節へ追記する）。",
    )
    reject.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help="対応する対象リポジトリのcommit hash（本文末尾の`## 処理結果`節へ追記する）。",
    )

    rm = sub.add_parser("rm", help="inboxから単純削除しコミット・push")
    rm.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="削除するinboxファイル名（1個以上）。"
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    edit = sub.add_parser("edit", help="$EDITORで対象ファイルを編集しコミット・push")
    edit.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="?",
        default=None,
        help="編集対象のinboxファイル名。省略時はinbox配下で最終追加のファイル（ファイル名順で最大）を対象とする。",
    ).completer = _feedback_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    sub.add_parser(
        "commit",
        help="外部編集後にinbox配下の未コミット変更をコミット・push（差分なしなら無動作）",
    )

    sub.add_parser(
        "enable",
        help="feedback-inboxフラグファイルを作成する",
    )
    sub.add_parser(
        "disable",
        help="feedback-inboxフラグファイルを削除する",
    )
    sub.add_parser(
        "status",
        help="feedback-inboxの有効状態を判定する（正常時exit 0、無効時exit 1で原因を標準エラー出力へ書く）",
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
        "--autopilot",
        action="store_true",
        help="agent-toolkit:autopilotスキルの併用を指示するプロンプトを付与する",
    )

    tbd_add = sub.add_parser("tbd-add", help="TBDをtbd/inboxへ投入する")
    tbd_add.add_argument(
        "repo_path",
        metavar="REPO_PATH",
        help="対象リポジトリのローカルパス（リモートURLを自動取得して格納）。",
    )
    tbd_add.add_argument(
        "--scope",
        metavar="NAME",
        default=None,
        help="呼び出し元固有のスコープ識別子（任意。frontmatterにscope: <NAME>として記録）。",
    )
    tbd_add.add_argument(
        "--question-type",
        choices=("free", "yesno", "choice"),
        default="free",
        help="質問種別（既定: free）。",
    )
    tbd_add.add_argument(
        "--choices",
        metavar="A,B,C",
        default=None,
        help="question-type=choice時の選択肢（カンマ区切り）。",
    )
    tbd_add.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="*",
        help="投入するTBDメッセージ（省略時は$EDITORで編集する）。",
    )

    tbd_list = sub.add_parser("tbd-list", help="TBDをtarget_repoごとに出力する")
    tbd_list.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターする。",
    )
    tbd_list.add_argument(
        "--status",
        choices=("all", "answered", "unanswered"),
        default="all",
        help="回答状況でフィルターする（既定: all）。",
    )
    tbd_list.add_argument(
        "--skip-pull",
        action="store_true",
        help="git pull --ff-onlyをスキップする（ログイン時など軽量参照用）。",
    )

    tbd_answer = sub.add_parser(
        "tbd-answer",
        help="未回答TBDを1件ずつ画面表示し$EDITORで回答する",
    )
    tbd_answer.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターする。",
    )

    tbd_edit = sub.add_parser("tbd-edit", help="$EDITORでTBDを編集してcommit・push")
    tbd_edit.add_argument(
        "filename", metavar="FILENAME", help="編集対象のtbd/inboxファイル名。"
    ).completer = _tbd_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]

    tbd_adopt = sub.add_parser(
        "tbd-adopt",
        help="回答済みTBDをtbd/inboxからtbd/adopted/へ移動しcommit・push",
    )
    tbd_adopt.add_argument(
        "filenames", metavar="FILENAME", nargs="+", help="採用するTBDファイル名（1個以上）。"
    ).completer = _tbd_filename_completer  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
    tbd_adopt.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="採否結果のメモ（本文末尾の`## 処理結果`節へ追記する）。",
    )
    tbd_adopt.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help="対応する対象リポジトリのcommit hash（本文末尾の`## 処理結果`節へ追記する）。",
    )

    enable_completion(parser)
    return parser


def _feedback_filename_completer(prefix: str, **_: object) -> list[str]:
    """argcomplete用のフィードバックファイル名補完候補生成。

    `~/private-notes/feedback/inbox/`配下の`*.md`ファイル名をprefix一致で返す。
    ディレクトリ不在時は空リストを返す。
    """
    feedback_dir = pathlib.Path.home() / "private-notes" / "feedback" / "inbox"
    if not feedback_dir.exists():
        return []
    return sorted(p.name for p in feedback_dir.iterdir() if p.suffix == ".md" and p.name.startswith(prefix))


def _cmd_enable(home: pathlib.Path) -> None:
    """enableサブコマンド: feedback-inboxフラグファイルを作成する。"""
    path = _flag_path(home)
    if path.exists():
        print(f"既に有効です: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    print(f"有効化しました: {path}")


def _cmd_disable(home: pathlib.Path) -> None:
    """disableサブコマンド: feedback-inboxフラグファイルを削除する。"""
    path = _flag_path(home)
    if not path.exists():
        print(f"既に無効です: {path}")
        return
    path.unlink()
    print(f"無効化しました: {path}")


def _cmd_status(home: pathlib.Path) -> typing.NoReturn:
    """statusサブコマンド: feedback-inboxの有効状態を判定し終了コードで通知する。"""
    code, message = _check_environment(home)
    stream = sys.stdout if code == 0 else sys.stderr
    print(message, file=stream)
    sys.exit(code)


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
    args = parser.parse_args(argv)
    if home is None:
        home = pathlib.Path.home()
    if now is None:
        now = datetime.datetime.now()
    if args.subcommand == "enable":
        _cmd_enable(home)
        sys.exit(0)
    if args.subcommand == "disable":
        _cmd_disable(home)
        sys.exit(0)
    if args.subcommand == "status":
        _cmd_status(home)
    private_notes = _ensure_environment(home)
    dispatch = {
        "add": lambda: _cmd_add(args, private_notes, now, home),
        "list": lambda: _cmd_list(args, private_notes),
        "show": lambda: _cmd_show(args, private_notes),
        "adopt": lambda: _cmd_adopt(args, private_notes, now),
        "reject": lambda: _cmd_reject(args, private_notes, now),
        "rm": lambda: _cmd_rm(args, private_notes),
        "edit": lambda: _cmd_edit(args, private_notes),
        "commit": lambda: _cmd_commit(private_notes),
        "process-loop": lambda: _cmd_process_loop(args, private_notes),
        "tbd-add": lambda: _cmd_tbd_add(args, private_notes, now, home),
        "tbd-list": lambda: _cmd_tbd_list(args, private_notes),
        "tbd-answer": lambda: _cmd_tbd_answer(args, private_notes),
        "tbd-edit": lambda: _cmd_tbd_edit(args, private_notes),
        "tbd-adopt": lambda: _cmd_tbd_adopt(args, private_notes, now),
    }
    dispatch[args.subcommand]()
    sys.exit(0)
