"""宣言の復旧操作と未確定の読込条件を判定する。"""

# 公開関数の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import os
import pathlib
from typing import TYPE_CHECKING

from agent_toolkit._common import delegated_session, read_triggers
from agent_toolkit._common.plugin_roots import (
    PluginRootError,
)
from agent_toolkit._common.shell_segments import split_bash_commands
from agent_toolkit._common.shell_tokens import is_atk_command, split_words
from agent_toolkit._hooks import (
    agent_id,
    plugin_resources,
    rules_context,
    work_contract,
)
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.large_reads import (
    bash_read_paths,
    sed_read_range,
)
from agent_toolkit._hooks.pretooluse.notices import _llm_notice
from agent_toolkit._hooks.pretooluse.read_delivery_paths import _read_name_hints
from agent_toolkit._hooks.pretooluse.read_delivery_roles import _subagent_task_documents

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


def _condition_candidate_notice(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> str:
    """機械確定できない判断条件を、案内済み遮断の集合へ加えずに提示する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if not session_id or delegated_session.is_lightweight_delegated(os.environ):
        return ""
    cwd = str(payload.get("cwd", ""))
    if _work_record_only(operation_skills.written_paths(tool_name, tool_input, cwd), payload, session_id):
        return ""
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    records = records if isinstance(records, dict) else {}
    roles = read_delivery.active_roles(payload, session_id, records, agent) | _subagent_task_documents(payload)
    selected_sources = {target.path for target in read_delivery.selected_targets(records, agent)} | roles
    command = tool_input.get("command")
    acquisition_paths = (
        {path.resolve() for group in bash_read_paths(command, cwd) for path in group}
        if tool_name == "Bash" and isinstance(command, str)
        else set()
    )
    try:
        host = "codex" if is_codex_payload(payload) else "claude"
        rows = [
            *read_delivery.declarations(cwd, host=host, session_id=session_id),
            *_named_reference_rows(tool_name, tool_input, cwd, records, agent, selected_sources, host=host),
        ]
        candidates = [
            row
            for row in rows
            if (not row.source.name.endswith(".subagent.md") or row.source in roles)
            and read_delivery.row_matches(row, tool_name, tool_input, cwd)
            and not read_delivery.condition_confirmed(row, tool_name, tool_input, cwd)
            and (
                row.source in selected_sources
                or row.source.is_file()
                and read_delivery.target_ready(records, read_triggers.ReadTarget(row.source), agent)
            )
            and any(
                target.path not in acquisition_paths and not read_delivery.target_ready(records, target, agent)
                for target in row.targets
            )
        ]
    except OSError, UnicodeError, ValueError:
        # 宣言の取得失敗は本体の診断が通知する。
        return ""
    if not candidates:
        return ""
    shown_targets = {row: tuple(target for target in row.targets if target.path not in acquisition_paths) for row in candidates}
    candidates = list({(row.condition, tuple(sorted(map(str, shown_targets[row])))): row for row in candidates}.values())
    shown = "\n".join(
        f"- 条件: {row.condition}。資料: {'、'.join(map(str, shown_targets[row]))}（宣言: {row.source}:{row.line}）"
        for row in candidates
    )
    return _llm_notice(
        "現在の操作に関連する読込条件の候補。条件の成立は未確定である。\n" + shown,
        tag=_WARN_TAG,
        fix="自身の役割と現工程で各条件が成立するか確認し、成立する規範資料の全文を判断・操作の前に読め。",
        removable_cause=True,
        escalate_on_repeat=False,
    )


def _named_reference_rows(
    tool_name: str,
    tool_input: dict,
    cwd: str,
    records: dict,
    agent: str,
    selected_sources: set[pathlib.Path],
    *,
    host: str,
) -> list[read_triggers.ReadTrigger]:
    """主体が取得済みの宣言元のうち、読取対象を本文で名指しする文書が補う宣言を返す。

    補う宣言は本文の全行の解決で求まるため、全文書では求めない。読込条件の候補になるのは、
    宣言元を取得済みで、今の操作が読む資料を名指しする文書だけである。
    """
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import
    from agent_toolkit._hooks.pretooluse.read_delivery import _declaration_sources  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    hints = _read_name_hints(tool_name, tool_input, cwd)
    if not hints:
        return []
    root = plugin_resources.plugin_root()
    rows: list[read_triggers.ReadTrigger] = []
    # `_declaration_sources`は実体のパスを返すため、ここで解決し直さない。
    for source in _declaration_sources(cwd):
        if not read_triggers.is_normative_document(source):
            continue
        if source not in selected_sources and not read_delivery.target_ready(records, read_triggers.ReadTarget(source), agent):
            continue
        if not any(hint in source.read_text(encoding="utf-8") for hint in hints):
            continue
        rows.extend(read_triggers.named_reference_rows(source, root, host=host, project=cwd))
    return rows
