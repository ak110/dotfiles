"""`atk serve`の引数の登録と実行の振り分け。

起動処理（`cli.py`）はWebサーバーの依存を読み込むため、引数の登録だけを行う`atk`の起動ごとに読み込まないよう、
実行するときだけ遅延importする。
"""

import argparse
import importlib
import pathlib

from agent_toolkit._atk import cli_support as _cli_support


def _port_type(value: str) -> int:
    """`--port`の値を1から65535までの整数として検証する。"""
    try:
        port = int(value)
    except ValueError as error:
        raise _cli_support.argument_type_error(
            f"portが整数ではない: {value}", "portは1から65535までの整数で指定する"
        ) from error
    if not 1 <= port <= 65535:
        raise _cli_support.argument_type_error(f"portが範囲外である: {value}", "portは1から65535までの整数で指定する")
    return port


def build_parser(parser: argparse.ArgumentParser) -> None:
    """`atk serve`の引数を登録する。"""
    parser.add_argument("serve_action", nargs="?", choices=("logs",), help="user serviceのjournalを表示")
    parser.add_argument("-f", "--follow", action="store_true", help="直近100行を表示して追従")
    parser.add_argument(
        "--host",
        default=None,
        help="待受ホスト（環境変数 AGENT_TOOLKIT_SERVE_HOST、設定ファイルからも参照）",
    )
    parser.add_argument(
        "--port",
        default=None,
        type=_port_type,
        help="待受ポート（環境変数 AGENT_TOOLKIT_SERVE_PORT、設定ファイルからも参照）",
    )


def dispatch(args: argparse.Namespace, *, parser: argparse.ArgumentParser, home: pathlib.Path) -> int:
    """`atk serve`を実行し、終了コードを返す。`parser`は`--follow`の誤用を報告するトップレベルのパーサー。"""
    serve_cli = importlib.import_module("agent_toolkit._atk.serve.cli")
    if args.serve_action == "logs":
        return serve_cli.show_logs(follow=args.follow)
    if args.follow:
        parser.error("--followは`atk serve logs`で指定してください。")
    try:
        serve_cli.run(host=args.host, port=args.port, home=home)
    except ValueError as error:
        _cli_support.report_rejected(args, error)
        return 1
    return 0
