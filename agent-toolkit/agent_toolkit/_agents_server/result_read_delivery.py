"""正式な結果回収時に、起動した役割の親契約から判断資料を案内する。"""

from __future__ import annotations

import pathlib
import subprocess
from collections.abc import Mapping
from typing import Any

from agent_toolkit._agents_server import result_projection
from agent_toolkit._common import delegated_session, read_triggers, session_registry, task_documents
from agent_toolkit._common.session_state import read_state
from agent_toolkit._git import document_acquisition


def with_read_guidance(
    result: dict[str, Any], environment: Mapping[str, str], *, state_root: pathlib.Path | None = None
) -> dict[str, Any]:
    """未取得資料を結果の次操作へ加え、結果本文と回収の成否を保持する。"""
    resolution = session_registry.resolve(result["session_id"], state_root=state_root)
    info = resolution.resume_info or resolution.handoff_info
    if info is None:
        return result
    document = task_documents.launched_task_document(info.launch_info.prompt)
    if document is None:
        return result
    recipient = delegated_session.current_conversation_session_id(environment)
    if recipient is None:
        return result
    host = "codex" if environment.get("CODEX_THREAD_ID") else "claude"
    root = document.parent.parent
    records = read_state(recipient).get(document_acquisition.OPERATION_SKILL_READY_KEY, {})
    try:
        if not isinstance(records, dict):
            raise ValueError("資料の取得記録が辞書形式ではない")
        parents = read_triggers.parent_contract_targets(document, root, host=host, project=info.cwd)
        rows = [
            row
            for parent in parents
            for row in read_triggers.parse_tables(parent.path, root, host=host, project=info.cwd)
            if "delegation-result" in row.operations
        ]
        missing = list(
            dict.fromkeys(
                target
                for row in rows
                for target in row.targets
                if document_acquisition.acquisition_state(records, target, "main") != "current"
            )
        )
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as error:
        return result_projection.append_result_next_action(
            result, f"受領時の読込宣言を解決できない（役割: {document}）: {error}。親契約の読込表を確認せよ。"
        )
    if not missing:
        return result
    # 自然文の条件は受領した主体が判定する。未確定の候補をread_requirementsへ入れると、
    # PostToolUseが条件にかかわらず必読の選択として記録してしまう。
    steps = "\n".join(
        f"- 条件: {row.condition}。資料: " + "、".join(f"`{target.path}`" for target in row.targets if target in missing)
        for row in rows
        if any(target in missing for target in row.targets)
    )
    return result_projection.append_result_next_action(
        result, "返却内容を検収する前に、親契約の読込条件を確認し、該当する未取得資料を全文読め。\n" + steps
    )
