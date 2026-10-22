"""Antigravityの6イベントを共通hookへ接続し、ホストの応答形式へ変換する。

protojsonのcamelCaseとJSON文字列のツール引数は受信時に正規化する。
SessionStartとツールイベントの通知は排他更新付きセッション状態へ保存し、
InvocationでephemeralMessageとして配送する。常時規範はプラグインが配送する。
"""

from __future__ import annotations

import contextlib
import dataclasses
import importlib
import io
import json
import pathlib
import re
import sys
from typing import Any

from agent_toolkit._common import agy_records, message_format, session_activity, session_state

EVENTS = frozenset({"SessionStart", "PreInvocation", "PreToolUse", "PostToolUse", "PostInvocation", "Stop"})
PENDING_CONTEXTS = "antigravity_pending_contexts"
LAST_USER_STEP = "antigravity_last_user_step"
STOP_CONTINUING = "antigravity_stop_continuing"
NATIVE_SUBAGENTS = "antigravity_subagent_ids"


def _decoded(value: Any) -> Any:
    """protoのmap<string,string>でJSON符号化された値を復元する。"""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _native_launch_result(value: object) -> object:
    """実機のinvoke_subagent応答が返す会話IDを、既存の所有記録へ渡す。"""
    original = value
    if isinstance(value, str) and "Created the following subagent:" in value:
        tail = value.split("Created the following subagent:", 1)[1].lstrip()
        try:
            value, _ = json.JSONDecoder().raw_decode(tail)
        except ValueError:
            return original
    if isinstance(value, dict) and isinstance(value.get("conversationId"), str):
        return {**value, "status": "async_launched", "agentId": value["conversationId"]}
    return original


def _tool_input(name: str, raw_args: object) -> tuple[str, dict[str, Any]]:
    """hookの判定処理が読む名前と引数へ射影し、未知の入力はそのまま保持する。"""
    canonical_name, normalized = agy_records.normalize_tool_call(name, raw_args)
    if name == "read_file" and isinstance(normalized.get("EndLine"), int):
        normalized["limit"] = normalized["EndLine"] - normalized.get("offset", 1) + 1
    if name in {"manage_task", "manage_subagents"}:
        action = normalized.get("Action", normalized.get("action"))
        if isinstance(action, str) and action.lower() in {"stop", "cancel", "terminate", "kill"}:
            for key in ("TaskId", "TaskID", "SubagentId"):
                if key in normalized:
                    normalized["task_id"] = normalized[key]
            if "SubagentId" in normalized:
                normalized["agent_id"] = normalized["SubagentId"]
            return "TaskStop", normalized
    return canonical_name, normalized


def normalize_payload(payload: dict[str, Any], event: str) -> dict[str, Any]:
    """Antigravity入力を共有hookの入力へ変換する。元のpayloadは変更しない。"""
    workspace = payload.get("workspacePaths")
    cwd = next((value for value in workspace if isinstance(value, str)), "") if isinstance(workspace, list) else ""
    call = payload.get("toolCall")
    call = call if isinstance(call, dict) else {}
    name, args = _tool_input(str(call.get("name", "")), call.get("args"))
    result = _decoded(call.get("result", payload.get("toolResult")))
    if call.get("name") == "invoke_subagent":
        result = _native_launch_result(result)
    if name == "AskUserQuestion":
        result = agy_records.question_result(args.get("questions"), result) or result
    if isinstance(result, dict):
        result = dict(result)
        if "taskId" in result:
            result["backgroundTaskId"] = result["taskId"]
        if "subagentId" in result:
            result["agentId"] = result["subagentId"]
    normalized = {
        "runtime": "agy",
        "session_id": payload.get("conversationId", ""),
        "cwd": cwd,
        "transcript_path": payload.get("transcriptPath", ""),
        "hook_event_name": event,
        "tool_name": name,
        "tool_input": args,
        "tool_response": result,
        "model": payload.get("modelName", ""),
        "effort": payload.get("effort", ""),
    }
    if payload.get("error"):
        normalized["error"] = payload["error"]
    if event == "SessionStart":
        normalized["source"] = payload.get("source", "startup")
    return normalized


@dataclasses.dataclass(frozen=True)
class _Response:
    code: int
    body: dict[str, Any]
    diagnostic: str

    def contexts(self) -> list[str]:
        specific = self.body.get("hookSpecificOutput")
        context = specific.get("additionalContext") if isinstance(specific, dict) else None
        return [context] if isinstance(context, str) and context else []


def _invoke(module_name: str, payload: dict[str, Any]) -> _Response:
    """共有handlerの単一JSONと診断を捕捉し、ホスト形式へ変換する。"""
    module = importlib.import_module(f"agent_toolkit._hooks.{module_name}")
    output, errors = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
        text = json.dumps(payload, ensure_ascii=False)
        code = module.main(text, host="agy") if module_name == "rules_context" else module.main(text)
    diagnostic = errors.getvalue()
    if diagnostic and code != 2:
        sys.stderr.write(diagnostic)
    raw = output.getvalue().strip()
    body = json.loads(raw) if raw else {}
    return _Response(code, body if isinstance(body, dict) else {}, diagnostic)


def _enqueue(session_id: str, contexts: list[str]) -> None:
    if not contexts:
        return

    def append(state: dict) -> dict:
        prior = state.get(PENDING_CONTEXTS)
        pending = [text for text in prior if isinstance(text, str)] if isinstance(prior, list) else []
        state[PENDING_CONTEXTS] = list(dict.fromkeys([*pending, *contexts]))
        return state

    if not session_state.update_state(session_id, append):
        raise OSError("Antigravityの遅延通知を保存できない")


def _consume(session_id: str) -> list[str]:
    contexts: list[str] = []

    def take(state: dict) -> dict | None:
        pending = state.pop(PENDING_CONTEXTS, None)
        if pending is None:
            return None
        if isinstance(pending, list):
            contexts.extend(text for text in pending if isinstance(text, str))
        return state

    if session_state.read_state(session_id).get(PENDING_CONTEXTS) is not None and not session_state.update_state(
        session_id, take
    ):
        raise OSError("Antigravityの遅延通知を取り出せない")
    return contexts


def _latest_steps(path: object) -> tuple[dict[str, Any] | None, str]:
    """切り詰めのない原本を行ごとに読み、直近の入力と応答だけを保持する。"""
    latest_user = None
    last_response = ""
    if not isinstance(path, str) or not path:
        return None, last_response
    record = pathlib.Path(path)
    if record.name == "transcript.jsonl" and (full := record.with_name("transcript_full.jsonl")).is_file():
        record = full
    try:
        with record.open(encoding="utf-8") as stream:
            for line in stream:
                try:
                    step = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(step, dict):
                    continue
                kind = str(step.get("type", "")).removeprefix("CORTEX_STEP_TYPE_")
                if kind == "USER_INPUT":
                    latest_user = step
                    last_response = ""
                elif kind == "PLANNER_RESPONSE" and isinstance(step.get("content"), str):
                    last_response = step["content"]
    except OSError as error:
        print(f"Antigravityの原本を読めない: {error}", file=sys.stderr)
    return latest_user, last_response


def _user_prompt(step: dict[str, Any]) -> str:
    content = step.get("content")
    if not isinstance(content, str):
        return ""
    return agy_records.user_text(content)


def _claim_user_step(session_id: str, step: dict[str, Any]) -> bool:
    index = step.get("step_index", step.get("stepIndex"))
    if not isinstance(index, (int, str)) or isinstance(index, bool):
        return False
    claimed = False

    def claim(state: dict) -> dict | None:
        nonlocal claimed
        if state.get(LAST_USER_STEP) == index:
            return None
        claimed = True
        state[LAST_USER_STEP] = index
        state[STOP_CONTINUING] = False
        return state

    return session_state.update_state(session_id, claim) and claimed


def _native_subagent_context(payload: dict[str, Any]) -> dict[str, Any] | None:
    """SubagentStartの代替として、呼び出し先のPromptへ委譲先規範を配送する。"""
    call = payload.get("toolCall")
    if not isinstance(call, dict) or call.get("name") != "invoke_subagent":
        return None
    raw = call.get("args")
    raw = _decoded(raw)
    if not isinstance(raw, dict):
        return None
    key = "Prompt" if "Prompt" in raw else "prompt"
    prompt = _decoded(raw.get(key))
    if not isinstance(prompt, str):
        return None
    module = importlib.import_module("agent_toolkit._hooks.rules_context")
    content = module.compose_subagent_start(host="agy")
    rewritten = f"{prompt}\n\n{content}"
    # protojsonのmap<string,string>と展開済みのobjectの双方で入力の符号化を保つ。
    value = json.dumps(rewritten, ensure_ascii=False) if _decoded(raw[key]) != raw[key] else rewritten
    return {key: value}


def _injections(contexts: list[str]) -> dict[str, Any]:
    # agy 1.4.4の公開hook契約のephemeralMessageを使う。
    return {"injectSteps": [{"ephemeralMessage": text} for text in contexts]} if contexts else {}


def _native_children(payload: dict[str, Any], normalized: dict[str, Any]) -> None:
    """親の起動応答と全件のactive一覧を使い、ネイティブ子の所有と終端を更新する。"""
    call = payload.get("toolCall")
    if not isinstance(call, dict) or payload.get("error"):
        return
    session_id = normalized["session_id"]
    result = normalized["tool_response"]
    launched = result.get("agentId") if isinstance(result, dict) else None
    active: set[str] | None = None
    if call.get("name") == "manage_subagents" and isinstance(result, str):
        match = re.search(r"You have (\d+) active subagent\(s\):\s*", result)
        if match is not None:
            try:
                listed, _ = json.JSONDecoder().raw_decode(result[match.end() :])
            except ValueError:
                listed = None
            if (
                isinstance(listed, list)
                and len(listed) == int(match[1])
                and all(isinstance(item, dict) and isinstance(item.get("conversationId"), str) for item in listed)
            ):
                active = {item["conversationId"] for item in listed}

    def update(state: dict) -> dict:
        children = state.get(NATIVE_SUBAGENTS, [])
        children = set(children) if isinstance(children, list) else set()
        if call.get("name") == "invoke_subagent" and isinstance(launched, str):
            children.add(launched)
        if active is not None:
            children.intersection_update(active)
        state[NATIVE_SUBAGENTS] = sorted(children)
        return state

    session_state.update_state(session_id, update)


def _record_read(payload: dict[str, Any]) -> list[str]:
    """read_fileが返した本文を確かめ、成功したスキルの読取を共通観測へ渡す。"""
    documents = importlib.import_module("agent_toolkit._hooks.document_reads")
    operations = importlib.import_module("agent_toolkit._hooks.pretooluse.operation_skills")
    args = payload["tool_input"]
    path = args.get("file_path")
    document = documents.skill_document(path) if isinstance(path, str) else None
    text = documents.response_text(payload.get("tool_response"))
    if document is None or not documents.body_returned(*document, text):
        return []
    # 行番号や結果の説明を除いた本文が、読取ファイルの全文を含むか確かめる。
    try:
        expected = pathlib.Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return []
    returned = re.sub(r"(?m)^\s*\d+\s*[:│→]\s?", "", text or "")
    full = bool(expected) and expected in returned
    read = documents.DocumentRead(*document, partial=not full)
    operations.record_document_read(payload["session_id"], read, "main")
    if read.relative_path != "SKILL.md" or not full:
        return []
    observed = {**payload, "tool_name": "Skill", "tool_input": {"skill": f"agent-toolkit:{read.skill}"}}
    return _invoke("posttooluse", observed).contexts()


def _dispatch(payload: dict[str, Any], event: str) -> dict[str, Any]:
    normalized = normalize_payload(payload, event)
    session_id = normalized["session_id"]
    if not isinstance(session_id, str) or re.fullmatch(r"[A-Za-z0-9_-]+", session_id) is None:
        raise ValueError("conversationIdは英数字・ハイフン・アンダースコアで指定する")
    session_state.update_state(
        session_id,
        lambda state: {
            **state,
            "agy_cwd": normalized["cwd"],
            "agy_transcript_path": normalized["transcript_path"],
        },
    )
    if event == "SessionStart":
        session_state.sweep_stale_states(keep_session_id=session_id)
        response = _invoke("rules_context", normalized)
        identity = message_format.auto_message(
            f"この会話のAntigravityセッションID: {session_id}\nこの会話のtranscript: {normalized['transcript_path']}",
            source="antigravity",
            kind="notice",
        )
        _enqueue(session_id, [*response.contexts(), identity])
        session_activity.record_activity(normalized)
        return {}
    if event in {"PreInvocation", "PostInvocation"}:
        if event == "PreInvocation":
            step, _ = _latest_steps(normalized["transcript_path"])
            if step is not None and _claim_user_step(session_id, step):
                normalized.update(hook_event_name="UserPromptSubmit", prompt=_user_prompt(step))
                source = step.get("source")
                normalized["source"] = "user" if source in {None, "USER_EXPLICIT"} else str(source)
                _enqueue(session_id, _invoke("user_prompt_submit", normalized).contexts())
                session_activity.record_activity(normalized)
        return _injections(_consume(session_id))
    if event == "PreToolUse":
        response = _invoke("pretooluse", normalized)
        session_activity.record_activity(normalized)
        if response.code == 2 or response.body.get("decision") in {"block", "deny"}:
            return {
                "decision": "deny",
                "reason": response.diagnostic.strip() or str(response.body.get("reason", "規範違反を修正する")),
            }
        _enqueue(session_id, response.contexts())
        overwrite = _native_subagent_context(payload)
        # 公開契約でdecisionは必須。省略した空objectは実ホストで理由のないdenyになる。
        return {"decision": "allow", "overwrite": overwrite} if overwrite else {"decision": "allow"}
    if event == "PostToolUse":
        if payload.get("error"):
            normalized["hook_event_name"] = "PostToolUseFailure"
        response = _invoke("posttooluse", normalized)
        _native_children(payload, normalized)
        contexts = response.contexts()
        if not payload.get("error") and normalized["tool_name"] == "Read":
            contexts.extend(_record_read(normalized))
        _enqueue(session_id, contexts)
        session_activity.record_activity(normalized)
        return {}
    if payload.get("terminationReason") in {"USER_CANCELED", "ERROR", "MAX_TOKEN_BUDGET_EXCEEDED"}:
        session_state.update_state(session_id, lambda state: {**state, STOP_CONTINUING: False})
        session_activity.record_activity(normalized)
        return {"decision": "allow"}
    _, assistant = _latest_steps(normalized["transcript_path"])
    normalized["last_assistant_message"] = assistant
    state = session_state.read_state(session_id)
    normalized["stop_hook_active"] = state.get(STOP_CONTINUING) is True
    children = state.get(NATIVE_SUBAGENTS, [])
    if isinstance(children, list) and children:
        if payload.get("fullyIdle") is True:
            session_state.update_state(session_id, lambda current: {**current, NATIVE_SUBAGENTS: []})
        else:
            normalized["background_tasks"] = [{"id": child, "type": "subagent", "status": "running"} for child in children]
    response = _invoke("stop", normalized)
    contexts = [*_consume(session_id), *response.contexts()]
    reason = response.body.get("reason")
    if response.body.get("decision") == "block" and isinstance(reason, str):
        contexts.insert(0, reason)
    continuing = bool(contexts)
    session_state.update_state(session_id, lambda state: {**state, STOP_CONTINUING: continuing})
    activity_response = {"decision": "block"} if continuing else {}
    session_activity.record_activity(normalized, output=json.dumps(activity_response))
    return {"decision": "continue", "reason": "\n\n".join(contexts)} if continuing else {"decision": "allow"}


def main(payload_text: str, *, event: str) -> int:
    """6イベントの入力を処理し、stdoutへJSON objectを1つ返す。"""
    if event not in EVENTS:
        raise ValueError(f"未対応のAntigravityイベント: {event}")
    payload = json.loads(payload_text)
    if not isinstance(payload, dict):
        raise ValueError("Antigravityのhook入力はJSON objectで指定する")
    response = _dispatch(payload, event)
    print(json.dumps(response, ensure_ascii=False))
    return 0
