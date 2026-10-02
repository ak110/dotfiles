"""agents_serverのMCPツール名の判定に使う値をまとめる。

Claude Codeはplugin経由で配布したMCPサーバーのツール名を`mcp__plugin_<plugin-name>_<server-key>__`で、
Codexは`mcp__<server-key>__`で修飾する。この修飾と、子sessionを生成する起動ツールの操作名を判定する箇所は、
session状態側の孫session追跡、PreToolUse・PostToolUseの各フック、session-reviewの証拠抽出器に分かれるため、
受理する値の集合を本モジュールへ集約し、判定箇所が同じ値を参照する。

判定箇所が値を個別に保持すると、新しい修飾形式や起動ツールへ追随した箇所と追随しない箇所が混在する。
追随しない箇所では実行環境が配送したツール名が未知の名前として扱われ、そうした箇所の判定が
その呼び出しに対して働かない（孫sessionが追跡対象へ入らない、親sessionが自動再開しない、
振り返りの証拠抽出が委譲先を収集しないなど）。起動ツールの集合と`agents_server`の登録ツールの一致は
`agents_server_mcp_test.py`が確かめる。
起動の種類は`start`の`mode`で選ぶため、種類に応じた判定は操作名ではなく`start_mode`の返り値を使う。
"""

from collections.abc import Mapping
from typing import Any

MCP_NAMESPACES: tuple[str, ...] = (
    "mcp__plugin_agent-toolkit_agents_server__",
    "mcp__agents_server__",
)
"""ホストがagents_serverのMCPツール名へ付ける修飾接頭辞。"""

START_OPERATIONS: frozenset[str] = frozenset(("start",))
"""子sessionを生成するagents_serverの公開起動ツールの操作名（修飾接頭辞を除いた名前）。"""

START_MODES: tuple[str, ...] = ("task", "delegate", "explore", "write", "shell")
"""`start`の`mode`が受理する値。先頭の`task`を省略時の値とする。"""

DEFAULT_START_MODE = START_MODES[0]

START_MODE_MODEL_TYPES: Mapping[str, str] = {"explore": "low_tier", "write": "write", "shell": "low_tier"}
"""`model_type`を省略した起動で使う工程別設定の種別。taskはタスク文書から、delegateは必須の指定から決める。"""

LEGACY_START_MODES: Mapping[str, str] = {
    "start_custom": "delegate",
    "start_explore": "explore",
    "start_write": "write",
    "start_shell": "shell",
}
"""`start`へ統合する前の起動ツール名と、統合後の`mode`の対応。

公開ツールとしては登録しない。統合前に保存された会話記録を読む処理（session-reviewの証拠抽出器など）が、
旧名の起動も委譲として認識するために使う。
"""

RECORDED_START_OPERATIONS: frozenset[str] = START_OPERATIONS | frozenset(LEGACY_START_MODES)
"""会話記録に現れ得る起動ツールの操作名。統合前の旧名を含む。"""


def start_mode(operation: str, arguments: Mapping[str, Any] | None) -> str | None:
    """起動ツールの操作名と入力から`mode`を返す。起動ツールでない場合と未知の`mode`では`None`を返す。"""
    if operation in START_OPERATIONS:
        mode = arguments.get("mode") if isinstance(arguments, Mapping) else None
        if mode is None:
            return DEFAULT_START_MODE
        return mode if mode in START_MODES else None
    return LEGACY_START_MODES.get(operation)
