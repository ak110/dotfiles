"""主体別に宣言資料を選択し、取得済み状態を照会する。"""

# 公開入口の差し替えを呼出し時に参照し、モジュール初期化中の逆向きimportを避ける。
# pylintの依存グラフは関数内importも含むため、その参照行だけ循環検査から除く。

from __future__ import annotations

import json
import pathlib
from collections.abc import Callable
from typing import TYPE_CHECKING

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks import (
    document_reads,
    plugin_resources,
    rules_context,
    work_contract,
)
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks.host import is_codex_payload
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._hooks.pretooluse.read_delivery_paths import _document_owner, _read_paths

if TYPE_CHECKING:
    pass  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import


def _project_skill_rows(
    records: dict,
    agent: str,
    paths: tuple[pathlib.Path, ...] = (),
    *,
    known_sources: set[pathlib.Path] | None = None,
    host: str = "claude",
    project: str = "",
) -> list[read_triggers.ReadTrigger]:
    """選択した資料と、同じ主体が取得したplugin資料の宣言を使う。"""
    rows: list[read_triggers.ReadTrigger] = []
    selected = set(paths)
    origins: dict[pathlib.Path, list[dict]] = {}
    for key, owners in records.items():
        if agent not in owners:
            continue
        if key.startswith("resolved-project-skill:"):
            selected.add(pathlib.Path(key.removeprefix("resolved-project-skill:")))
        elif key.startswith("selected-read-target:"):
            # 未読の起動資料も、選択元の現行宣言を次要求で再解決する。
            # 本文の参照先変更はこの後のparse_tablesへ反映し、保存時のtargetsを復活させない。
            recorded_origins = json.loads(key.removeprefix("selected-read-target:")).get("declarations", [])
            if isinstance(recorded_origins, list):
                for origin in recorded_origins:
                    source = origin.get("source") if isinstance(origin, dict) else None
                    if isinstance(source, str) and pathlib.Path(source).is_absolute():
                        origins.setdefault(pathlib.Path(source).resolve(), []).append(origin)
        elif key.startswith(document_reads.READ_TRIGGER_PREFIX):
            value, separator, fingerprint = key.removeprefix(document_reads.READ_TRIGGER_PREFIX).rpartition("#sha256:")
            if separator and len(fingerprint) == 64:
                path = pathlib.Path(value)
                if (
                    path.is_file()
                    and _document_owner(path) is not None
                    and document_reads.content_fingerprint(path) == fingerprint
                ):
                    selected.add(path)
    selected = {path.resolve() for path in selected}
    for path in sorted(selected | origins.keys()):
        if path.suffix != ".md" or not path.is_file() or path in (known_sources or set()):
            continue
        explicit = "resolved-project-skill:" + str(path)
        if _document_owner(path) is not None or agent in records.get(explicit, []):
            # 未読の宣言元は選択済みの行だけを復元し、別工程の行を有効にしない。
            rows.extend(
                row
                for row in read_triggers.parse_tables(
                    path, plugin_resources.plugin_root(), host=host, project=project, defer_material=True
                )
                if path in selected or _declaration_identity(row) in origins.get(path, [])
            )
    return rows


def document_key(path: pathlib.Path, fingerprint: str = "") -> str:
    return document_reads.read_trigger_key(path, fingerprint or document_reads.content_fingerprint(path))


def read_guidance_key(target: read_triggers.ReadTarget, *, invocation: bool) -> str:
    """取得案内を送った記録のキー。保存・重複判定・未解消判定の全てがこの関数を使う。"""
    if read_triggers.is_normative_document(target.path):
        target = read_triggers.ReadTarget(target.path)
    key = (
        document_reads.skill_invocation_key(target.path, document_reads.content_fingerprint(target.path))
        if invocation
        else document_key(target.path)
    )
    return "read-guidance:" + key + "#heading:" + target.heading


def dynamic_targets(
    row: read_triggers.ReadTrigger,
    payload: dict,
    tool_name: str,
    tool_input: dict,
    records: dict,
    agent: str,
    session_id: str = "",
) -> list[read_triggers.ReadTarget]:
    """宣言された動的資料を、実行時の実在入力だけから解決する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if not row.selector:
        return []
    if row.selector == "project-skill":
        targets: list[read_triggers.ReadTarget] = []
        for path in _read_paths(tool_name, tool_input, str(payload.get("cwd", ""))):
            if path.name != "SKILL.md" or not path.is_file():
                continue
            source = read_triggers.resolve_read_source(
                path,
                plugin_resources.plugin_root(),
                "codex" if is_codex_payload(payload) else "claude",
                project=payload.get("cwd"),
            )
            declared = read_triggers.parse_tables(
                source,
                plugin_resources.plugin_root(),
                host="codex" if is_codex_payload(payload) else "claude",
                project=payload.get("cwd"),
                defer_material=True,
            )
            if session_id:
                read_delivery.update_state(
                    session_id, operation_skills.mark_ready("resolved-project-skill:" + str(source), agent)
                )
            targets.extend(target for item in declared if item.kind == "起動時" for target in item.targets)
            targets.append(read_triggers.ReadTarget(source))
        return targets
    if row.selector.startswith("input:"):
        targets = []
        for key, agents in records.items():
            if key.startswith("startup-input-target:") and agent in agents:
                item = json.loads(key.removeprefix("startup-input-target:"))
                if row.selector in item.get("selectors", [item.get("selector", "")]):
                    targets.append(read_triggers.ReadTarget(pathlib.Path(item["path"]), item["heading"]))
        source = read_triggers.INPUT_SELECTOR_RESOLUTION_SOURCES.get(row.selector)
        if not targets and source == "main-work-contract" and agent == "main":
            path = work_contract.record_path(session_id)
            if path is not None and path.is_file():
                targets.append(read_triggers.ReadTarget(path))
            elif path is not None:
                raise ValueError(
                    f"{row.source}:{row.line}: {row.selector}の作業契約が無い: {path}。"
                    "agent-toolkit/share/work-contract.md「作成と所有」に従って作成せよ。"
                )
        if not targets:
            raise ValueError(f"{row.source}:{row.line}: {row.selector}の起動時配送入力が無い")
        return targets
    if row.selector == "same-document":
        return [read_triggers.ReadTarget(row.source)]
    if row.selector == "role-contract":
        role = tool_input.get("subagent_md_path")
        if not isinstance(role, str):
            return []
        return list(
            read_triggers.parent_contract_targets(
                role,
                plugin_resources.plugin_root(),
                host="codex" if is_codex_payload(payload) else "claude",
                project=payload.get("cwd"),
            )
        )
    if row.selector == "review-reference":
        outputs = [
            pathlib.Path(key.removeprefix("review-output:"))
            for key, agents in records.items()
            if key.startswith("review-output:") and agent in agents
        ]
        unread = [path for path in outputs if agent not in records.get("review-output-parsed:" + document_key(path), [])]
        if unread:
            if any(path in _read_paths(tool_name, tool_input, str(payload.get("cwd", ""))) for path in unread):
                return []
            raise ValueError(f"prepareの保存JSONを末尾まで取得せよ: {unread}")
        paths = [
            key.removeprefix("review-reference:")
            for key, agents in records.items()
            if key.startswith("review-reference:") and agent in agents
        ]
        return [read_triggers.ReadTarget(pathlib.Path(path)) for path in paths if pathlib.Path(path).is_file()]
    if row.selector == "artifact-standards":
        cwd = payload.get("cwd", "")
        targets = []
        host = "codex" if is_codex_payload(payload) else "claude"
        provider = document_reads.document_plugin(str(row.source))
        selection = read_triggers.resolve_document(
            f"{provider}:writing-standards", row.source, plugin_resources.plugin_root(), host, project=cwd
        )
        if selection is not None and selection.is_file():
            targets.append(read_triggers.ReadTarget(selection))
        for candidate in read_delivery.declarations(cwd, host=host):
            if candidate.kind == "操作" and any(
                operation.startswith(("path:", "write:", "dependency:"))
                and read_delivery.matches(operation, tool_name, tool_input, cwd)
                for operation in candidate.operations
            ):
                targets.extend(candidate.targets)
        return list(dict.fromkeys(targets))
    if row.selector == "external-pytilpack-index":
        if is_codex_payload(payload):
            # CodexにはWebFetchの観測契約がない。取得手段は本文の条件に従って選ぶ。
            return []
        index_ready = agent in records.get("external-document:https://ak110.github.io/pytilpack/llms.txt", [])
        pages = [
            key
            for key, agents in records.items()
            if key.startswith("external-document:https://ak110.github.io/pytilpack/")
            and not key.endswith(("/llms.txt", "/llms-full.txt"))
            and agent in agents
        ]
        if index_ready and pages:
            return []
        raise ValueError(f"{row.source}:{row.line}: 外部資料の索引と対象モジュールをWebFetchで取得せよ。ローカル配送の対象外")
    raise ValueError(f"未登録の資料選択子: {row.selector}")


def _mark(session_id: str, path: pathlib.Path, agent: str, fingerprint: str = "", *, record_scopes: bool = True) -> None:
    from agent_toolkit._hooks import result_reads  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    identifier = document_key(path, fingerprint)
    read_delivery.update_state(session_id, operation_skills.mark_ready(identifier, agent))
    if record_scopes and (not fingerprint or fingerprint == document_reads.content_fingerprint(path)):
        for metadata in document_reads.receipt_candidates([read_triggers.ReadTarget(path)]):
            read_delivery.update_state(session_id, operation_skills.mark_ready(document_reads.receipt_key(metadata), agent))
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    # 案内済みの記録は保存時と同じ関数で照会し、案内した形（資料取得・スキル起動）ごとの取得状態で判定する。
    outstanding = [
        target
        for target in selected_targets(records, agent)
        for invocation in (False, True)
        if agent in records.get(read_guidance_key(target, invocation=invocation), [])
        and not target_ready(records, target, agent, invocation=invocation)
    ]
    if not outstanding:
        notice_module.resolve_notice(session_id, "pretooluse", "_deliver_documents", subject=agent)
    if agent in records.get("review-output:" + str(path), []):
        raw = path.read_bytes()
        if fingerprint and document_reads.bytes_fingerprint(raw) != fingerprint:
            return
        _review_reference(session_id, json.loads(raw), agent)
        read_delivery.update_state(
            session_id, operation_skills.mark_ready("review-output-parsed:" + document_key(path, fingerprint), agent)
        )

        def parsed(state: dict) -> dict:
            rows = state.get(rules_context.OPERATION_SKILL_READY_KEY, {})
            key = "review-output:" + str(path)
            if key in rows:
                rows[key] = [owner for owner in rows[key] if owner != agent]
                if not rows[key]:
                    del rows[key]
            return state

        read_delivery.update_state(session_id, parsed)
    _select_result_documents(session_id, agent, result_reads.observe_file(session_id, agent, path))


def _select_result_documents(session_id: str, agent: str, documents: list[str]) -> None:
    targets = []
    root = plugin_resources.plugin_root()
    for document in documents:
        name, _separator, heading = document.partition("#")
        path = read_triggers.resolve_document(name, root / "share/read-trigger-contract.md", root)
        if path is None or not path.is_file():
            raise ValueError(f"結果の判断資料を解決できない: {document}")
        targets.append(read_triggers.ReadTarget(path, heading))
    if targets:
        _select_targets(session_id, targets, agent)


def _review_reference(session_id: str, prepared: object, agent: str) -> None:
    """prepareの成功JSONが示す実在する資料だけを登録する。"""
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    if not isinstance(prepared, dict):
        raise ValueError("session-review-prepareの結果がJSON objectでない")
    value = prepared.get("reference_document")
    if value is None or value == "":
        return
    path = pathlib.Path(value) if isinstance(value, str) else None
    if path is None or not path.is_absolute() or not path.is_file():
        raise ValueError(f"reference_documentの実体を解決できない: {value}")
    read_delivery.update_state(session_id, operation_skills.mark_ready("review-reference:" + str(path), agent))


def target_ready(records: dict, target: read_triggers.ReadTarget, agent: str, *, invocation: bool = False) -> bool:
    if not invocation:
        return document_reads.acquisition_state(records, target, agent) != "missing"
    raw = target.path.read_bytes()
    if target.heading:
        try:
            target.body(raw.decode("utf-8"))
        except ValueError:
            return False
    key = document_reads.skill_invocation_key if invocation else document_reads.read_trigger_key
    owners = records.get(key(target.path, document_reads.bytes_fingerprint(raw)), [])
    return isinstance(owners, list) and agent in owners


def _declaration_identity(row: read_triggers.ReadTrigger) -> dict:
    """行移動や参照見出しの改名をまたいで、選択元の宣言を識別する。"""
    identity = {"source": str(row.source), "operations": list(row.operations), "kind": row.kind, "selector": row.selector}
    if row.derived_read:
        # 導出行は配送操作を持たないため、同じ宣言元の行を資料列で区別する。
        identity["material"] = row.material
    return identity


def selected_targets(
    records: dict, agent: str, *, rows: list[read_triggers.ReadTrigger] | None = None
) -> list[read_triggers.ReadTarget]:
    targets: list[read_triggers.ReadTarget] = []
    for key, agents in records.items():
        if key.startswith("selected-read-target:") and agent in agents:
            raw = json.loads(key.removeprefix("selected-read-target:"))
            if rows is not None and raw.get("declarations"):
                # 保存時の見出しを再利用せず、現在の宣言が示す対象を解決する。
                targets.extend(
                    target for row in rows if _declaration_identity(row) in raw["declarations"] for target in row.targets
                )
                continue
            path = pathlib.Path(raw["path"])
            if not path.is_file():
                continue
            target = read_triggers.ReadTarget(path, "" if read_triggers.is_normative_document(path) else raw["heading"])
            if target.heading:
                try:
                    target.body()
                except ValueError:
                    # 選択後に資料から消えた見出しは全文を読んでも取得済みにならないため、
                    # 保存時の`_select_targets`と同じく選択から外す。
                    continue
            targets.append(target)
    return list(dict.fromkeys(targets))


def _unmark(identifier: str, agent: str) -> Callable[[dict], dict | None]:
    """記録キーから主体を除く状態更新を返す。"""

    def mutate(state: dict) -> dict | None:
        recorded = state.get(rules_context.OPERATION_SKILL_READY_KEY)
        if not isinstance(recorded, dict):
            return None
        agents = recorded.get(identifier)
        if not isinstance(agents, list) or agent not in agents:
            return None
        state[rules_context.OPERATION_SKILL_READY_KEY] = {
            **recorded,
            identifier: [item for item in agents if item != agent],
        }
        return state

    return mutate


def _select_targets(
    session_id: str,
    targets: list[read_triggers.ReadTarget],
    agent: str,
    *,
    rows: list[read_triggers.ReadTrigger] | None = None,
) -> None:
    from agent_toolkit._hooks.pretooluse import read_delivery  # noqa: PLC0415  # pylint: disable=import-outside-toplevel,cyclic-import

    current = {target.path for target in targets}
    records = read_delivery.read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    for key, owners in records.items():
        if key.startswith("selected-read-target:") and agent in owners:
            old = json.loads(key.removeprefix("selected-read-target:"))
            path = pathlib.Path(old["path"])
            if path in current:
                read_delivery.update_state(session_id, _unmark(key, agent))
    for target in targets:
        origins = [_declaration_identity(row) for row in rows or () if target in row.targets]
        if read_triggers.is_normative_document(target.path):
            target = read_triggers.ReadTarget(target.path)
        if target.heading:
            try:
                target.body()
            except ValueError:
                # 不正な宣言を保持すると、宣言を復旧した後にも旧見出しが残る。
                continue
        key = document_reads.selected_target_key(target, declarations=origins)
        read_delivery.update_state(session_id, operation_skills.mark_ready(key, agent))
