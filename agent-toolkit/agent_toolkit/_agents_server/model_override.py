from __future__ import annotations

import json
import pathlib
import re
import socket
from collections.abc import Iterable, Mapping
from typing import Any

import yaml

from agent_toolkit._agents_server.launch_requests_materials import exec_review_wi_names
from agent_toolkit._agents_server.state import SessionResumeState, SessionState
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import lane_records
from agent_toolkit._atk.session_record_format import as_text
from agent_toolkit._atk.wi import frontmatter, layout, processing_owner, repo, user_comment, uwi_scan
from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES, WI_TYPE_AWI, WI_TYPE_UWI
from agent_toolkit._atk.wi.uwi_body import ANSWER_MARKER
from agent_toolkit._common import (
    codex_models,
    operation_approvals,
    session_launchers,
    session_record_paths,
    session_registry,
    task_documents,
    wi_layout,
)
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._common.runtime_inserted import is_runtime_generated, is_runtime_inserted_text, report_lines


def directive(scope: str, phase: str, model_type: str) -> str:
    return f"モデル指定: {scope} | {phase} | {model_type}"


def _last_input(records: Iterable[object], engine: str) -> tuple[int, str] | None:
    """最後の本人入力より前へさかのぼって古い指定を有効にしない。 pyfltr: allow-comment"""
    latest: tuple[int, str] | None = None
    for line, record in enumerate(records, 1):
        if not isinstance(record, dict):
            continue
        if engine == "codex":
            payload = record.get("payload")
            if record.get("type") == "session_meta" and isinstance(payload, dict) and isinstance(payload.get("source"), dict):
                # 子threadでは親の指示がuser_messageになる。 pyfltr: allow-comment
                return None
            if record.get("type") == "compacted":
                latest = None
            if record.get("type") != "event_msg" or not isinstance(payload, dict) or payload.get("type") != "user_message":
                continue
            text = payload.get("message")
        elif engine == "claude":
            if record.get("isCompactSummary") or record.get("subtype") == "compact_boundary":
                latest = None
            if record.get("isSidechain") or is_runtime_generated(record):
                continue
            item = record.get("attachment") if record.get("type") == "attachment" else record
            if not isinstance(item, dict) or not isinstance(item.get("origin"), dict) or item["origin"].get("kind") != "human":
                continue
            if record.get("type") == "attachment" and item.get("type") == "queued_command":
                text = item.get("prompt")
            elif record.get("type") == "user":
                message = record.get("message")
                text = as_text(message.get("content")) if isinstance(message, dict) else None
            else:
                continue
        else:
            continue
        if isinstance(text, str) and text.strip() and not is_runtime_inserted_text(text):
            latest = line, text.strip()
    return latest


def _ancestry(launcher: str | None, state_root: pathlib.Path | None) -> tuple[str, ...]:
    parents = []
    registry = session_registry.registry_directory(state_root)
    while launcher and session_launchers.valid_launcher(launcher) == launcher and launcher not in parents:
        parents.append(launcher)
        launcher = session_launchers.read_launcher(registry / f"{launcher}.json")
    return tuple(parents)


def _record_context(session: str, launcher: Mapping[str, Any]) -> tuple[str, pathlib.Path] | None:
    if session == launcher.get("launcher_session_id"):
        if launcher.get("status") != "resolved":
            return None
        return launcher["record_engine"], pathlib.Path(launcher["record_path"])
    record = session_record_paths.find_session_record(session)
    if record is None or len(record.paths) != 1:
        return None
    return record.engine, record.paths[0]


def _latest_human(context: tuple[str, pathlib.Path]) -> tuple[int, str] | None:
    engine, path = context
    with path.open(encoding="utf-8") as source:
        return _last_input((json.loads(line) if line.strip() else None for line in source), engine)


def _matches_record(
    evidence: str, expected: tuple[str, ...], launcher: Mapping[str, Any], ancestry: tuple[str, ...], *, repository_codex: bool
) -> bool:
    match = re.fullmatch(r"session:([0-9A-Za-z_-]+):([1-9][0-9]{0,8})", evidence)
    if match is None or match[1] not in ancestry:
        return False
    # 委譲先のCodexでは親の追送もuser_messageになる。 pyfltr: allow-comment
    session = match[1]
    context = _record_context(session, launcher)
    if context is None or context[0] == "codex" and session != ancestry[-1]:
        return False
    latest = _latest_human(context)
    return (
        latest is not None
        and latest[0] == int(match[2])
        and (latest[1] in expected or (repository_codex and _REPOSITORY_CODEX.fullmatch(latest[1]) is not None))
    )


_REPOSITORY_CODEX = re.compile(
    r"(?:あと[、 ]*)?" + r"`?repository-process`?\s*は\s*`?codex`?\s*(?:を\s*)?"
    r"(?:使え(?:って言ったでしょ)?|使ってください|使う)[。！!]?",
    re.IGNORECASE,
)


def _repository_codex_selection(model_type: str, phase: str, launch: Mapping[str, Any]) -> bool:
    if phase != "execute" or pathlib.Path(launch.get("subagent_md_path") or "").name != "repository-process.subagent.md":
        return False
    if launch["mode"] not in {"task", "handoff"}:
        return False
    try:
        requested = _atk_config.parse_stage_model_candidates(model_type)
        codex = [item for item in _atk_config.parse_unresolved_model_candidates("execute") if item[0] == "codex"]
    except ValueError:
        return False
    return bool(codex) and requested == codex


_WI_ROLES = {"execute": "実装担当", "review": "実行レビュー担当", "review_fast": "実行レビュー担当"}
_WI_TASK_PHASES = {"exec.subagent.md": {"execute"}, "exec-review.subagent.md": {"review", "review_fast"}}


def _legacy_wi_model(comment: str, phase: str) -> str | None:
    role = _WI_ROLES.get(phase)
    if role is None:
        return None
    values: list[str] = []
    for line in report_lines(comment):
        text = line.strip()
        if text.startswith("担当モデル: {"):
            try:
                named = yaml.compose(text, Loader=yaml.SafeLoader)
            except yaml.YAMLError:
                return None
            if not isinstance(named, yaml.MappingNode) or len(named.value) != 1:
                return None
            _, models = named.value[0]
            if not isinstance(models, yaml.MappingNode):
                return None
            values.extend(
                value.value
                for key, value in models.value
                if isinstance(key, yaml.ScalarNode) and key.value == role and isinstance(value, yaml.ScalarNode)
            )
        elif match := re.fullmatch(rf"(?:担当モデル[.])?{role}[:：]\s*(.+)", text):
            values.append(match[1].strip().removeprefix("`").removesuffix("`"))
        elif match := re.fullmatch(rf"{role}は\s*`([^`]+)`\s*を使って(?:ください)?。", text):
            values.append(match[1])
    return values[0] if values and all(value == values[0] for value in values) else None


def _assigned_wi(launch: Mapping[str, Any], filename: str, phase: str) -> bool:
    if launch["mode"] not in {"task", "handoff"}:
        return False
    params = launch.get("extra_params") or {}
    document = pathlib.Path(launch.get("subagent_md_path") or "").name
    if phase not in _WI_TASK_PHASES.get(document, ()):
        return False
    if document == "exec-review.subagent.md":
        bases = {value.strip() for value in re.split(r"[、,，､]", params.get("レビュー基準", ""))}
        if "CI記録" in bases:
            return False
        scope = {"計画": params.get("計画", "")} if "計画" in bases else {}
        if "WI" in bases and (record := params.get("WI記録")):
            scope["WI記録"] = record
        return exec_review_wi_names(scope) == {filename}
    if document != "exec.subagent.md" or params.get("担当種別") != "レーン担当":
        return False
    selection_file = pathlib.Path(params.get("選定結果の出力先ファイル") or "")
    lane = params.get("レーン識別子")
    if not selection_file.is_absolute() or not isinstance(lane, str) or not lane.strip():
        return False
    assigned = lane_records.read_selection({"selection_file": selection_file, "lane": lane})
    return assigned is not None and assigned["lane_wis"] == [filename]


def wi_task_context(session: SessionState | SessionResumeState, phase: str) -> dict[str, Any] | None:
    document = task_documents.launched_task_document(session.prompt)
    if document is None or phase not in _WI_TASK_PHASES.get(document.name, ()):
        return None
    filename = f"{session.label}.md"
    if wi_layout.DATED_NAME.fullmatch(filename) is None:
        return None
    # 旧版では成功したWI根拠だけが自由本文と区別できる起動時の記録になる。 pyfltr: allow-comment
    proven = f"モデル設定の上書き: 工程={phase}; 指定値={session.model_type}; 区分=user_instruction; 根拠=wi:{filename}。"
    if session.task_role != document.name and not (session.model_override_record or "").startswith(proven):
        return None
    try:
        params = task_documents.task_prompt_inputs(session.prompt)
    except ValueError:
        return None
    return {
        "mode": "handoff",
        "cwd": session.cwd,
        "label": session.label,
        "subagent_md_path": str(document),
        "extra_params": params,
    }


def wi_reply_matches(session: SessionState | SessionResumeState, phase: str) -> bool:
    launch = wi_task_context(session, phase)
    if launch is None:
        return False
    try:
        registry = session_registry.registry_directory()
        launcher = session.launcher_session_id if isinstance(session, SessionState) else None
        if launcher is None and session_launchers.valid_launcher(session.session_id):
            launcher = session_launchers.read_launcher(registry / f"{session.session_id}.json")
        ancestry = _ancestry(launcher, None)
        filename = f"{session.label}.md"
        if not ancestry or (record := _wi_record(filename, session.cwd)) is None:
            return False
        comment = _assigned_user_comment(record[0], record[1], filename, ancestry, launch=launch, phase=phase)
        if not comment or (requested := _legacy_wi_model(comment, phase)) is None:
            return False
        identity = (session.engine, session.model, session.effort)
        candidates = _atk_config.parse_stage_model_candidates(requested)
        if identity in candidates:
            return True
        aliases = [item for item in candidates if item[0] == session.engine and codex_models.requires_catalog(item)]
        if not aliases:
            return False
        catalog = codex_models.list_models()
        for item in aliases:
            try:
                if codex_models.resolve_candidate(item, catalog) == identity:
                    return True
            except codex_models.CatalogResolutionError:
                continue
    except OSError, ValueError, RuntimeError, yaml.YAMLError:
        return False
    return False


def _wi_record(filename: str, cwd: str) -> tuple[str, dict[str, Any], str] | None:
    if wi_layout.DATED_NAME.fullmatch(filename) is None:
        return None
    root = uwi_scan.private_notes_root()
    path = layout.unique_dated_entry_path(root, filename) if root is not None else None
    if path is None:
        return None
    raw = frontmatter.decode_entry_text(path.read_bytes())
    parsed = frontmatter.parse_frontmatter(raw)
    if parsed is None:
        return None
    metadata, body = parsed
    if metadata.get("state") not in WI_PROCESSABLE_STATES or metadata.get("target_repo") != repo.resolve_repo_id_or_raise(
        None, cwd=pathlib.Path(cwd)
    ):
        return None
    return raw, metadata, body


def _assigned_user_comment(
    raw: str, metadata: Mapping[str, Any], filename: str, ancestry: tuple[str, ...], *, launch: Mapping[str, Any], phase: str
) -> str | None:
    owner = processing_owner.from_text(raw)
    if (
        metadata.get("type") == WI_TYPE_AWI
        and pathlib.Path(filename).stem == launch.get("label")
        and owner is not None
        and owner.host == socket.gethostname()
        and owner.session in ancestry
        and _assigned_wi(launch, filename, phase)
    ):
        return user_comment.extract_user_comment(raw)
    return None


def _matches_wi(
    evidence: str,
    expected: str,
    ancestry: tuple[str, ...],
    *,
    launch: Mapping[str, Any],
    phase: str,
    model_type: str,
) -> bool:
    kind, _, filename = evidence.partition(":")
    if kind not in {"wi", "answer"} or (record := _wi_record(filename, launch["cwd"])) is None:
        return False
    raw, metadata, body = record
    if kind == "wi":
        comment = _assigned_user_comment(raw, metadata, filename, ancestry, launch=launch, phase=phase)
        return bool(comment and (comment == expected or _legacy_wi_model(comment, phase) == model_type))
    question, marker, answer = body.partition(ANSWER_MARKER)
    return (
        metadata.get("type") == WI_TYPE_UWI
        and not uwi_scan.is_report(raw)
        and metadata.get("submitter_session") in ancestry
        and marker != ""
        and expected in question.splitlines()
        and answer.strip() == expected
    )


def require(
    evidence: str,
    model_type: str,
    phase: str,
    *,
    launch: Mapping[str, Any],
    launcher: Mapping[str, Any],
    state_root: pathlib.Path | None,
    wi_task: Mapping[str, Any] | None = None,
) -> None:
    scope = "start-" + operation_approvals.fingerprint(launch)
    expected = directive(scope, phase, model_type)
    raw_prompt = launch.get("prompt")
    direct = ()
    if (
        launch["mode"] in {"delegate", "explore", "write"}
        and not launch.get("whole_contract_path")
        and not launch.get("startup_profile")
        and isinstance(raw_prompt, str)
        and re.fullmatch(r"[^「」\r\n]+", raw_prompt)
    ):
        direct = (f"{launch['cwd']}で{launch['mode']}の「{raw_prompt}」は{model_type}で。",)
    ancestry = _ancestry(launcher.get("launcher_session_id"), state_root)
    try:
        repository_codex = _repository_codex_selection(model_type, phase, launch)
        forms = (expected, *direct, directive(scope, phase, "codex")) if repository_codex else (expected, *direct)
        if ancestry and (
            _matches_record(evidence, forms, launcher, ancestry, repository_codex=repository_codex)
            or _matches_wi(
                evidence,
                expected,
                ancestry,
                launch=wi_task if wi_task is not None and evidence.startswith("wi:") else launch,
                phase=phase,
                model_type=model_type,
            )
            or (
                repository_codex
                and _matches_wi(
                    evidence, directive(scope, phase, "codex"), ancestry, launch=launch, phase=phase, model_type="codex"
                )
            )
        ):
            return
    except OSError, ValueError, yaml.YAMLError:
        pass
    raise ActionableError(
        "本人の原文・回答を取得できないか、今回の対象・工程・全候補に一致しない。委譲先は起動していない。",
        next_action=(
            "役割起動ではmodel_typeを省き、自由本文では目的に合う工程名を指定する。"
            "同じWIの本人コメントに担当モデルがあればmodel_override_evidence=wi:<WI名.md>を渡す。"
            "今回の操作だけの上書きにはsession:<会話ID>:<行番号>かanswer:<UWI名>と、対象に一致する本人指定を使う: "
            f"{expected}"
        ),
    )
