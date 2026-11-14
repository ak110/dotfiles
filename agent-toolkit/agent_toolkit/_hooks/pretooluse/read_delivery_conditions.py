"""宣言の復旧操作と作業記録の書込を判定する。"""

# 公開関数の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from agent_toolkit._common import delegated_session
from agent_toolkit._common.plugin_roots import (
    PluginRootError,
)
from agent_toolkit._common.shell_segments import split_bash_commands
from agent_toolkit._common.shell_tokens import is_atk_command, split_words
from agent_toolkit._hooks import (
    agent_id,
    work_contract,
)
from agent_toolkit._hooks.pretooluse.large_reads import (
    sed_read_range,
)

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def _codex_dependency_repair(tokens: tuple[str, ...], error: Exception) -> bool:
    """未導入のBasicsだけを、導入済みToolkitと同じmarketplaceから追加できる。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if str(error).casefold() != "codexのagent-basics導入先が一意でない: 0件":
        return False
    if tokens[1:3] != ("plugin", "add") or len(tokens) != 5 or tokens[4] != "--json":
        return False
    if not tokens[3].startswith("agent-basics@"):
        return False
    try:
        installed = read_delivery.codex_installed_plugins(tokens[0])
    except PluginRootError:
        return False
    marketplaces = {
        item["marketplaceName"]
        for item in installed
        if isinstance(item, dict)
        and item.get("name") == "agent-toolkit"
        and item.get("installed") is True
        and item.get("enabled") is True
        and isinstance(item.get("marketplaceName"), str)
        and item["marketplaceName"]
    }
    return len(marketplaces) == 1 and tokens[3] == "agent-basics@" + next(iter(marketplaces))


def _declaration_recovery_operation(tool_name: str, tool_input: dict, error: Exception) -> bool:
    """読取・公開診断と、原因に対応する正規の依存追加を宣言取得から独立させる。"""
    if not isinstance(error, PluginRootError):
        return False
    if tool_name in {"Read", "Glob", "Grep", "mcp__agents_server__list"}:
        return True
    command = tool_input.get("command")
    if tool_name != "Bash" or not isinstance(command, str):
        return False
    # 回復用の例外では、単一の静的な呼出しだけを受理する。
    if any(char in command for char in ("\n", "\r")):
        return False
    commands = split_bash_commands(command)
    if len(commands) != 1:
        return False
    try:
        words = split_words(commands[0][1])
    except ValueError:
        return False
    if any(word.expansion or word.redirection or set("()<>") & set(word.unquoted) for word in words):
        return False
    tokens = tuple(words)
    if not tokens:
        return False
    name = pathlib.Path(tokens[0]).name
    if name in {"cat", "cat.exe"}:
        return len(tokens) > 1
    if sed_read_range(tokens) is not None:
        return True
    if name in {"codex", "codex.exe"}:
        return (
            tokens[1:3] == ("plugin", "list")
            and all(token in {"--json", "--help"} for token in tokens[3:])
            or tokens[1:] == ("plugin", "add", "--help")
            or _codex_dependency_repair(tokens, error)
        )
    if is_atk_command(tokens):
        return tokens[1:2] in {("info",), ("--help",)} or tokens[1:3] in {
            ("agents", "logs"),
            ("agents", "list"),
            ("agents", "status"),
        }
    return False


def _work_record_only(written: list[str], payload: dict, session_id: str) -> bool:
    """書込先が全て、その主体が到達点と残工程を保つ作業記録（委譲先の引き継ぎ記録、メインの作業契約）かを返す。

    作業記録の更新は成果原稿の執筆と目的が異なるため、執筆基準などの資料の案内を出さない。
    案内を出すと、作業記録を保つたびに使わない基準の読込を求めることになる（2026年11月の実測で、
    作業契約の作成に執筆基準8本の取得を求める通知が3セッションとも出ていた）。
    """
    if not written:
        return False
    records = []
    handoff = delegated_session.handoff_path(os.environ)
    if handoff:
        records.append(pathlib.Path(handoff).resolve())
    if session_id and agent_id.resolve_hook_agent_id(payload) == agent_id.MAIN_AGENT_ID:
        contract = work_contract.record_path(session_id)
        if contract is not None:
            records.append(contract.resolve())
    return bool(records) and all(pathlib.Path(path).resolve() in records for path in written)
