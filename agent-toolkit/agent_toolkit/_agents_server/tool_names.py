"""agents_serverのMCPツール名の判定に使う値の正本を保持する。

Claude Codeはplugin経由で配布したMCPサーバーのツール名を`mcp__plugin_<plugin-name>_<server-key>__`で、
Codexは`mcp__<server-key>__`で修飾する。この修飾と、子sessionを生成する起動ツールの操作名を判定する箇所は、
session状態側の孫session追跡、PreToolUse・PostToolUseの各フック、session-reviewの証拠抽出器に分かれるため、
受理する値の集合を本モジュールへ集約し、判定箇所が同じ値を参照する。

判定箇所が値を個別に保持すると、新しい修飾形式や起動ツールへ追随した箇所と追随しない箇所が混在する。
追随しない箇所では実行環境が配送したツール名が未知の名前として扱われ、当該箇所の判定が
その呼び出しに対して働かない（孫sessionが追跡対象へ入らない、親sessionが自動再開しない、
振り返りの証拠抽出が委譲先を収集しないなど）。起動ツールの集合と`agents_server`の登録ツールの一致は
`agents_server_mcp_test.py`が検査する。
"""

MCP_NAMESPACES: tuple[str, ...] = (
    "mcp__plugin_agent-toolkit_agents_server__",
    "mcp__agents_server__",
)
"""ホストがagents_serverのMCPツール名へ付ける修飾接頭辞。"""

START_OPERATIONS: frozenset[str] = frozenset(("start", "start_custom", "start_explore", "start_write", "start_shell"))
"""子sessionを生成するagents_serverの起動ツールの操作名（修飾接頭辞を除いた名前）。"""
