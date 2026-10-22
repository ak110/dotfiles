"""Antigravityの対話記録とJSONストリームを、原本を保持した会話エントリへ変換する。"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Any


def is_agy_entry(entry: dict[str, Any]) -> bool:
    """対話ステップ、公開ストリーム、またはその正規化済みエントリを識別する。"""
    return (
        "_agy_original" in entry
        or isinstance(entry.get("step_index"), int)
        and isinstance(entry.get("source"), str)
        or entry.get("event") in {"init", "step_update", "result"}
    )


def _decoded(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def _arguments(value: Any) -> dict[str, Any]:
    value = _decoded(value)
    return {key: _decoded(item) for key, item in value.items()} if isinstance(value, dict) else {}


def user_text(content: Any) -> str:
    """USER_REQUESTの内側を逐語で返し、ホストが付加したmetadataを除く。"""
    if not isinstance(content, str):
        return ""
    match = re.fullmatch(r"<USER_REQUEST>\n(.*)\n</USER_REQUEST>(?:\n.*)?", content, re.DOTALL)
    return match.group(1) if match else content


def question_result(questions: Any, value: Any) -> dict[str, Any] | None:
    """ask_questionの番号付き回答を、改行を保持して共通の回答構造へ写す。"""
    if not isinstance(questions, list) or not isinstance(value, str):
        return None
    markers = list(re.finditer(r"(?m)^A(\d+): ?", value))
    if len(markers) != len(questions) or [int(marker[1]) for marker in markers] != list(range(1, len(questions) + 1)):
        return None
    answers = {}
    for index, (question, marker) in enumerate(zip(questions, markers, strict=True)):
        if not isinstance(question, dict) or not isinstance(question.get("question"), str):
            return None
        end = markers[index + 1].start() if index + 1 < len(markers) else len(value)
        answers[question["question"]] = value[marker.end() : end].rstrip("\r\n")
    return {"questions": questions, "answers": answers}


def normalize_entries(entries: list[dict[str, Any]], *, hook_view: bool = False) -> list[dict[str, Any]]:
    """物理行と1対1の解析用写しを返す。原本・行番号・バイト位置は呼出側で保持する。

    対話記録のGENERICは直前のtool_callsの順序で結果を持つ。対応する呼び出しをキューで保持し、
    明示IDのない記録にstep_indexと配列位置から局所IDを与える。各レコードへ原本を残すため、
    identityや構造照会は変換前のモデル・推論量・ツール名を参照できる。
    """
    pending: deque[tuple[str, str, dict[str, Any]]] = deque()
    stream_tools: set[str] = set()
    result: list[dict[str, Any]] = []
    for entry in entries:
        if not is_agy_entry(entry) or "_agy_original" in entry:
            result.append(entry)
            continue
        original = entry
        body = entry.get("step_update")
        body = body if isinstance(body, dict) else entry
        kind = body.get("type", body.get("step_type"))
        content: list[dict[str, Any]] = []
        normalized: dict[str, Any] = {
            "_agy_original": original,
            "timestamp": entry.get("created_at", entry.get("timestamp", body.get("created_at"))),
        }
        if entry.get("isSidechain") is True:
            normalized["isSidechain"] = True
        role = "assistant"
        identity = str(body.get("step_index", len(result)))
        if kind in ("USER_INPUT", "user_input"):
            role = "user"
            text = user_text(body.get("content", body.get("text")))
            if text:
                content.append({"type": "text", "text": text})
            if body.get("source") not in {None, "USER_EXPLICIT"}:
                normalized["isMeta"] = True
        elif kind == "GENERIC" and pending:
            role = "user"
            call_id, name, args = pending.popleft()
            text = body.get("content", "")
            content.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call_id,
                    "content": text,
                    "is_error": body.get("status") == "ERROR" or body.get("error") is not None,
                }
            )
            normalized["toolUseResult"] = _result_mapping(text)
            if name == "ask_question" and (answer := question_result(args.get("questions"), text)):
                normalized["toolUseResult"] = answer
        elif kind in {"PLANNER_RESPONSE", "agent_response"}:
            text = body.get("content", body.get("text", body.get("text_delta")))
            if isinstance(text, str) and text:
                content.append({"type": "text", "text": text})
            thinking = body.get("thinking")
            if isinstance(thinking, str) and thinking:
                content.append({"type": "thinking", "thinking": thinking})
            calls = body.get("tool_calls", [])
            for index, call in enumerate(calls if isinstance(calls, list) else []):
                if not isinstance(call, dict) or not isinstance(call.get("name"), str):
                    continue
                name, args = call["name"], _arguments(call.get("args"))
                call_id = str(call.get("id") or f"agy-{identity}-{index}")
                pending.append((call_id, name, args))
                content.append({"type": "tool_use", "id": call_id, "name": name, "input": args})
        elif kind == "tool":
            info = body.get("tool_info")
            info = info if isinstance(info, dict) else {}
            call_id = str(body.get("tool_call_id") or f"agy-tool-{identity}")
            name = str(body.get("tool_name") or "")
            args = _arguments(info.get("parameters"))
            if call_id not in stream_tools:
                content.append({"type": "tool_use", "id": call_id, "name": name, "input": args})
                stream_tools.add(call_id)
            if body.get("state") != "ACTIVE":
                content.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": info.get("result", info.get("output", "")),
                        "is_error": body.get("state") == "ERROR" or info.get("error") is not None,
                    }
                )
        elif entry.get("event") == "result":
            response = entry.get("result")
            response = response if isinstance(response, dict) else {}
            text = response.get("response")
            if isinstance(text, str):
                content.append({"type": "text", "text": text})
        message: dict[str, Any] = {"id": f"agy-{identity}", "role": role, "content": content}
        usage = usage_tokens(original)
        if usage is not None:
            message["usage"] = usage
        for key in ("model", "effort"):
            value = body.get(key, entry.get(key))
            if isinstance(value, str):
                if key == "model":
                    message[key] = value
                else:
                    normalized[key] = value
        if kind in {"PLANNER_RESPONSE", "agent_response"} and not body.get("tool_calls"):
            message["stop_reason"] = "end_turn"
        normalized.update(type=role, message=message)
        result.append(normalized)
    return [_hook_entry(entry) for entry in result] if hook_view else result


def normalize_tool_call(name: str, args: Any) -> tuple[str, dict[str, Any]]:
    """実機ツールの名前とJSON符号化された引数を、既存hookの契約へそろえる。"""
    return _canonical_tool_call(name, _arguments(args))


def _canonical_tool_call(name: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    names = {
        "run_command": "Bash",
        "read_file": "Read",
        "view_file": "Read",
        "edit_file": "Edit",
        "write_file": "Write",
        "ask_question": "AskUserQuestion",
        "invoke_subagent": "Agent",
        "manage_task": "TaskUpdate",
        "manage_subagents": "TaskList",
    }
    keys = {
        "CommandLine": "command",
        "Path": "file_path",
        "Content": "content",
        "Target": "old_string",
        "Replacement": "new_string",
        "Prompt": "prompt",
        "Role": "description",
        "Model": "model",
        "TypeName": "subagent_type",
        "StartLine": "offset",
    }
    canonical = {keys.get(key, key): value for key, value in args.items()}
    if name == "ask_question" and isinstance(canonical.get("questions"), list):
        canonical["questions"] = [
            {
                **question,
                "options": [{"label": option} if isinstance(option, str) else option for option in question.get("options", [])],
            }
            for question in canonical["questions"]
            if isinstance(question, dict)
        ]
    return names.get(name, name), canonical


def _hook_entry(entry: dict[str, Any]) -> dict[str, Any]:
    if "_agy_original" not in entry:
        return entry
    message = entry.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return entry
    content = []
    for block in message["content"]:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            raw_args = block.get("input")
            name, args = _canonical_tool_call(str(block.get("name", "")), raw_args if isinstance(raw_args, dict) else {})
            block = {**block, "name": name, "input": args}
        content.append(block)
    return {**entry, "message": {**message, "content": content}}


def _result_mapping(value: Any) -> dict[str, Any] | None:
    """MCPの結果にあるJSON本文を、呼び出しIDとの対応を保って取り出す。"""
    parsed = _decoded(value)
    if isinstance(parsed, dict):
        return parsed
    if isinstance(value, str):
        for line in value.splitlines():
            parsed = _decoded(line)
            if isinstance(parsed, dict):
                return parsed
        for match in re.finditer(r"\{", value):
            try:
                parsed, _end = json.JSONDecoder().raw_decode(value[match.start() :])
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def usage_tokens(entry: dict[str, Any]) -> dict[str, int] | None:
    """応答ステップの増分トークンだけを共通4成分へ写す。"""
    entry = entry.get("_agy_original", entry)
    body = entry.get("step_update", entry)
    if not isinstance(body, dict) or body.get("type", body.get("step_type")) not in {"PLANNER_RESPONSE", "agent_response"}:
        return None
    usage = body.get("usage", body)
    if not isinstance(usage, dict) or not any(key in usage for key in ("input_tokens", "output_tokens")):
        return None

    def number(key: str) -> int:
        value = usage.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0

    return {
        "input_tokens": number("input_tokens"),
        "output_tokens": number("output_tokens"),
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": number("cache_read_tokens"),
    }
