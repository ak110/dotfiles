"""`atk serve`の引数の登録と実行の振り分け。

起動処理（`cli.py`）はWebサーバーの依存を読み込むため、引数の登録だけを行う`atk`の起動ごとに読み込まないよう、
実行するときだけ遅延importする。
"""

from __future__ import annotations

import argparse
import importlib
import pathlib
from typing import TYPE_CHECKING

from agent_toolkit._atk import cli_support as _cli_support

if TYPE_CHECKING:
    from agent_toolkit._atk.serve.agent_routes import AgentListing
    from agent_toolkit._atk.serve.versions import AgentsVersion


def _port_type(value: str) -> int:
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
    parser.add_argument(
        "serve_action", nargs="?", choices=("logs", "versions"), help="ログの表示、または公開バージョンとの照合"
    )
    parser.add_argument("-f", "--follow", action="store_true", help="直近100行を表示して追従")
    parser.add_argument("--published", help="検収する公開コミットの完全OID（省略時はdotfilesのupstreamの公開先を照会）")
    parser.add_argument("--skip-serve", action="store_true", help="atk serveが未導入のホストでversionsの対象から外す")
    parser.add_argument(
        "--skip-agents-server", action="store_true", help="agents_serverが未導入のホストでversionsの対象から外す"
    )
    parser.set_defaults(serve_parser=parser)
    parser.add_argument(
        "--host",
        default=None,
        help="待受ホスト（環境変数 AGENT_TOOLKIT_SERVE_HOST、atk configのserve_hostからも参照）",
    )
    parser.add_argument(
        "--port",
        default=None,
        type=_port_type,
        help="待受ポート（環境変数 AGENT_TOOLKIT_SERVE_PORT、atk configのserve_portからも参照）",
    )


def dispatch(
    args: argparse.Namespace,
    *,
    home: pathlib.Path,
    agent_listing: AgentListing | None = None,
    agents_version: AgentsVersion | None = None,
) -> int:
    if args.serve_action == "versions":
        if args.follow:
            args.serve_parser.error("--followは`atk serve logs`で指定してください。")
        if args.skip_serve and args.skip_agents_server:
            args.serve_parser.error("照合するサービスを1つ以上指定してください。")
        serve_cli = importlib.import_module("agent_toolkit._atk.serve.cli")
        return serve_cli.show_versions(args, home=home, agents_version=agents_version)
    if args.published is not None or args.skip_serve or args.skip_agents_server:
        args.serve_parser.error("--published・--skip-serve・--skip-agents-serverは`atk serve versions`で指定してください。")
    serve_cli = importlib.import_module("agent_toolkit._atk.serve.cli")
    if args.serve_action == "logs":
        return serve_cli.show_logs(follow=args.follow)
    if args.follow:
        args.serve_parser.error("--followは`atk serve logs`で指定してください。")
    try:
        serve_cli.run(host=args.host, port=args.port, home=home, agent_listing=agent_listing, agents_version=agents_version)
    except ValueError as error:
        _cli_support.report_rejected(args, error)
        return 1
    return 0
