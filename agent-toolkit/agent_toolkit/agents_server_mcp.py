"""CodexとClaudeの委譲先を非同期MCPとして公開するagents_serverの起動スクリプト。

MCPホストの設定（`mcp.json`など）が本ファイルのパスを起動する。実装は`agent_toolkit._agents_server`配下に置き、
本ファイルは引数の解釈と、依存の確認（`_agents_server.mcp_tools.check_dependencies`）または
共有管理サービスへの転送（`_agents_server.service_forward.forward`）の呼び出しだけを持つ。
"""

from __future__ import annotations

# 通常起動は軽量転送だけ、依存検査時だけMCP・backendを読み込む。
# pylint: disable=import-outside-toplevel
import argparse
from collections.abc import Sequence


def main(argv: Sequence[str] | None = None) -> int:
    """引数に応じて依存の確認、またはMCP stdioから共有管理サービスへの転送を行う。"""
    parser = argparse.ArgumentParser(description="CodexとClaudeの委譲先を非同期MCPとして公開する。")
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Claude Agent SDKの依存を読み込み、optionsを構築できることを確かめる。",
    )
    args = parser.parse_args(argv)
    if args.check_dependencies:
        from agent_toolkit._agents_server import mcp_tools

        return mcp_tools.check_dependencies()
    from agent_toolkit._agents_server.service_forward import forward

    return forward()


if __name__ == "__main__":
    raise SystemExit(main())
