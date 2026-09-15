"""agents_serverのMCPツール名をホストが修飾する接頭辞の正本を保持する。

Claude Codeはplugin経由で配布したMCPサーバーのツール名を`mcp__plugin_<plugin-name>_<server-key>__`で、
Codexは`mcp__<server-key>__`で修飾する。この修飾を判定する箇所は、session状態側の孫session追跡と
PreToolUse・PostToolUseの各フックに分かれるため、受理する接頭辞の集合を本モジュールへ集約し、
全ての判定箇所が同じ値を参照する。

判定箇所が接頭辞を個別に保持すると、新しい修飾形式へ追随した箇所と追随しない箇所が混在する。
追随しない箇所では実行環境が配送したツール名が未知の名前として扱われ、当該箇所の判定が
その呼び出しに対して働かない（孫sessionが追跡対象へ入らず、親sessionが自動再開しないなど）。
"""

MCP_NAMESPACES: tuple[str, ...] = (
    "mcp__plugin_agent-toolkit_agents_server__",
    "mcp__agents_server__",
)
"""ホストがagents_serverのMCPツール名へ付ける修飾接頭辞。"""
