"""成果作業と`agent-toolkit:report-completion`の起動証拠を作業単位で保持する。

各イベントはこの所有モジュールへ観測を渡す。成功したSkill起動は本文版付きの共通取得記録にも接続する。
成果作業は成功した対象スキルの開始、リポジトリへの自編集、
計画の保存、WIの保存、commit・pushの実行記録から、起動証拠はClaude Codeの成功したSkill呼び出しと、
Codexの実際の入力と結果が示す`SKILL.md`の読取から取得する。完了の自己申告と報告本文は受理しない。
意味上の中止・開始はメインが原入力と対象を結び付け、ここでは識別子から実体を読み、由来と対象を確かめる。
"""

from __future__ import annotations

import json
import os
import pathlib
lazy import argparse
lazy from typing import Any

lazy from agent_toolkit._atk.wi import uwi_scan
lazy from agent_toolkit._common import automated_prompt, next_action, read_triggers, runtime_inserted, session_state, shell_cwd
lazy from agent_toolkit._common.shell_tokens import ATK_SHELL_TOOLS, join_words
lazy from agent_toolkit._hooks import agent_id
lazy from agent_toolkit._hooks import document_reads as _document_reads
lazy from agent_toolkit._hooks.host import is_codex_payload
lazy from agent_toolkit._hooks.pretooluse.operation_skills import mark_ready
lazy from agent_toolkit._hooks.termination_evidence_decisions import _omission_policy, missing_completion, record_decision
lazy from agent_toolkit._hooks.termination_evidence_operations import (
    _git_root,
    _hook_tool_input,
    _invocations,
    _read_skills,
    published_git_operations,
    unscoped_self_edits,
)
lazy from agent_toolkit._hooks.termination_evidence_state import (
    _HOST_STOP_FIX,
    _UNRESOLVED_REPOSITORY_FIX,
    COMPLETION_REPORT,
    STATE_KEY,
    _compact,
    _current_work,
    _data,
    _FailureEvidenceError,
    _finished,
    _InactiveWaitError,
    _last_decision,
    _new_work,
    _parse,
    _position,
    _store_input,
    _sync_queued_inputs,
    _UnresolvedRepositoryError,
    decision_hint,
    evidence_available,
    pending_work,
    session_works,
    waits_on_delegated_session,
)

__all__ = [
    "_hook_tool_input",
    "_omission_policy",
    "decision_hint",
    "evidence_available",
    "missing_completion",
    "pending_work",
    "published_git_operations",
    "session_works",
    "unscoped_self_edits",
    "uwi_scan",
    "waits_on_delegated_session",
]


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
        # この本文より前にセッション記録へ書かれた途中入力を先に取り込み、入力の順序を記録の順序と一致させる。
        _sync_queued_inputs(data, payload.get("transcript_path"))
        reference = payload.get("turn_id") or payload.get("prompt_id")
        if not isinstance(reference, str) or not reference:
            data["sequence"] += 1
            reference = f"input-{data['sequence']}"
        _store_input(data, reference, prompt, human=human, offset=_position(payload))
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
    if not isinstance(tool_id, str) or not tool_id:
        return
    response = payload.get("tool_response")
    code = response.get("exit_code") if isinstance(response, dict) else None
    failed = payload.get("hook_event_name") == "PostToolUseFailure" or (
        isinstance(code, int) and not isinstance(code, bool) and code != 0
    )
    if after and is_codex_payload(payload) and not failed:
        _register_document_reads(payload)
    invocations = _invocations(payload, after=after, failed=failed)
    _observe_invocations(payload, invocations, after=after, failed=failed)


def _register_document_reads(payload: dict[str, Any]) -> None:
    """内側の成功は読取意図と版だけを保留し、本文受信の証拠にはしない。"""
    tool_input = payload.get("tool_input", {})
    if (
        not isinstance(tool_input, dict)
        or payload.get("tool_name") not in ATK_SHELL_TOOLS
        or not isinstance(tool_input.get("command"), str)
    ):
        return
    response = payload.get("tool_response")
    if isinstance(response, dict) and any(response.get(key) for key in ("isError", "is_error", "interrupted")):
        return
    state = session_state.read_state(payload["session_id"])
    candidates = [
        packet["source"]
        for packet in state.get("document_read_pending", {}).get("main", [])
        if packet.get("read_id") == payload.get("tool_use_id")
    ]
    candidates.extend(state.get("document_read_candidates", {}).get("main", []))
    pending = []
    documents = []
    for invocation, current in shell_cwd.bash_invocation_cwds(tool_input["command"], payload.get("cwd", "")):
        if not current.resolved:
            continue
        for read in _read_skills(join_words(invocation.segment.tokens)):
            path = str((pathlib.Path(current.path) / read.path).resolve())
            if _document_reads.document_plugin(path) == "agent-toolkit":
                documents.append(_document_reads.DocumentRead(read.skill, read.relative_path, "agent-toolkit", path))
    for document in documents:
        metadata = next((item for item in candidates if item["path"] == document.path and not item["heading"]), None)
        if metadata is None:
            metadata = next(
                iter(_document_reads.receipt_candidates([read_triggers.ReadTarget(pathlib.Path(document.path))])), None
            )
        if metadata is None:
            continue
        records: list[dict[str, Any]] = [{"kind": "completion"}]
        if document.skill != COMPLETION_REPORT:
            repository = _git_root(payload.get("cwd", ""))
            records = [
                {
                    "kind": "deliverable",
                    "labels": [f"agent-toolkit:{document.skill}の開始"],
                    "repository": str(repository) if repository else None,
                }
            ]
        pending.append(
            {
                "path": document.path,
                "version": metadata["version"],
                "parts": metadata["parts"],
                "tool_id": payload["tool_use_id"],
                "records": records,
            }
        )
    if pending:

        def register(value: dict[str, Any]) -> dict[str, Any]:
            queue = _data(value, payload["session_id"]).setdefault("pending_reads", [])
            for item in pending:
                if not any(old["path"] == item["path"] and old["version"] == item["version"] for old in queue):
                    queue.append(item)
            return value

        session_state.update_state(payload["session_id"], register)


def observe_delivered_documents(payload_text: str) -> None:
    """実配送の共通記録で本文を確認してから、Codexの起動証拠へ接続する。"""
    payload = _parse(payload_text)
    if payload is None or not is_codex_payload(payload):
        return
    state = session_state.read_state(payload["session_id"])
    data = state.get(STATE_KEY, {})
    queue = data.get("pending_reads", []) if isinstance(data, dict) else []
    receipts = [
        json.loads(key.removeprefix(_document_reads.RECEIPT_PREFIX))
        for key, actors in state.get("operation_skill_delivered_agents", {}).items()
        if key.startswith(_document_reads.RECEIPT_PREFIX) and "main" in actors
    ]
    for pending in list(queue):
        if not any(not item.get("partial") and not item["heading"] and item["parts"] == pending["parts"] for item in receipts):
            continue
        _observe_invocations({**payload, "tool_use_id": pending["tool_id"]}, pending["records"], after=True, failed=False)

        def remove(value: dict[str, Any], pending: dict = pending) -> dict[str, Any]:
            current = value.get(STATE_KEY, {}).get("pending_reads", [])
            if pending in current:
                current.remove(pending)
            return value

        session_state.update_state(payload["session_id"], remove)


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
            if current is None or _last_decision(current[1]) in {"cancel", "replace"} or _finished(data, current[1]):
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
            if record["kind"] == "completion":
                work["completion_report"] = True
                if "skill_invocation" in record:
                    mark_ready(record["skill_invocation"], agent_id.resolve_hook_agent_id(payload))(state)
            elif record["kind"] == "deliverable":
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
    """判断記録用のJSONファイルを既存run-scriptから受理する。"""
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
                # 直前の作業観測より後に届いた途中入力も、返す人間入力へ含める。
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
            # 作業・会話の識別子と人間入力由来の項目を分けて返す。実行結果を根拠とする判断（blocked、委譲先のwait）は
            # 人間入力を要さないため、自動起動の会話でも識別子の取得を止めない。人間の判断を要する記録の検証は
            # record_decisionが担い、生成入力は人間入力として返さない。
            input_id = data.get("last_human_input", data.get("last_input"))
            entry = data.get("inputs", {}).get(input_id) if isinstance(data.get("inputs"), dict) else None
            human = isinstance(entry, dict) and entry.get("human") is True
            current = _current_work(data)
            work_id = (
                current[0]
                if current is not None
                and not _finished(data, current[1])
                and _last_decision(current[1]) not in {"cancel", "replace"}
                else None
            )
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
        recovery = "原入力と対象を確かめ、判断のJSONファイルを直して同じコマンドで再実行せよ"
        if isinstance(error, _UnresolvedRepositoryError):
            recovery = _UNRESOLVED_REPOSITORY_FIX
        elif isinstance(error, _FailureEvidenceError):
            recovery = (
                "受理する証拠は失敗応答を返したツール呼び出しだけである。別のcall_idでの再試行では記録できない。"
                + _HOST_STOP_FIX
            )
        elif isinstance(error, _InactiveWaitError):
            recovery = "`atk agents wait`で待機を開始するか回収後の残工程へ戻れ。" + _HOST_STOP_FIX
        next_action.report(
            str(error),
            next_action=recovery,
        )
        return 2
    print(json.dumps({"work_id": work_id}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
