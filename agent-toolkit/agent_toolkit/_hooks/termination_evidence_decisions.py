"""終了工程の判断記録と公開CLI。"""

from __future__ import annotations

import json
import os
import pathlib
lazy from typing import Any

lazy from agent_toolkit._atk.wi import layout, uwi_scan
lazy from agent_toolkit._atk.wi.constants import WI_PROCESSABLE_STATES
lazy from agent_toolkit._atk.wi.frontmatter import parse_frontmatter
lazy from agent_toolkit._common import delegated_session as _delegated_session
lazy from agent_toolkit._common import session_state
lazy from agent_toolkit._hooks import agent_id, agents_server_observations
lazy from agent_toolkit._hooks.termination_evidence_state import (
    _compact,
    _data,
    _FailureEvidenceError,
    _InactiveWaitError,
    _new_work,
    _sync_queued_inputs,
)


def _waiting_uwi(document: dict[str, Any], session_id: str) -> pathlib.Path:
    path_text = document.get("uwi_file")
    root = uwi_scan.private_notes_root()
    if root is None or not isinstance(path_text, str):
        raise ValueError("確認待ちには未回答UWIの絶対パスを渡す")
    path = pathlib.Path(path_text)
    if not path.is_absolute() or path not in layout.iter_entry_paths(root):
        raise ValueError("確認待ちのUWIはactive状態のキューから取得する")
    text = path.read_text(encoding="utf-8")
    if layout.entry_state_of(path, text) not in WI_PROCESSABLE_STATES:
        raise ValueError("確認待ちのUWIはactive状態のキューから取得する")
    parsed = parse_frontmatter(text)
    if parsed is None or parsed[0].get("type") != "uwi" or parsed[0].get("submitter_session") != session_id:
        raise ValueError("現在の会話が投入したUWIを指定する")
    if uwi_scan.is_uwi_answered(text):
        raise ValueError("未回答のUWIを指定する")
    if "quote" in document and document["quote"] != text:
        raise ValueError("quoteは指定した未回答UWIの全文と一致させるか省略する")
    return path


def record_decision(document: dict[str, Any]) -> str:
    session_id = document.get("session_id")
    action = document.get("action")
    if (
        not isinstance(session_id, str)
        or not session_id
        or action not in {"start", "cancel", "replace", "resume", "wait", "blocked", "publish-scope"}
    ):
        raise ValueError("session_idと受理する判断のactionを指定する")
    if not agent_id.is_main_agent_context({"agent_id": document.get("agent_id", "main")}):
        raise ValueError("終了工程の判断はメインが記録する")
    owner = _delegated_session.owner_session_id(os.environ)
    if owner and owner != session_id:
        raise ValueError("現在の会話のsession_idを指定する")
    if not isinstance(document.get("reason"), str) or not document["reason"].strip():
        raise ValueError("判断の理由を指定する")
    selected = ""

    def update(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal selected
        data = _data(state, session_id)
        _sync_queued_inputs(data, None)
        evidence: dict[str, Any] = {}
        if action == "wait":
            target = document.get("target_session_id")
            child = agents_server_observations.session_record(state, target)
            work_for_wait = data["works"].get(document.get("work_id"), {})
            if target is not None:
                if not isinstance(target, str) or target not in work_for_wait.get("async_targets", {}):
                    raise ValueError(f"委譲先待機の対象が作業に対応しません: {target}。実際のwork_idと対象sessionを指定する")
                if not isinstance(child, dict) or child.get("owner_agent_id") != "main":
                    raise ValueError(
                        f"委譲先待機の対象を所有していません: {target}。この作業が起動した所有済みの対象を指定する"
                    )
                active = agents_server_observations.actively_waited_session_ids([target])
                if child.get("pending_observation") is not True and target not in active:
                    raise _InactiveWaitError(f"対象sessionの有効なCLI待機がありません: {target}")
                evidence["target_session_id"] = target
            else:
                evidence["uwi_file"] = str(_waiting_uwi(document, session_id))
        elif action == "blocked":
            call_id = document.get("call_id")
            records = data["calls"].get(call_id)
            if (
                not isinstance(records, list)
                or len(records) != 1
                or records[0].get("kind") != "failure"
                or records[0].get("work_id") != document.get("work_id")
            ):
                raise _FailureEvidenceError("対象作業の実際の失敗call_idを指定する")
            if (
                "quote" in document
                and json.dumps(records[0]["response"], ensure_ascii=False, sort_keys=True) != document["quote"]
            ):
                raise ValueError("quoteは実際の失敗応答全体のJSONと一致させるか省略する")
            evidence["call_id"] = call_id
        elif action == "publish-scope" and document.get("origin") == "standing-authorization":
            policy = document.get("policy_file")
            reference = data["inputs"].get(document.get("input_id"))
            if not isinstance(reference, dict) or reference.get("human") is not True:
                raise ValueError("適用済み認可を使う作業の人間入力のinput_idを渡す")
            if not isinstance(policy, str) or not pathlib.Path(policy).is_absolute():
                raise ValueError("適用済み認可の実在するpolicy_fileを絶対パスで渡す")
            text = pathlib.Path(policy).read_text(encoding="utf-8")
            if "quote" in document and text != document["quote"]:
                raise ValueError("quoteは適用済み認可の全文と一致させるか省略する")
            evidence = {"policy_file": policy, "input_id": document.get("input_id"), "policy_text": text}
        else:
            reference = data["inputs"].get(document.get("input_id"))
            if not isinstance(reference, dict) or reference.get("human") is not True:
                raise ValueError("原入力のinput_idを渡す。生成入力は人間の判断の根拠にできない")
            if "quote" in document and reference.get("text") != document["quote"]:
                raise ValueError("quoteは指定した原入力の全文と一致させるか省略する")
            evidence["input_id"] = document["input_id"]
        if action == "publish-scope":
            if document.get("scope") not in {
                "既存の判断基準どおり",
                "push・CIまで",
                "commitまで",
                "commitしない",
            } or document.get("origin") not in {"instruction", "answer", "standing-authorization"}:
                raise ValueError("公開範囲のscopeと原入力のoriginを指定する")
            selected = document.get("work_id", "")
            if selected:
                work = data["works"].get(selected)
                if not isinstance(work, dict):
                    raise ValueError("実在するwork_idを指定する")
            else:
                selected, work = _new_work(data, document.get("input_id", data.get("last_input", "")))
            work["publish_scope"] = {
                "scope": document["scope"],
                "origin": document["origin"],
                "reason": document["reason"],
                **evidence,
            }
            work["input_at_last_call"] = data.get("last_human_input", data.get("last_input"))
        elif action == "start":
            selected, work = _new_work(data, document["input_id"])
        else:
            selected = document.get("work_id", "")
            work = data["works"].get(selected)
            if not isinstance(work, dict):
                raise ValueError("実在するwork_idを指定する")
        if action != "publish-scope":
            work["decisions"].append(
                {
                    "action": action,
                    **{key: value for key, value in evidence.items() if key != "policy_text"},
                    "reason": document["reason"],
                }
            )
        if action == "resume":
            data["current_work"] = selected
        _compact(data)
        return state

    if not session_state.update_state(session_id, update):
        raise ValueError("判断の証拠を読み書きできない。対象の実体と状態の保存先を確かめる")
    return selected
