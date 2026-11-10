"""WIの共通オプションと採用引数を、実行処理への依存なしで登録する。"""

import argparse
import pathlib
from typing import Any

lazy from agent_toolkit._atk import cli_support as _cli_support
lazy from agent_toolkit._atk import help_text as _atk_help
lazy from agent_toolkit._atk.wi import constants as _wi_constants
lazy from agent_toolkit._atk.wi import filenames as _wi_filenames
lazy from agent_toolkit._atk.wi import repo as _wi_repo


def _source_filter_type(value: str) -> str:
    """`--source`の値を検証するargparse `type=`コールバック。

    先頭`!`を除いた残りが空文字列の場合（`--source=`・`--source=!`）は`ArgumentTypeError`を送出する。
    """
    remainder = value[1:] if value.startswith("!") else value
    if not remainder:
        raise _cli_support.argument_type_error(
            "空文字列は指定できません", "空でない投入元識別子を指定する（例: --source=session-review）"
        )
    return value


def add_source_arg(parser: argparse.ArgumentParser, *, multiple: bool = False) -> None:
    """`--source`オプションを共通形式で登録する。"""
    parser.add_argument(
        "--source",
        metavar="NAME",
        type=_source_filter_type,
        action="append" if multiple else "store",
        default=None,
        help=(
            "投入元識別子（frontmatterのsource）で限定する。完全一致の値、"
            "または先頭に`!`を付けた否定指定（無指定エントリも対象に含む）を指定する。"
            "空文字列（`--source=`・`--source=!`）は拒否する。"
            "例: --source=session-review、--source=!session-review"
        ),
    )


def add_target_repo_arg(
    parser: argparse.ArgumentParser,
    *,
    help_extra: str = "",
    multiple: bool = False,
    required: bool = False,
    allow_all: bool = False,
    resolved_by_consumer: bool = False,
) -> None:
    """`--target-repo`オプションを共通形式で登録する。

    受理条件をヘルプ文面と同じ位置で確定する。`allow_all`は対象を限定しない`all`を受理するか、
    `resolved_by_consumer`はサブコマンド自身が未指定時の解決を行うかを表す。
    登録した値は`_resolve_wi_target_repo`が読み取る。
    """
    default_note = (
        "" if required else "省略時はカレントディレクトリが属するリポジトリを対象とし、Gitの作業ツリー外では対象を限定しない。"
    )
    all_note = f"`{_wi_repo.TARGET_REPO_ALL}`を指定すると対象を限定しない。" if allow_all else ""
    parser.add_argument(
        "--target-repo",
        metavar="REPO",
        action="append" if multiple else "store",
        default=None,
        required=required,
        help="対象リポジトリ（パスまたは正規化リモートURL）でフィルターまたは検証する。" + default_note + all_note + help_extra,
    )
    parser.set_defaults(
        _target_repo_multiple=multiple,
        _target_repo_allow_all=allow_all,
        _target_repo_resolved_by_consumer=resolved_by_consumer,
        target_repo_defaulted=False,
    )


def _absolute_note_path(raw_path: str) -> pathlib.Path:
    """メモファイルの絶対パスだけを受理する。"""
    path = pathlib.Path(raw_path)
    if not path.is_absolute():
        raise _cli_support.argument_type_error(
            f"--note-fileが絶対パスではない: {raw_path}", "--note-fileには絶対パスを指定する"
        )
    return path


def add_note_args(parser: argparse.ArgumentParser, *, help_text: str) -> None:
    """文字列またはUTF-8ファイルからメモを受け取る排他引数を登録する。"""
    note = parser.add_mutually_exclusive_group()
    note.add_argument("--note", metavar="TEXT", default=None, help=help_text)
    note.add_argument(
        "--note-file",
        metavar="PATH",
        type=_absolute_note_path,
        default=None,
        help=(
            "メモを記載したUTF-8ファイルの絶対パス。引用符・改行・バッククォートを含む本文を"
            "シェルのエスケープを介さず渡す場合に使う。--noteとは併用できない。"
        ),
    )


def add_bulk_transition_args(parser: Any, *, action_label: str) -> None:
    """状態遷移コマンドの一括操作引数を`rm`と同じ選択肢と省略時の値で追加する。

    一括フィルターの綴りは`--status`だけとする。`rm`と`return-to-inbox`は
    個別指定の探索先と差し戻し元を`--state`で受け取るため、同じ綴りが2つの概念を指す状態を避ける。
    フィルター専用の`list`・`show`・`grep`だけが`--state`を正式名として受理する。
    """
    parser.add_argument(
        "--all",
        action="store_true",
        help=f"--target-repoとフィルターに一致する全項目を{action_label}する。確認時は候補一覧を表示する。",
    )
    parser.add_argument(
        "--type",
        choices=("all", *_wi_constants.WI_TYPES),
        action="append",
        default=None,
        help="--allの対象種別。省略時は全種別を対象とする。",
    )
    parser.add_argument(
        "--status",
        dest="status",
        choices=("all", "active", "processable", *_wi_constants.WI_STATES),
        action="append",
        default=None,
        help="--allの対象状態。省略時はactiveを対象とする。listと同じ集合名を受理する。",
    )
    parser.add_argument(
        "--answered",
        choices=("all", "yes", "no"),
        action="append",
        default=None,
        help="--allのUWI回答状況。省略時は全ての回答状況を対象とする。",
    )
    add_source_arg(parser, multiple=True)
    parser.add_argument(
        "--yes",
        action="store_true",
        help=f"--allによる一括{action_label}の候補一覧と確認入力を省略する。",
    )
    parser.add_argument(
        "--skip-pull",
        action="store_true",
        help=(
            f"{action_label}対象の選定・確認をremote同期せずローカル状態で行う"
            f"（{action_label}の直前は毎回同期する）。--all指定時のみ有効。"
        ),
    )


def add_adopt_parser(sub: Any) -> None:
    """公開CLIとレーンの採用前検査へ同じ採用引数を登録する。"""
    adopt = _atk_help.add_command(sub, "adopt", **_atk_help.HELP["atk wi adopt"])
    adopt.add_argument(
        "filenames",
        metavar="FILENAME",
        nargs="*",
        help="採用するファイル名。--allと併用せず、個別指定では1個以上を指定する。",
    ).completer = _wi_filenames.make_filename_completer(_wi_constants.WI_ACTIVE_STATES)  # type: ignore[attr-defined]
    add_note_args(
        adopt,
        help_text="採否結果のメモ（本文末尾の`## 処理結果`節へ追記する）。--note=VALUE形式で渡すことを推奨。",
    )
    adopt.add_argument(
        "--commit",
        metavar="SHA",
        action="append",
        default=None,
        help=(
            "ローカルworktreeとrevisionを検証し、一意な長さの短縮OIDと件名を記録する。短縮OIDと完全OIDのどちらも受理する。"
            "worktreeまたはrevisionを解決できない場合は終了コード2で状態変更前に停止する。"
            "対象worktreeの絶対パスを--target-repoへ指定して再実行する。"
            "複数commitは--commitを反復指定する。--commit=VALUE形式で渡すことを推奨。"
        ),
    )
    adopt.add_argument(
        "--skip-push",
        action="store_true",
        help="管理リポジトリへのpushを省略してcommitだけ行う（連続操作の中間で用い、最後の操作では指定しない）。",
    )
    add_bulk_transition_args(adopt, action_label="採用")
    add_target_repo_arg(
        adopt,
        help_extra="個別指定時はfrontmatterと一致するか検証し、--all指定時は対象を限定する。",
        multiple=True,
    )
    adopt.set_defaults(subparser=adopt)
