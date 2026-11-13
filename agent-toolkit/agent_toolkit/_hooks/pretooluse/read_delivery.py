"""宣言された操作の前に、主体の文脈に未取得の資料を案内する。

必要な資料を初回に案内し、資料の取得判定を操作の許可条件にしない。
取得の観測だけを主体別の記録に反映する。
実際に配送された必要範囲を記録し、旧版の受信と現在版の更新を区別する。
軽量委譲は起動契約がスキルを使わないため対象外とする。
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re

from agent_toolkit._common import delegated_session, read_triggers
from agent_toolkit._common.plugin_roots import (
    codex_installed_plugins,
    plugin_resolution_scope,
    sibling_plugin_root,
)
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._common.shell_segments import extract_execution_segments
from agent_toolkit._common.shell_tokens import is_atk_command
from agent_toolkit._hooks import (
    agent_id,
    document_reads,
    plugin_resources,
    rules_context,
    work_contract,
)
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.notice import _WARN_TAG
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._hooks.pretooluse.large_reads import (
    bash_read_paths,
)
from agent_toolkit._hooks.pretooluse.notices import _llm_notice
from agent_toolkit._hooks.pretooluse.read_delivery_cache import _input_cached, _input_scope
from agent_toolkit._hooks.pretooluse.read_delivery_conditions import (
    _condition_candidate_notice,
    _declaration_recovery_operation,
    _work_record_only,
)
from agent_toolkit._hooks.pretooluse.read_delivery_guidance import (
    _deliver_documents,
    _merged_steps,
    acquisition_step,
)
from agent_toolkit._hooks.pretooluse.read_delivery_matching import (
    condition_confirmed,
    matches,
    row_matches,
)
from agent_toolkit._hooks.pretooluse.read_delivery_observation import (
    _observe_content,
    host_truncated,
)
from agent_toolkit._hooks.pretooluse.read_delivery_paths import (
    _acquiring,
    _document_owner,
    _read_paths,
    resolve_skill_document,
    skill_name,
)
from agent_toolkit._hooks.pretooluse.read_delivery_receipts import _receipt_targets, _refresh_receipts
from agent_toolkit._hooks.pretooluse.read_delivery_roles import (
    _foreign_task_document_notice,
    active_roles,
)
from agent_toolkit._hooks.pretooluse.read_delivery_selection import (
    _mark,
    _project_skill_rows,
    _review_reference,
    _select_result_documents,
    _select_targets,
    document_key,
    dynamic_targets,
    read_guidance_key,
    selected_targets,
    target_ready,
)
lazy from agent_toolkit._common import task_documents

__all__ = [
    "_condition_candidate_notice",
    "_declaration_recovery_operation",
    "_deliver_documents",
    "_mark",
    "_merged_steps",
    "_project_skill_rows",
    "_select_targets",
    "acquisition_step",
    "active_roles",
    "bash_read_paths",
    "codex_installed_plugins",
    "condition_confirmed",
    "delegated_session",
    "document_key",
    "dynamic_targets",
    "host_truncated",
    "matches",
    "plugin_resources",
    "read_guidance_key",
    "read_state",
    "read_triggers",
    "resolve_skill_document",
    "row_matches",
    "rules_context",
    "selected_targets",
    "skill_name",
    "target_ready",
    "task_documents",
    "update_state",
    "work_contract",
]


@_input_cached
def declarations(cwd: str, *, host: str = "claude", session_id: str = "") -> list[read_triggers.ReadTrigger]:
    """稼働版と対象プロジェクト・ユーザースキルの宣言を読む。"""
    root = plugin_resources.plugin_root()
    # 宣言表ごとの入れ子scopeを、1回のhook要求が共有する一覧へ接続する。
    with plugin_resolution_scope(session_id=session_id), read_triggers.resolution_scope():
        entries = [
            # `_declaration_sources`は実体のパスを返すため、ここで解決し直さない。
            (source, read_triggers.parse_tables(source, root, host=host, project=cwd, defer_material=True))
            for source in _declaration_sources(cwd)
        ]
    # 参照先の所有plugin全体は有効化しない。選択した実体の宣言は取得時に追加する。
    included = {row.source for source, rows in entries for row in rows if row.source != source}
    return list(dict.fromkeys(row for source, rows in entries if source not in included for row in rows))


@_input_cached
def _declaration_sources(cwd: str) -> list[pathlib.Path]:
    """宣言を読む文書（稼働版、対象プロジェクト・ユーザーのスキル、導入済みの品質スキル）を列挙する。"""
    root = plugin_resources.plugin_root()
    sources = read_triggers.document_sources(root, cwd)
    # 外部分離された品質スキルは、導入された実体がある場合に同じ表形式で取り込む。
    sibling = sibling_plugin_root("agent-basics", root)
    if sibling is not None:
        sources.extend(read_triggers.canonical_markdown_files(sibling / "skills"))
    return list(dict.fromkeys(sources))


@_input_scope
def deliver(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    """現在の操作に関係する宣言資料を配送または案内し、他の担当の手順書の取得へ警告を加え、通常操作の進行を保つ。

    ユーザー側の読込表から除いた行は、同じ要求の終わりに警告として示す。
    """
    decision = _deliver(payload, tool_name, tool_input, session_id)
    warnings = tuple(filter(None, map(_declaration_issue_notice, read_triggers.declaration_issues())))
    return dataclasses.replace(decision, notices=(*decision.notices, *warnings)) if warnings else decision


def _declaration_issue_notice(issue: str) -> str:
    """除いた行の警告。同じ本文は整形関数の記録により同じ会話で1回だけ届く。"""
    return _llm_notice(
        f"ユーザー側の読込表の1行を解釈できないため、この行の読込案内と読取記録を省いた: {issue}",
        tag=_WARN_TAG,
        removable_cause=True,
        escalate_on_repeat=False,
        fix=(
            "作業はそのまま続けよ。宣言元を編集する担当は、"
            "`agent-basics:writing-standards`の`references/agent-documents-basics-read-tables.md`「読込表」に合わせてこの行を直せ。"
        ),
    )


def _deliver(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> Decision:
    written = operation_skills.written_paths(tool_name, tool_input, str(payload.get("cwd", "")))
    if _work_record_only(written, payload, session_id):
        return Decision()
    _refresh_receipts(payload, tool_name, tool_input, session_id)
    decision = _deliver_documents(payload, tool_name, tool_input, session_id)
    if _acquiring(tool_name, tool_input, str(payload.get("cwd", ""))):
        # 取得操作の依存案内は次の関連操作へ保留する。所有する役割の読取保護は維持する。
        notice = _foreign_task_document_notice(payload, tool_name, tool_input, session_id)
        return dataclasses.replace(decision, notices=(*decision.notices, notice)) if notice else decision
    agent = agent_id.resolve_hook_agent_id(payload)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    updates = []
    has_receipts = any(
        key.startswith((document_reads.RECEIPT_PREFIX, document_reads.READ_TRIGGER_PREFIX)) and agent in actors
        for key, actors in records.items()
        if isinstance(actors, list)
    )
    current_targets = (
        _receipt_targets(payload, tool_name, tool_input, session_id, active_only=True, include_selected=False)
        if has_receipts
        else []
    )
    for target in dict.fromkeys(current_targets):
        if document_reads.acquisition_state(records, target, agent) != "updated":
            continue
        metadata = next(item for item in document_reads.receipt_candidates([target]) if item["heading"] == target.heading)
        key = "read-update:" + document_reads.receipt_key(metadata)
        if agent in records.get(key, []):
            continue
        text = _llm_notice(
            f"取得済み資料の更新あり: `{target}`。旧版の取得記録を保持している。",
            tag=_WARN_TAG,
            removable_cause=True,
            escalate_on_repeat=False,
            fix=acquisition_step(
                target, operation_skills.resolve_subject(payload), reference=True, cwd=str(payload.get("cwd", ""))
            ),
        )
        notice_module.stage_delivery(text, session_id, rules_context.OPERATION_SKILL_READY_KEY, key, agent)
        updates.append(text)
    decision = dataclasses.replace(decision, notices=(*decision.notices, *updates))
    if decision.block is not None:
        return decision
    candidate_notice = _condition_candidate_notice(payload, tool_name, tool_input, session_id)
    if candidate_notice:
        decision = dataclasses.replace(decision, notices=(*decision.notices, candidate_notice))
    notice = _foreign_task_document_notice(payload, tool_name, tool_input, session_id)
    return dataclasses.replace(decision, notices=(*decision.notices, notice)) if notice else decision


@_input_scope
def observe(payload: dict, tool_name: str, tool_input: dict, session_id: str) -> None:
    """本文受信と操作全体の成否を分け、成功時の副作用だけを後続へ渡す。"""
    _observe_content(payload, tool_name, tool_input, session_id)
    if payload.get("hook_event_name") in {"PostToolUseFailure", "PermissionDenied"}:
        return
    response = payload.get("tool_response")
    if isinstance(response, dict) and (
        response.get("exit_code", 0) != 0 or response.get("interrupted") or response.get("is_error") or response.get("isError")
    ):
        return
    cwd = payload.get("cwd", "")
    cwd = cwd if isinstance(cwd, str) else ""
    agent = agent_id.resolve_hook_agent_id(payload)
    result_operation = tool_name.startswith(
        ("mcp__agents_server__", "mcp__plugin_agent-toolkit_agents_server__")
    ) and tool_name.rsplit("__", 1)[-1] in {"wait", "send_message"}
    command = tool_input.get("command")
    if tool_name == "Bash" and isinstance(command, str):
        result_operation = any(
            segment.resolved and is_atk_command(segment.tokens) and segment.tokens[1:3] == ("agents", "wait")
            for segment in extract_execution_segments(command)
        )
    if result_operation and not tool_input.get("run_in_background") and not host_truncated(response):
        from agent_toolkit._hooks import result_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        documents = result_reads.observe(session_id, agent, response, deferred=is_codex_payload(payload))
        _select_result_documents(
            session_id, agent, documents, host="codex" if is_codex_payload(payload) else "claude", project=cwd
        )
    edited = document_reads.edited_document(tool_name, tool_input, response, cwd)
    if edited is not None:
        path, before = edited
        records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
        if agent in records.get(document_reads.read_trigger_key(path, before), []):
            _mark(session_id, path, agent, host="codex" if is_codex_payload(payload) else "claude", project=cwd)
        if agent in records.get(document_reads.skill_invocation_key(path, before), []):
            update_state(
                session_id,
                operation_skills.mark_ready(
                    document_reads.skill_invocation_key(path, document_reads.content_fingerprint(path)), agent
                ),
            )
    fetch_failed = isinstance(response, dict) and (
        bool(response.get("error")) or isinstance(response.get("code"), int) and not 200 <= response["code"] < 300
    )
    if (
        tool_name == "WebFetch"
        and isinstance(tool_input.get("url"), str)
        and response
        and not fetch_failed
        and not host_truncated(response)
    ):
        update_state(session_id, operation_skills.mark_ready("external-document:" + tool_input["url"], agent))
    for path in operation_skills.written_paths(tool_name, tool_input, cwd):
        if operation_skills.is_agent_doc_target_file(path):
            update_state(
                session_id, operation_skills.mark_ready("modified-agent-document:" + str(pathlib.Path(path).resolve()), agent)
            )
    text = document_reads.response_text(response)
    if (
        tool_name == "Bash"
        and isinstance(tool_input.get("command"), str)
        and any(
            segment.resolved
            and is_atk_command(segment.tokens)
            and segment.tokens[1:3] == ("run-script", "session-review-prepare")
            for segment in extract_execution_segments(tool_input["command"])
        )
        and text
    ):
        try:
            prepared = json.loads(text)
        except ValueError:
            prepared = None
        if prepared is not None:
            _review_reference(session_id, prepared, agent)
        else:
            saved = re.search(r"(?m)^保存先: (.+)$", text)
            if saved:
                path = pathlib.Path(saved[1])
                if not path.is_absolute() or not path.is_file():
                    raise ValueError(f"prepareの保存結果が実在しない: {path}")
                update_state(session_id, operation_skills.mark_ready("review-output:" + str(path), agent))
                _select_targets(session_id, [read_triggers.ReadTarget(path)], agent)
    paths = _read_paths(tool_name, tool_input, cwd)
    if tool_name == "Bash" and any(not path.is_file() for path in paths):
        return
    if tool_name != "Skill" and not paths:
        return
    rows = declarations(cwd, host="codex" if is_codex_payload(payload) else "claude")
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    rows = list(
        dict.fromkeys(
            [
                *rows,
                *_project_skill_rows(
                    records if isinstance(records, dict) else {},
                    agent,
                    tuple(paths),
                    known_sources={row.source for row in rows},
                    host="codex" if is_codex_payload(payload) else "claude",
                    project=cwd,
                ),
            ]
        )
    )
    chosen_targets = selected_targets(records if isinstance(records, dict) else {}, agent)
    targets = {target.path for target in chosen_targets}
    targets.update(path for path in paths if path.suffix == ".md" and _document_owner(path) is not None)
    if tool_name == "Skill":
        qualified = tool_input.get("skill")
        if not response:
            return
        targets.update(row.source for row in rows)
        targets.update(read_triggers.document_sources(plugin_resources.plugin_root(), cwd))
        targets.update(
            pathlib.Path(key.removeprefix("resolved-project-skill:"))
            for key, owners in records.items()
            if key.startswith("resolved-project-skill:") and agent in owners
        )
        resolved = resolve_skill_document(qualified, sorted(targets))
        if resolved is not None:
            update_state(
                session_id,
                operation_skills.mark_ready(
                    document_reads.skill_invocation_key(resolved, document_reads.content_fingerprint(resolved)), agent
                ),
            )
            _mark(session_id, resolved, agent, host="codex" if is_codex_payload(payload) else "claude", project=cwd)
        return
