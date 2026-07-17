#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
# /// script
# requires-python = ">=3.12"
# dependencies = ["argcomplete", "watchdog>=6.0.0", "platformdirs>=4.0"]
# ///
"""agent-toolkitプラグイン提供CLI`atk`のPEP 723 entrypoint。

サブコマンド構成: `atk fb <sub>`形式。
`fb`サブパーサ配下に旧`dotfiles-fb`のサブコマンド群を再登録する。

- fb add: inboxへフィードバックを投入する
- fb list: feedback/tbd inbox・processing全件を1件1行（filename・target_repo・状態ラベル・本文冒頭要約）で出力する
- fb show: feedback/tbd inboxの1件または全件（--all）の本文を表示する
  （`--include-processed`でFILENAME指定時にadopted・rejected配下も探索）
- fb start-processing: feedbackをinboxからprocessing/へ移動し処理中状態に遷移させコミット・push
- fb adopt: 採用としてinboxまたはprocessingからadopted/へ移動しコミット・push
- fb reject: 不採用としてinboxまたはprocessingからrejected/へ移動しコミット・push
- fb rm: inboxから単純削除しコミット・push
- fb edit: $EDITORで対象ファイルを編集しコミット・push
- fb commit: 外部編集後のinbox配下未コミット変更をコミット・push
- fb enable/disable/status: feedback-inbox有効化フラグの操作・判定
- fb process-loop: `claude /process-feedbacks`と`/agent-toolkit:exit-session`直接起動で常駐実行する
- fb tbd-add/tbd-list/tbd-answer/tbd-edit/tbd-adopt/tbd-rm: TBD項目の操作

ハンドラ実装は`_atk_fb_add`・`_atk_fb_list`・`_atk_fb_show`・`_atk_fb_mutations`・
`_atk_fb_process_loop`・`_atk_fb_tbd`の各補助モジュールに分割し、
本モジュールはargparse定義・dispatch・エントリポイントと`enable`・`disable`・`status`の軽量ハンドラを保持する。
"""

import argparse
import datetime
import pathlib
import sys
import typing

# 兄弟モジュール（_atk_fb_*.py）を絶対importで解決するためsys.pathへ同一ディレクトリを挿入する。
# sys.path挿入前の相対解決を避けるため、モジュール内importはこの下に配置する。
# pylint: disable=wrong-import-position,protected-access
sys.path.insert(0, str(pathlib.Path(__file__).parent))

import _atk_fb_add as _add  # noqa: E402
import _atk_fb_common as _common  # noqa: E402
import _atk_fb_list as _list  # noqa: E402
import _atk_fb_mutations as _mutations  # noqa: E402
import _atk_fb_process_loop as _process_loop  # noqa: E402
import _atk_fb_show as _show  # noqa: E402
import _atk_fb_tbd as _tbd  # noqa: E402


def _extract_legacy_repo_path(argv: list[str]) -> tuple[list[str], str | None]:
    """`fb add`・`fb tbd-add`のサブコマンド名直後のトークンが実在ディレクトリの場合、argparseへ渡す前に取り除く。

    REPO_PATH位置引数廃止後の後方互換のため、argparse解析前の生argvへ適用する。
    `messages`側のnargs="*"単一positionalでは、オプションで分断され前後2箇所に分かれた
    位置引数を一括で解決できない（argparseの既知の制約）ため、サブコマンド名直後という
    先頭位置に限定して抽出することで後続のオプション・MESSAGE位置を通常解析に委ねる。
    """
    if len(argv) < 2 or argv[0] != "fb" or argv[1] not in ("add", "tbd-add"):
        return argv, None
    candidate_index = 2
    if candidate_index >= len(argv):
        return argv, None
    candidate = argv[candidate_index]
    if candidate.startswith("-"):
        return argv, None
    candidate_path = pathlib.Path(candidate).expanduser()
    if not candidate_path.is_dir():
        return argv, None
    new_argv = argv[:candidate_index] + argv[candidate_index + 1 :]
    return new_argv, str(candidate_path)


def _add_target_repo_arg(parser: argparse.ArgumentParser, *, help_extra: str = "") -> None:
    """`--target-repo`オプションを共通形式で登録する。"""
    parser.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターまたは検証する。" + help_extra,
    )


def _build_fb_parser(fb: argparse.ArgumentParser) -> None:
    """`fb`サブパーサ配下にfeedback/TBD操作用サブコマンドを登録する。"""
    sub = fb.add_subparsers(dest="fb_subcommand", required=True)

    add = sub.add_parser("add", help="フィードバックをinboxへ投入する")
    add.add_argument(
        "messages",
        metavar="MESSAGE",
        nargs="*",
        help=(
            "投入するフィードバックメッセージ（省略時は$EDITORで編集する）。"
            "対象リポジトリは常にカレントディレクトリから解決する。"
            "メッセージ先頭がYAML frontmatter形式の場合はtarget_repo・sourceをCLIオプションより優先する。"
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

    list_ = sub.add_parser(
        "list", help="feedback/tbd inbox・processing全件を1件1行（filename・target_repo・状態ラベル・本文冒頭要約）で出力する"
    )
    list_.add_argument(
        "--target-repo",
        metavar="REPO",
        default=None,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターする。",
    )
    list_.add_argument("--type", choices=("all", "feedback", "tbd"), default="all", help="出力対象種別（既定: all）。")
    list_.add_argument(
        "--status",
        choices=("all", "active", "answered", "unanswered", "inbox", "processing", "adopted", "rejected"),
        default="active",
        help=(
            "表示範囲を限定する（既定: active）。"
            "`active`はfeedback側`inbox`・`processing`とtbd側`answered`を出力する。"
            "feedback側は`inbox`・`processing`・`adopted`・`rejected`・`all`で状態フォルダを切り替える。"
            "tbd側は`answered`・`unanswered`で回答状況を限定する"
            "（`inbox`・`processing`・`adopted`・`rejected`はtbd側では全件出力扱い）。"
        ),
    )
    list_.add_argument(
        "--category",
        default=None,
        help="指定時、同ラベルが付与されたfeedbackのみへ限定する。",
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
    )
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
        choices=("all", "active", "answered", "unanswered"),
        default="active",
        help=(
            "表示範囲を限定する（既定: active）。"
            "`active`はfeedback側`inbox`・`processing`とtbd側`answered`を出力する。"
            "`answered`・`unanswered`はtbd側のみに作用しfeedback側には作用しない。"
        ),
    )
    show.add_argument(
        "--skip-pull",
        action="store_true",
        help="git pull --ff-onlyをスキップする（ログイン時など軽量参照用）。",
    )
    show.add_argument(
        "--include-processed",
        action="store_true",
        help="FILENAME指定時にadopted・rejected配下も探索対象へ含める（--allには影響しない）。",
    )

    start_processing = sub.add_parser(
        "start-processing",
        help="feedbackをinboxからprocessing/へ移動し処理中状態に遷移させコミット・push",
    )
    start_processing.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="処理開始するinboxファイル名（1個以上）。",
    )
    _add_target_repo_arg(start_processing, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    adopt = sub.add_parser("adopt", help="採用としてinboxまたはprocessingからadopted/へ移動しコミット・push")
    adopt.add_argument("filenames", metavar="FILENAME", nargs="+", help="採用するinboxファイル名（1個以上）。")
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
    reject.add_argument("filenames", metavar="FILENAME", nargs="+", help="不採用とするinboxファイル名（1個以上）。")
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

    rm = sub.add_parser("rm", help="inboxから単純削除しコミット・push")
    rm.add_argument("filenames", metavar="FILENAME", nargs="+", help="削除するinboxファイル名（1個以上）。")
    _add_target_repo_arg(rm, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    edit = sub.add_parser("edit", help="$EDITORで対象ファイルを編集しコミット・push")
    edit.add_argument(
        "filename",
        metavar="FILENAME",
        nargs="?",
        default=None,
        help="編集対象のinboxファイル名。省略時はinbox配下で最終追加のファイル（ファイル名順で最大）を対象とする。",
    )
    _add_target_repo_arg(edit, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

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
        "--no-update",
        action="store_true",
        help="1反復完了後のupdate-dotfiles実行と自身再起動を抑止する。",
    )
    loop.add_argument(
        "--model",
        default="opus",
        help="claude起動時の--modelオプションの既定はopusとする。",
    )

    tbd_add = sub.add_parser("tbd-add", help="TBDをtbd/inboxへ投入する")
    tbd_add.add_argument(
        "--scope",
        metavar="NAME",
        default=None,
        help="呼び出し元固有のスコープ識別子（任意。frontmatterにscope: <NAME>として記録）。",
    )
    tbd_add.add_argument(
        "--source",
        metavar="NAME",
        default=None,
        help="投入元の識別子（任意。frontmatterに source: <NAME> として記録する。既知値: session-hold）。",
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
        help="投入するTBDメッセージ（省略時は$EDITORで編集する）。対象リポジトリは常にカレントディレクトリから解決する。",
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
    tbd_edit.add_argument("filename", metavar="FILENAME", help="編集対象のtbd/inboxファイル名。")
    _add_target_repo_arg(tbd_edit, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    tbd_adopt = sub.add_parser(
        "tbd-adopt",
        help="回答済みTBDをtbd/inboxからtbd/adopted/へ移動しcommit・push",
    )
    tbd_adopt.add_argument("filenames", metavar="FILENAME", nargs="+", help="採用するTBDファイル名（1個以上）。")
    tbd_adopt.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="採否結果のメモ（本文末尾の`## 処理結果`節へ追記する）。--note=VALUE形式で渡すことを推奨。",
    )
    tbd_adopt.add_argument(
        "--commit",
        metavar="SHA",
        default=None,
        help="対応する対象リポジトリのcommit hash（本文末尾の`## 処理結果`節へ追記する）。--commit=VALUE形式で渡すことを推奨。",
    )
    _add_target_repo_arg(tbd_adopt, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")

    tbd_rm = sub.add_parser(
        "tbd-rm",
        help="TBD項目をtbd/inboxから単純削除しcommit・push",
    )
    tbd_rm.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="+",
        help="削除するTBDファイル名（1個以上）。",
    )
    tbd_rm.add_argument(
        "--note",
        metavar="TEXT",
        default=None,
        help="削除理由のメモ（commit messageへ追記する）。",
    )
    _add_target_repo_arg(tbd_rm, help_extra="指定時は対象filenameのfrontmatterと一致するか検証する。")


def _build_parser() -> argparse.ArgumentParser:
    """`atk`トップレベルargparseパーサーを構築する。"""
    parser = argparse.ArgumentParser(
        prog="atk",
        description="agent-toolkitプラグイン提供CLI。",
    )
    top = parser.add_subparsers(dest="command", required=True)
    fb = top.add_parser("fb", help="フィードバック・TBDの操作")
    _build_fb_parser(fb)
    return parser


def _cmd_enable(home: pathlib.Path) -> None:
    """enableサブコマンド: feedback-inboxフラグファイルを作成する。"""
    path = _common._flag_path(home)
    if path.exists():
        print(f"既に有効です: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    print(f"有効化しました: {path}")


def _cmd_disable(home: pathlib.Path) -> None:
    """disableサブコマンド: feedback-inboxフラグファイルを削除する。"""
    path = _common._flag_path(home)
    if not path.exists():
        print(f"既に無効です: {path}")
        return
    path.unlink()
    print(f"無効化しました: {path}")


def _cmd_status(home: pathlib.Path) -> typing.NoReturn:
    """statusサブコマンド: feedback-inboxの有効状態を判定し終了コードで通知する。"""
    code, message = _common._check_environment(home)
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
    # bash補完（argcomplete）は配布物内で直接遅延importして呼び出す。
    # `pytools._internal.cli`依存を避け、agent-toolkitプラグインの独立性を保つため。
    import argcomplete  # noqa: PLC0415  # pylint: disable=import-outside-toplevel  # 補完起動時のみ必要なので遅延importする

    argcomplete.autocomplete(parser)
    raw_argv = argv if argv is not None else sys.argv[1:]
    _common.warn_space_separated_option(raw_argv)
    raw_argv, repo_path_override = _extract_legacy_repo_path(raw_argv)
    args = parser.parse_args(raw_argv)
    args.repo_path_override = repo_path_override
    if home is None:
        home = pathlib.Path.home()
    if now is None:
        now = datetime.datetime.now()
    if args.command != "fb":
        parser.error(f"未知のトップレベルコマンド: {args.command}")
    sub = args.fb_subcommand
    if sub == "enable":
        _cmd_enable(home)
        sys.exit(0)
    if sub == "disable":
        _cmd_disable(home)
        sys.exit(0)
    if sub == "status":
        _cmd_status(home)
    private_notes = _common._ensure_environment(home)
    dispatch = {
        "add": lambda: _add._cmd_add(args, private_notes, now, home),
        "list": lambda: _list._cmd_list(args, private_notes),
        "show": lambda: _show._cmd_show(args, private_notes),
        "start-processing": lambda: _mutations._cmd_start_processing(args, private_notes),
        "adopt": lambda: _mutations._cmd_adopt(args, private_notes, now),
        "reject": lambda: _mutations._cmd_reject(args, private_notes, now),
        "rm": lambda: _mutations._cmd_rm(args, private_notes),
        "edit": lambda: _mutations._cmd_edit(args, private_notes),
        "commit": lambda: _mutations._cmd_commit(private_notes),
        "process-loop": lambda: _process_loop._cmd_process_loop(args, private_notes),
        "tbd-add": lambda: _tbd._cmd_tbd_add(args, private_notes, now, home),
        "tbd-list": lambda: _tbd._cmd_tbd_list(args, private_notes),
        "tbd-answer": lambda: _tbd._cmd_tbd_answer(args, private_notes),
        "tbd-edit": lambda: _tbd._cmd_tbd_edit(args, private_notes),
        "tbd-adopt": lambda: _tbd._cmd_tbd_adopt(args, private_notes, now),
        "tbd-rm": lambda: _tbd._cmd_tbd_rm(args, private_notes),
    }
    dispatch[sub]()
    if not sub.startswith("tbd-"):
        _common.notify_unanswered_tbds_if_any(private_notes, getattr(args, "target_repo", None))
    sys.exit(0)


if __name__ == "__main__":
    main()
