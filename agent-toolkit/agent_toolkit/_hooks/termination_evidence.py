"""成果作業の対象と、中止・置換・待機・実際の失敗の判断根拠を保持する。  pyfltr: allow-comment"""

from __future__ import annotations

import json
import os
import pathlib
lazy import argparse
lazy from typing import Any

lazy from agent_toolkit._atk.wi import uwi_scan
lazy from agent_toolkit._common import automated_prompt, next_action, runtime_inserted, session_state
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks.termination_evidence_decisions import record_decision
lazy from agent_toolkit._hooks.termination_evidence_operations import (
    _hook_tool_input,
    _invocations,
    published_git_operations,
)
lazy from agent_toolkit._hooks.termination_evidence_state import (
    STATE_KEY,
    _compact,
    _current_work,
    _data,
    _FailureEvidenceError,
    _InactiveWaitError,
    _last_decision,
    _new_work,
    _parse,
    _position,
    _store_input,
    _sync_queued_inputs,
    completion_playbook_path,
    decision_hint,
    host_stop_fix,
    pending_work,
    session_works,
    termination_evidence_reference,
    waits_on_delegated_session,
)

__all__ = [
    "_hook_tool_input",
    "completion_playbook_path",
    "decision_hint",
    "pending_work",
    "published_git_operations",
    "session_works",
    "termination_evidence_reference",
    "uwi_scan",
    "waits_on_delegated_session",
]


def observe_user(payload_text: str, *, entry_work: str | None = None) -> None:
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

        _sync_queued_inputs(data, payload.get("transcript_path"))
        reference = payload.get("turn_id") or payload.get("prompt_id")
        if not isinstance(reference, str) or not reference:
            data["sequence"] += 1
            reference = f"input-{data['sequence']}"
        _store_input(data, reference, prompt, human=human, offset=_position(payload))
        if human and entry_work is not None:
            data["entry_work"] = entry_work
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)


def observe_tool(payload_text: str, *, after: bool) -> None:
    resolved_payload = _parse(payload_text)
    if resolved_payload is None:
        return
    payload: dict[str, Any] = resolved_payload
    tool_id = payload.get("tool_use_id")
    if not isinstance(tool_id, str) or not tool_id:
        return
    response = payload.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    failed = payload.get("hook_event_name") == "PostToolUseFailure" or (
        isinstance(code, int) and not isinstance(code, bool) and code != 0
    )
    invocations = _invocations(payload, after=after, failed=failed)
    _observe_invocations(payload, invocations, after=after, failed=failed)


def _observe_invocations(payload: dict[str, Any], invocations: list[dict[str, Any]], *, after: bool, failed: bool) -> None:
    tool_id = payload["tool_use_id"]
    response = payload.get("tool_response")
    if not invocations and not (after and failed):
        # 工程外の成功した呼び出しは状態を読み書きせず、全ツール呼び出しの排他更新を避ける。
        return

    def update(state: dict[str, Any]) -> dict[str, Any]:
        if not invocations and STATE_KEY not in state:
            return state
        data = _data(state, payload["session_id"])
        _sync_queued_inputs(data, payload.get("transcript_path"))
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
            if current is None or _last_decision(current[1]) in {"cancel", "replace"}:
                if invocations[0]["kind"] == "async":
                    return state
                current = _new_work(data, data.get("last_human_input", data.get("last_input", tool_id)))
            work_id, work = current
            work["input_at_last_call"] = data.get("last_human_input", data.get("last_input"))
            data["sequence"] += 1
            records = [{**invocation, "work_id": work_id, "sequence": data["sequence"]} for invocation in invocations]
            data["calls"][tool_id] = records
            work["calls"].append(tool_id)
        if not after:
            return state
        for record in records:
            work = data["works"].get(record["work_id"])
            if not isinstance(work, dict):
                continue
            record["observed"] = True
            if record["kind"] == "deliverable":
                if entry_work := record.get("entry_work"):
                    data["entry_work"] = entry_work
                known_labels = work.setdefault("deliverables", [])
                known_labels.extend(label for label in record["labels"] if label not in known_labels)
                if "repository" in record:
                    repositories = work.setdefault("repositories", [])
                    if record["repository"] not in repositories:
                        repositories.append(record["repository"])
                    if "paths" in record:
                        known = work.setdefault("edits", {}).setdefault(record["repository"], [])
                        known.extend(path for path in record["paths"] if path not in known)
            elif record["kind"] == "async":
                started: Any = response
                if isinstance(started, dict) and isinstance(started.get("structuredContent"), dict):
                    started = started["structuredContent"]
                if isinstance(started, str):
                    try:
                        started = json.loads(started)
                    except ValueError:
                        started = None
                if isinstance(started, dict) and isinstance(started.get("session_id"), str):
                    work["async_targets"][started["session_id"]] = tool_id
        _compact(data)
        return state

    session_state.update_state(payload["session_id"], update)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument(
        "--decision-file",
        type=pathlib.Path,
        metavar="PATH",
        help="判断記録のJSONオブジェクトを保存したファイルのパス（JSON文字列そのものは受け取らない）",
    )
    operation.add_argument(
        "--context",
        action="store_true",
        help="現在のClaude Codeメインの会話・作業の識別子と、観測済みなら人間入力をJSONで取得する",
    )
    args = parser.parse_args(argv)
    try:
        if args.context:
            session_id = os.environ.get("CLAUDE_CODE_SESSION_ID", "")
            if not session_id or not agent_id.is_main_agent_context({}):
                raise ValueError("--contextは現在のClaude Codeメインで実行する")

            def synchronize(state: dict[str, Any]) -> dict[str, Any] | None:

                current = state.get(STATE_KEY)
                if not isinstance(current, dict) or current.get("session_id") != session_id:
                    return None
                _sync_queued_inputs(current, None)
                return state

            session_state.update_state(session_id, synchronize)
            data = session_state.read_state(session_id).get(STATE_KEY)
            if (
                not isinstance(data, dict)
                or data.get("session_id") != session_id
                or data.get("version") != 1
                or not isinstance(data.get("works"), dict)
            ):
                raise ValueError("現在の会話の終了工程の証拠をまだ観測していない")

            input_id = data.get("last_human_input", data.get("last_input"))
            entry = data.get("inputs", {}).get(input_id) if isinstance(data.get("inputs"), dict) else None
            human = isinstance(entry, dict) and entry.get("human") is True
            current = _current_work(data)
            work_id = current[0] if current is not None and _last_decision(current[1]) not in {"cancel", "replace"} else None
            if not human and work_id is None:
                raise ValueError("現在の会話の作業と人間入力をまだ観測していない")
            print(
                json.dumps(
                    {
                        "session_id": session_id,
                        "input_id": input_id if human else None,
                        "quote": entry["text"] if human and isinstance(entry, dict) else None,
                        "work_id": work_id,
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        document = json.loads(args.decision_file.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("判断記録はJSONオブジェクトで渡す")
        work_id = record_decision(document)
    except (OSError, UnicodeError, ValueError) as error:
        recovery = "原入力と対象を確かめ、判断のJSONファイルを直して同じコマンドで再実行してください"
        if isinstance(error, _FailureEvidenceError):
            recovery = (
                "失敗応答を返したツール呼び出しの`call_id`で記録してください。受理する証拠は"
                f"{termination_evidence_reference()}「受理する証拠」に従ってください。" + host_stop_fix()
            )
        elif isinstance(error, _InactiveWaitError):
            recovery = (
                "`atk agents wait`で待機を開始するか回収後の残工程へ戻ってください。"
                f"{termination_evidence_reference()}「受理する証拠」に従ってください。" + host_stop_fix()
            )
        next_action.report(
            str(error),
            next_action=recovery,
        )
        return 2
    print(json.dumps({"work_id": work_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
