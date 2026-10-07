"""CodexとClaudeの委譲先を非同期MCPとして公開するagents_serverの起動スクリプト。

MCPホストの設定（`mcp.json`など）が本ファイルのパスを起動する。実装は`agent_toolkit._agents_server`配下に置き、
本ファイルは引数の解釈と`_agents_server.mcp_tools.serve`の呼び出しだけを持つ。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from agent_toolkit._agents_server import mcp_tools


def main(argv: Sequence[str] | None = None) -> int:
    """引数に応じて依存の確認またはMCP stdio transportの起動を行う。"""
    parser = argparse.ArgumentParser(description="CodexとClaudeの委譲先を非同期MCPとして公開する。")
    parser.add_argument(
        "--check-dependencies",
        action="store_true",
        help="Claude Agent SDKの依存を読み込み、optionsを構築できることを確かめる。",
    )
    args = parser.parse_args(argv)
    return mcp_tools.serve(check_dependencies=args.check_dependencies)


if __name__ == "__main__":
    raise SystemExit(main())
