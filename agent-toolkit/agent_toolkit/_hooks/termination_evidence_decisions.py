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
lazy from agent_toolkit._git import worktrees as git_worktrees
lazy from agent_toolkit._hooks import agent_id, agents_server_observations
lazy from agent_toolkit._hooks.termination_evidence_operations import _git_root
lazy from agent_toolkit._hooks.termination_evidence_state import (
    _compact,
    _data,
    _FailureEvidenceError,
    _InactiveWaitError,
    _new_work,
    _sync_queued_inputs,
    _UnresolvedRepositoryError,
)


def missing_completion(work: dict[str, Any], *, context_loaded: bool | None = None) -> list[str]:
    """文脈の読込証拠と有効な省略を欠く成果作業を返す。

    `None`は従来の直接呼出側として作業別の証拠を使う。明示した偽では圧縮前の作業別証拠を再利用しない。
    """
    if context_loaded is True or (context_loaded is None and work.get("completion_report")):
        return []
    omission = work.get("omit_completion")
    if isinstance(omission, dict) and omission.get("input_id") == work.get("input_at_last_call"):
        try:
            if _omission_policy(omission, work) == omission.get("policy_text"):
                return []
        except OSError, UnicodeError, ValueError:
            pass
    return list(work.get("deliverables", []))


def _omission_policy(document: dict[str, Any], work: dict[str, Any], *, unresolved_labels: list[str] | None = None) -> str:
    """観測した作業の単一リポジトリ内の規範を読み、対象への適用範囲を確かめる。"""
    target = document.get("target_repo")
    policy = document.get("policy_file")
    if not isinstance(target, str) or not pathlib.Path(target).is_absolute():
        raise ValueError("省略する作業のtarget_repoを絶対パスで渡す")
    repository = pathlib.Path(target).resolve(strict=True)
    observed = set(work.get("repositories", [])) | set(work.get("edits", {}))
    if None in observed:
        raise _UnresolvedRepositoryError(
            "対象リポジトリを特定できなかった成果作業: " + "、".join(unresolved_labels or ["種類の内訳を保持しない旧記録"])
        )
    if not work.get("deliverables") or observed != {str(repository)} or _git_root(str(repository)) != repository:
        raise ValueError("省略は観測済みの成果作業が属する単一リポジトリのrootを指定する")
    if not isinstance(policy, str) or not pathlib.Path(policy).is_absolute():
        raise ValueError("プロジェクト規範のpolicy_fileを絶対パスで渡す")
    path = pathlib.Path(policy).resolve(strict=True)
    owner = git_worktrees.owning_worktree(path, git_worktrees.list_worktrees(repository, timeout=5))
    if owner is None or owner.path.resolve() != repository:
        raise ValueError("policy_fileは対象リポジトリ内の適用するプロジェクト規範を指定する")
    return path.read_text(encoding="utf-8")


def _waiting_uwi(document: dict[str, Any], session_id: str) -> pathlib.Path:
    """現在の会話が投入した実在の未回答UWIを確かめる。"""
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
    """実在する原入力と作用対象を持つ判断を記録し、対象作業の識別子を返す。"""
    session_id = document.get("session_id")
    action = document.get("action")
    if (
        not isinstance(session_id, str)
        or not session_id
        or action not in {"start", "cancel", "replace", "resume", "wait", "blocked", "publish-scope", "omit-completion"}
    ):
        raise ValueError("session_idと受理する判断のactionを指定する")
    if not agent_id.is_main_agent_context({"agent_id": document.get("agent_id", "main")}):
        raise ValueError("終了工程の判断はメインが記録する")
    owner = _delegated_session.owner_session_id(os.environ)
    if owner and owner != session_id:
        raise ValueError("現在の会話のsession_idを指定する")
    if action == "omit-completion":
        current_session = os.environ.get("CODEX_THREAD_ID") or os.environ.get("CLAUDE_CODE_SESSION_ID")
        if not agent_id.is_main_agent_context({}) or current_session != session_id:
            raise ValueError("省略の判断は現在の会話のメインが記録する")
    if not isinstance(document.get("reason"), str) or not document["reason"].strip():
        raise ValueError("判断の理由を指定する")
    selected = ""

    def update(state: dict[str, Any]) -> dict[str, Any]:
        nonlocal selected
        data = _data(state, session_id)
        _sync_queued_inputs(data, None)
        evidence: dict[str, Any] = {}
        if action == "omit-completion":
            work = data["works"].get(document.get("work_id"))
            input_id = document.get("input_id", data.get("last_human_input"))
            reference = data["inputs"].get(input_id)
            if not isinstance(work, dict):
                raise ValueError("実在するwork_idを指定する")
            if (
                not isinstance(reference, dict)
                or reference.get("human") is not True
                or input_id != data.get("last_human_input")
                or input_id != work.get("input_at_last_call")
            ):
                raise ValueError("省略する観測済み作業の現在の人間入力のinput_idを渡す")
            unresolved_labels = list(
                dict.fromkeys(
                    label
                    for call_id in work.get("calls", [])
                    for record in data["calls"].get(call_id, [])
                    if record.get("kind") == "deliverable" and record.get("repository") is None
                    for label in record["labels"]
                )
            )
            text = _omission_policy(document, work, unresolved_labels=unresolved_labels)
            if "quote" in document and text != document["quote"]:
                raise ValueError("quoteは適用するプロジェクト規範の全文と一致させるか省略する")
            evidence = {
                "input_id": input_id,
                "policy_file": document["policy_file"],
                "target_repo": document["target_repo"],
                "policy_text": text,
            }
        elif action == "wait":
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
        if action == "omit-completion":
            work["omit_completion"] = evidence
        else:
            work.pop("omit_completion", None)
        if action == "resume":
            data["current_work"] = selected
        _compact(data)
        return state

    if not session_state.update_state(session_id, update):
        raise ValueError("判断の証拠を読み書きできない。対象の実体と状態の保存先を確かめる")
    return selected
