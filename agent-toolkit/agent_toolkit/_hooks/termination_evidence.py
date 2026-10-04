"""終了工程の実際の入力・本文の適合結果を保持し、同じ作業の不足を導出する。

各イベントはこの所有モジュールへ観測を渡す。完了の自己申告は受理せず、
報告段階はメインの可視発話から取得する。意味上の中止・開始はメインが
原入力と対象を結び付け、ここでは由来、引用、対象の一致を確かめる。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
from typing import Any, cast

from agent_toolkit._agents_server import tool_names
from agent_toolkit._atk.wi import uwi_scan
from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES
from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
from agent_toolkit._common import automated_prompt, next_action, runtime_inserted
from agent_toolkit._hooks import agent_id, agents_server_session_advisor, report_validation, session_state
from agent_toolkit._hooks.bash_command_parser import extract_execution_segments
from agent_toolkit._hooks.stop_gate import append_stop_log

STATE_KEY = "termination_evidence"


def _parse(payload_text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_text)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("session_id"), str) or not payload["session_id"]:
        return None
    return payload if agent_id.is_main_agent_context(payload) else None


def _data(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    current = state.get(STATE_KEY)
    if (
        not isinstance(current, dict)
        or current.get("session_id") != session_id
        or current.get("version") != 1
        or not all(isinstance(current.get(key), dict) for key in ("works", "calls", "inputs"))
        or not isinstance(current.get("sequence"), int)
    ):
        if current is not None:
            append_stop_log(session_id, "termination_evidence_unavailable", {"reason": "状態の版または形式が異なる"})
        current = {"version": 1, "session_id": session_id, "sequence": 0, "works": {}, "calls": {}, "inputs": {}}
        state[STATE_KEY] = current
    works = current["works"]
    assert isinstance(works, dict)
    for work in works.values():
        reports = work.get("reports", {})
        work["reports"] = {stage: report for stage, report in reports.items() if "call_id" not in report}
    return current


def _compact(data: dict[str, Any]) -> None:
    """現在の証拠と遅延応答に必要な参照だけを保持する。"""
    referenced: set[str] = {data.get("last_input", "")}
    for work in data["works"].values():
        decisions = work.get("decisions", [])
        if decisions:
            work["decisions"] = decisions[-1:]
            referenced.add(decisions[-1].get("input_id", ""))
        referenced.add(work["reference"])
        retained = set(work.get("attempts", {}).values())
        retained.update(item["call_id"] for item in work.get("prepare", [])[-1:])
        if decisions:
            retained.add(decisions[-1].get("call_id", ""))
        retained.add(work.get("failure_call", ""))
        retained.update(work.get("async_targets", {}).values())
        work["prepare"] = work.get("prepare", [])[-1:]
        for call_id in list(work["calls"]):
            records = data["calls"].get(call_id, [])
            if call_id not in retained and all(record.get("observed") for record in records):
                data["calls"].pop(call_id, None)
                work["calls"].remove(call_id)
    data["inputs"] = {key: value for key, value in data["inputs"].items() if key in referenced}


def _new_work(data: dict[str, Any], reference: str) -> tuple[str, dict[str, Any]]:
    data["sequence"] += 1
    work_id = f"work-{data['sequence']}"
    work = {
        "reference": reference,
        "reports": {},
        "attempts": {},
        "calls": [],
        "decisions": [],
        "prepare": [],
        "async_targets": {},
        "offset": data["inputs"].get(reference, {}).get("offset", 0),
        "input_at_last_call": data.get("last_input"),
    }
    data["works"][work_id] = work
    data["current_work"] = work_id
    return work_id, work


def _current_work(data: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    work_id = data.get("current_work")
    work = data["works"].get(work_id)
    return (work_id, work) if isinstance(work_id, str) and isinstance(work, dict) else None


def _last_decision(work: dict[str, Any]) -> str:
    decisions = work.get("decisions", [])
    return decisions[-1]["action"] if decisions else ""


def _finished(data: dict[str, Any], work: dict[str, Any]) -> bool:
    """全段階を満たした作業の後に新しい入力が届いたかを返し、後続の仕事へ古い充足を流用しない。"""
    return bool(work.get("reports")) and not missing_stages(work) and data.get("last_input") != work.get("input_at_last_call")


def _position(payload: dict[str, Any]) -> int:
    path = payload.get("transcript_path")
    try:
        return pathlib.Path(path).stat().st_size if isinstance(path, str) else 0
    except OSError:
        return 0


def _invocations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if payload.get("tool_name") in {
        namespace + operation for namespace in tool_names.MCP_NAMESPACES for operation in tool_names.RECORDED_START_OPERATIONS
    }:
        return [{"kind": "async"}]
    tool_input = payload.get("tool_input")
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if payload.get("tool_name") != "Bash" or not isinstance(command, str):
        return []
    calls: list[dict[str, Any]] = []
    for segment in extract_execution_segments(command):
        if not segment.resolved or not segment.tokens:
            continue
        tokens = list(segment.tokens)
        name = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name
        if name.startswith("python") and len(tokens) > 1 and not tokens[1].startswith("-"):
            tokens.pop(0)
            name = pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name
        arguments: list[str]
        if name in {"atk", "atk.cmd", "atk.py"} and tokens[1:2] == ["run-script"] and len(tokens) > 2:
            operation, arguments = tokens[2], tokens[3:]
        elif name == "session_review_prepare.py":
            operation, arguments = "session-review-prepare", tokens[1:]
        else:
            continue
        if arguments[:1] == ["--"]:
            arguments = arguments[1:]
        if "--help" in arguments or "-h" in arguments:
            continue
        if operation == "session-review-prepare":
            calls.append({"kind": "prepare"})
    return calls


def _response_text(response: object) -> str | None:
    if isinstance(response, str):
        try:
            decoded = json.loads(response)
        except (json.JSONDecodeError, ValueError):
            return response
        if isinstance(decoded, dict):
            return _response_text(decoded)
        return response
    if isinstance(response, dict):
        for key in ("stdout", "output"):
            value = response.get(key)
            if isinstance(value, str):
                return value
    return None


def _returned_text(text: str) -> str:
    # atk自身の全量保存を消費し、欠落したpreviewを準備結果の代用にしない。
    paths = [line.removeprefix("保存先: ").strip() for line in text.splitlines() if line.startswith("保存先: ")]
    if len(paths) == 1:
        path = pathlib.Path(paths[0])
        if path.is_absolute():
            try:
                return path.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                return ""
    return text


def observe_user(payload_text: str) -> None:
    """実際のUserPromptSubmit本文を、生成入力と区別して保存する。"""
    payload = _parse(payload_text)
    if payload is None:
        return
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return
    human = (
        payload.get("source", "user") == "user"
        and not runtime_inserted.is_runtime_generated(payload)
        and not runtime_inserted.is_runtime_inserted_text(prompt)
        and not automated_prompt.contains(prompt)
    )
    if not human and STATE_KEY not in session_state.read_state(payload["session_id"]):
        return

    def update(state: dict[str, Any]) -> dict[str, Any]:
        data = _data(state, payload["session_id"])
        reference = payload.get("turn_id") or payload.get("prompt_id")
        if not isinstance(reference, str) or not reference:
            data["sequence"] += 1
            reference = f"input-{data['sequence']}"
        data["inputs"][reference] = {"text": prompt, "human": human, "offset": _position(payload)}
        data["last_input"] = reference
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)


def observe_tool(payload_text: str, *, after: bool) -> None:
    """同じtool_use_idの実行位置と応答を同じ作業へ対応付ける。"""
    resolved_payload = _parse(payload_text)
    if resolved_payload is None:
        return
    payload: dict[str, Any] = resolved_payload
    tool_id = payload.get("tool_use_id")
    invocations = _invocations(payload)
    if not isinstance(tool_id, str) or not tool_id:
        return
    response = payload.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    failed = payload.get("hook_event_name") == "PostToolUseFailure" or (
        isinstance(code, int) and not isinstance(code, bool) and code != 0
    )
    if not invocations and not (after and failed):
        # 工程外の成功した呼び出しは状態を読み書きせず、全ツール呼び出しの排他更新を避ける。
        return

    def update(state: dict[str, Any]) -> dict[str, Any]:
        if not invocations and STATE_KEY not in state:
            return state
        data = _data(state, payload["session_id"])
        records: Any = data["calls"].get(tool_id)
        if not isinstance(records, list):
            current = _current_work(data)
            if not invocations:
                # 工程外の呼び出しは現在の作業の失敗証拠に限って採取する。
                if current is None:
                    return state
                work_id, work = current
                records = [{"kind": "failure", "work_id": work_id, "observed": True, "response": response}]
                data["calls"][tool_id] = records
                work["calls"].append(tool_id)
                work["failure_call"] = tool_id
                _compact(data)
                return state
            if current is None or _last_decision(current[1]) in {"cancel", "replace"} or _finished(data, current[1]):
                if invocations[0]["kind"] == "async":
                    return state
                current = _new_work(data, data.get("last_input", tool_id))
            work_id, work = current
            work["input_at_last_call"] = data.get("last_input")
            data["sequence"] += 1
            records = [
                {
                    **invocation,
                    "work_id": work_id,
                    "turn_id": payload.get("turn_id"),
                    "offset": _position(payload),
                    "sequence": data["sequence"],
                }
                for invocation in invocations
            ]
            data["calls"][tool_id] = records
            work["calls"].append(tool_id)
        if not after:
            return state
        if records[0]["kind"] == "async":
            started: Any = response
            if isinstance(started, dict) and isinstance(started.get("structuredContent"), dict):
                started = started["structuredContent"]
            if isinstance(started, str):
                try:
                    started = json.loads(started)
                except ValueError:
                    started = None
            if isinstance(started, dict) and isinstance(started.get("session_id"), str):
                record = records[0]
                record["observed"] = True
                data["works"][record["work_id"]]["async_targets"][started["session_id"]] = tool_id
            return state
        text = _response_text(payload.get("tool_response"))
        if text is None:
            return state
        text = _returned_text(text)
        for raw_record in records:
            record = cast(dict[str, Any], raw_record)
            work = data["works"][record["work_id"]]
            record["observed"] = True
            record["response"] = payload.get("tool_response")
            previous_call = work["attempts"].get(record["kind"])
            if previous_call and data["calls"][previous_call][0]["sequence"] > record["sequence"]:
                continue
            work["attempts"][record["kind"]] = tool_id
            if len(records) != 1:
                record["unavailable"] = "複数の呼び出しの出力を分離できない"
                continue
            if record["kind"] == "prepare":
                try:
                    result = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    record["unavailable"] = "準備結果を読み取れない"
                    continue
                if (
                    isinstance(result, dict)
                    and all(
                        isinstance(result.get(key), str) and pathlib.Path(result[key]).is_file()
                        for key in ("conversation_path", "candidates_path", "stats_path")
                    )
                    and isinstance(result.get("prepared_at"), str)
                ):
                    work["prepare"].append({"call_id": tool_id, "result": result})
                else:
                    record["invalid"] = True
                continue
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)


def observe_reports(payload: dict[str, Any]) -> bool:
    """現在の作業に属する可視発話の報告だけを取り込み、言い回しで段階を判定しない。"""
    available = False

    def update(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal available
        data = _data(state, payload["session_id"])
        current = _current_work(data)
        if current is not None and _last_decision(current[1]) in {"cancel", "replace", "blocked"}:
            return state
        reference = data.get("last_input", payload["session_id"])
        if current is None or _finished(data, current[1]):
            offset = data["inputs"].get(reference, {}).get("offset", 0)
        else:
            offset = current[1].get("offset", 0)
        messages = visible_messages(payload, offset)
        if messages is None:
            append_stop_log(payload["session_id"], "termination_reports_unavailable", {})
            return state
        available = True
        reports = report_validation.reports_from_messages(messages)
        if not reports:
            return state
        if current is None or _finished(data, current[1]):
            current = _new_work(data, reference)
        work = current[1]
        work["reports"] = {stage: report for stage, report in work["reports"].items() if "call_id" not in report}
        work["reports"].update({stage: {"text": text} for stage, text in reports.items()})
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)
    return available


def report_violations(work: dict[str, Any]) -> list[str]:
    """同じ作業の発話本文へ、人間由来の4判定だけを適用する。"""
    return [
        error
        for stage, report in work.get("reports", {}).items()
        for error in report_validation.validate_report(report["text"], stage)
    ]


def _waiting_uwi(path_text: object, session_id: str, quote: object) -> pathlib.Path:
    """現在の会話が投入した実在の未回答UWIと引用を対応付ける。"""
    root = uwi_scan.private_notes_root()
    if root is None or not isinstance(path_text, str):
        raise ValueError("確認待ちには未回答UWIの絶対パスを渡す")
    path = pathlib.Path(path_text)
    if not path.is_absolute() or path.parent not in {root / name for name in WI_PROCESSABLE_STATES}:
        raise ValueError("確認待ちのUWIはactive状態のキューから取得する")
    text = path.read_text(encoding="utf-8")
    parsed = parse_frontmatter(text)
    if parsed is None or parsed[0].get("type") != "uwi" or parsed[0].get("submitter_session") != session_id:
        raise ValueError("現在の会話が投入したUWIを指定する")
    if uwi_scan.is_uwi_answered(text) or quote != text:
        raise ValueError("実際の未回答UWI全文をquoteへ渡す")
    return path


def record_decision(document: dict[str, Any]) -> str:
    """実在する原入力と作用対象を持つ判断を記録し、対象作業の識別子を返す。"""
    session_id = document.get("session_id")
    action = document.get("action")
    if (
        not isinstance(session_id, str)
        or not session_id
        or action not in {"start", "cancel", "replace", "resume", "wait", "blocked"}
    ):
        raise ValueError("session_idと受理する判断のactionを指定する")
    if not agent_id.is_main_agent_context({"agent_id": document.get("agent_id", "main")}):
        raise ValueError("終了工程の判断はメインが記録する")
    owner = os.environ.get("AGENT_TOOLKIT_OWNER_SESSION")
    if owner and owner != session_id:
        raise ValueError("現在の会話のsession_idを指定する")
    if not isinstance(document.get("reason"), str) or not document["reason"].strip():
        raise ValueError("判断の理由を指定する")
    selected = ""

    def update(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal selected
        data = _data(state, session_id)
        evidence: dict[str, Any] = {}
        if action == "wait":
            target = document.get("target_session_id")
            child = state.get("agents_server_sessions", {}).get(target)
            work_for_wait = data["works"].get(document.get("work_id"), {})
            if target is not None:
                if not isinstance(target, str) or target not in work_for_wait.get("async_targets", {}):
                    raise ValueError(f"委譲先待機の対象が作業に対応しません: {target}。実際のwork_idと対象sessionを指定する")
                if not isinstance(child, dict) or child.get("owner_agent_id") != "main":
                    raise ValueError(
                        f"委譲先待機の対象を所有していません: {target}。この作業が起動した所有済みの対象を指定する"
                    )
                active = agents_server_session_advisor.actively_waited_session_ids([target])
                if child.get("pending_observation") is not True and target not in active:
                    raise ValueError(
                        f"対象sessionの有効なCLI待機がありません: {target}。実際の待機を開始するか回収後の残工程へ戻る"
                    )
                evidence["target_session_id"] = target
            else:
                evidence["uwi_file"] = str(_waiting_uwi(document.get("uwi_file"), session_id, document.get("quote")))
        elif action == "blocked":
            call_id = document.get("call_id")
            records = data["calls"].get(call_id)
            if (
                not isinstance(records, list)
                or len(records) != 1
                or records[0].get("kind") != "failure"
                or records[0].get("work_id") != document.get("work_id")
            ):
                raise ValueError("対象作業の実際の失敗call_idを指定する")
            if json.dumps(records[0]["response"], ensure_ascii=False, sort_keys=True) != document.get("quote"):
                raise ValueError("実際の失敗応答全体をJSONのquoteへ渡す")
            evidence["call_id"] = call_id
        else:
            reference = data["inputs"].get(document.get("input_id"))
            if (
                not isinstance(reference, dict)
                or reference.get("human") is not True
                or reference.get("text") != document.get("quote")
            ):
                raise ValueError("原入力のinput_idと全文のquoteを渡す。生成入力は判断の人間の根拠にできない")
            evidence["input_id"] = document["input_id"]
        if action == "start":
            selected, work = _new_work(data, document["input_id"])
        else:
            selected = document.get("work_id", "")
            work = data["works"].get(selected)
            if not isinstance(work, dict):
                raise ValueError("実在するwork_idを指定する")
        work["decisions"].append({"action": action, **evidence, "reason": document["reason"]})
        if action == "resume":
            data["current_work"] = selected
        _compact(data)
        return state

    session_state.update_state(session_id, update)
    return selected


def pending_work(payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """現在の会話の観測済み作業を返し、別会話の継承値を流用しない。"""
    state = session_state.read_state(payload["session_id"])
    data = state.get(STATE_KEY)
    if not isinstance(data, dict) or data.get("session_id") != payload["session_id"] or data.get("version") != 1:
        return []
    pending: list[tuple[str, dict[str, Any]]] = []
    for work_id, work in data.get("works", {}).items():
        if not isinstance(work, dict) or _last_decision(work) in {"cancel", "replace", "blocked"}:
            continue
        if _last_decision(work) == "wait":
            decision = work["decisions"][-1]
            target = decision.get("target_session_id")
            child = state.get("agents_server_sessions", {}).get(target)
            if (
                isinstance(target, str)
                and target in work.get("async_targets", {})
                and isinstance(child, dict)
                and child.get("owner_agent_id") == "main"
                and (
                    child.get("pending_observation") is True
                    or target in agents_server_session_advisor.actively_waited_session_ids([target])
                )
            ):
                continue
            path = decision.get("uwi_file")
            if isinstance(path, str):
                try:
                    if not uwi_scan.is_uwi_answered(pathlib.Path(path).read_text(encoding="utf-8")):
                        continue
                except (OSError, UnicodeError) as error:
                    append_stop_log(
                        payload["session_id"], "termination_wait_unknown", {"work_id": work_id, "reason": str(error)}
                    )
                    continue
        pending.append((work_id, work))
    return pending


def decision_hint(payload: dict[str, Any]) -> str:
    """判断記録に必要な会話と直近のユーザー入力の識別子を、不足の通知へ添える本文として返す。"""
    data = session_state.read_state(payload["session_id"]).get(STATE_KEY)
    last = data.get("last_input") if isinstance(data, dict) else None
    entry = data.get("inputs", {}).get(last) if isinstance(data, dict) else None
    lines = [f"判断記録の`session_id`: {payload['session_id']}"]
    if isinstance(entry, dict) and entry.get("human") is True:
        lines.append(f"直近のユーザー入力の`input_id`: {last}")
    return "\n".join(lines)


def missing_stages(work: dict[str, Any]) -> list[str]:
    """実際の準備結果と可視発話から残る報告段階を導出する。"""
    data_calls = work.get("prepare", [])
    reports = work.get("reports", {})
    if not data_calls and not reports:
        return []
    result = reports.get("review-result")
    if not isinstance(result, dict):
        return ["review-result"]
    marker = report_validation.SCHEDULED_MARKER
    return ["review-submission"] if marker in result["text"] and "review-submission" not in reports else []


def visible_messages(payload: dict[str, Any], offset: int) -> list[str] | None:
    """現在の形式のassistant可視本文だけを取り出す。工程完了の推定には使わない。"""
    path = payload.get("transcript_path")
    texts: list[str] = []
    incomplete = False
    if isinstance(path, str):
        try:
            with pathlib.Path(path).open("rb") as stream:
                stream.seek(0, os.SEEK_END)
                if stream.tell() < offset:
                    return None
                stream.seek(offset)
                for raw in stream:
                    try:
                        entry = json.loads(raw)
                    except (json.JSONDecodeError, UnicodeError):
                        incomplete = True
                        continue
                    if (
                        not isinstance(entry, dict)
                        or entry.get("isSidechain") is True
                        or entry.get("agent_id", "main") != "main"
                    ):
                        continue
                    event = entry.get("payload", {})
                    if (
                        entry.get("type") == "response_item"
                        and event.get("type") == "message"
                        and event.get("role") == "assistant"
                    ):
                        if event.get("channel") not in {None, "final", "commentary"}:
                            continue
                        texts.extend(
                            part["text"]
                            for part in event.get("content", [])
                            if isinstance(part, dict)
                            and part.get("type") == "output_text"
                            and isinstance(part.get("text"), str)
                        )
                    elif entry.get("type") == "assistant":
                        message = entry.get("message", {})
                        texts.extend(
                            part["text"]
                            for part in message.get("content", [])
                            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                        )
        except OSError:
            return None
    last = payload.get("last_assistant_message")
    if isinstance(last, str):
        texts.append(last)
    return texts if not incomplete and (texts or isinstance(last, str)) else None


def main(argv: list[str] | None = None) -> int:
    """判断記録用のJSONファイルを既存run-scriptから受理する。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--decision-file", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.decision_file.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("判断記録はJSONオブジェクトで渡す")
        work_id = record_decision(document)
    except (OSError, UnicodeError, ValueError) as error:
        next_action.report(str(error), next_action="原入力と対象を確かめ、判断のJSONファイルを直して同じコマンドで再実行する")
        return 2
    print(json.dumps({"work_id": work_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
