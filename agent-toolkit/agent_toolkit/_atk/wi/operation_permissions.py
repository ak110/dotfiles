from __future__ import annotations

import dataclasses
import datetime
import functools
import json
import os
import pathlib
import re
import secrets
import subprocess
from typing import Any

from agent_toolkit._common import delegated_session, function_hook_requests, operation_approvals
from agent_toolkit._common.session_state import read_state, update_state
from agent_toolkit._git import remote as git_remote
lazy from agent_toolkit._atk.wi import entries, frontmatter, sync, uwi_scan
lazy from agent_toolkit._atk.wi.constants import WI_ACTIVE_STATES
lazy from agent_toolkit._atk.wi.uwi import ANSWER_MARKER
lazy from agent_toolkit._common import approval_classifier

_PENDING = "operation_permission_denials"
_DIRECT = "direct_operation_approvals"
_APPLIED = "operation_permission_results"
_LIMIT = 64


@dataclasses.dataclass(frozen=True)
class Authorization:
    allow: bool = False
    context: str = ""


def _call(payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    tool = payload.get("tool_name")
    inputs = payload.get("tool_input")
    cwd = payload.get("cwd")
    if not isinstance(tool, str) or not tool or not isinstance(inputs, dict) or not isinstance(cwd, str):
        return None
    if not pathlib.Path(cwd).is_absolute():
        return None
    try:
        url = git_remote.origin_url(cwd, timeout=5)
        repo = git_remote.normalize_remote_url(url) if url else None
    except OSError, ValueError, subprocess.SubprocessError:
        return None
    if repo is None:
        return None
    operation = {"tool_name": tool, "tool_input": inputs, "cwd": str(pathlib.Path(cwd).resolve()), "target_repo": repo}
    try:
        return operation_approvals.fingerprint(operation), operation
    except TypeError, ValueError:
        return None


def observe_denial(payload: dict[str, Any]) -> None:
    """ホストが返した拒否だけを記録する。記録したことを再試行の許可にはしない。"""
    if payload.get("hook_event_name") != "PermissionDenied":
        return
    session = payload.get("session_id")
    reason = payload.get("reason")
    tool_use_id = payload.get("tool_use_id")
    call = _call(payload)
    if (
        not isinstance(session, str)
        or not session
        or not isinstance(reason, str)
        or call is None
        or not isinstance(tool_use_id, str)
        or not tool_use_id
    ):
        return
    key, operation = call
    classified = approval_classifier.inspect(reason, cwd=operation["cwd"])

    def save(state: dict) -> dict:
        pending = state.get(_PENDING)
        pending = dict(pending) if isinstance(pending, dict) else {}
        previous = pending.get(key)
        request = previous.get("request") if isinstance(previous, dict) else None
        incomplete_revocation = isinstance(previous, dict) and previous.get("revocation_incomplete") is True
        if (
            not operation_approvals.validate_request(request)
            or isinstance(previous, dict)
            and previous.get("revoked") is True
            and not incomplete_revocation
        ):
            request = {
                **operation,
                "request_id": secrets.token_hex(16),
                "scope": "once",
                "allow_choice": "許可する (Recommended)",
                "source_session": session,
            }
        pending[key] = {
            "request": request,
            "reason": reason,
            "tool_use_id": payload.get("tool_use_id"),
            "classifier_version": classified.version,
            "classifier_config_hash": classified.config_hash,
            "soft_verdict": classified.eligible,
            "classification": classified.diagnostic,
        }
        if incomplete_revocation:
            pending[key].update(revoked=True, revocation_incomplete=True)
        state[_PENDING] = dict(list(pending.items())[-_LIMIT:])
        return state

    update_state(session, save)


def pending_requests(session_id: str) -> list[dict[str, Any]]:
    values = read_state(session_id).get(_PENDING)
    return [value for value in values.values() if isinstance(value, dict)] if isinstance(values, dict) else []


def _remember_use(state: dict, key: str, payload: dict, request: dict, *, source: str, ongoing: bool) -> None:
    pending = state.get(_PENDING)
    if isinstance(pending, dict) and not ongoing:
        pending.pop(key, None)
    results = state.get(_APPLIED)
    results = dict(results) if isinstance(results, dict) else {}
    results[payload["tool_use_id"]] = {
        "request_id": request["request_id"],
        "source": source,
        "request": request,
        "tool_use_id": payload.get("tool_use_id"),
        "tool_name": payload.get("tool_name"),
        "input_hash": operation_approvals.fingerprint(payload.get("tool_input")),
        "status": "authorized",
    }
    state[_APPLIED] = dict(list(results.items())[-_LIMIT:])


def observe_answer(payload: dict[str, Any]) -> None:
    if payload.get("tool_name") != "AskUserQuestion" or payload.get("hook_event_name") != "PostToolUse":
        return
    inputs = payload.get("tool_input")
    response = payload.get("tool_response")
    session = payload.get("session_id")
    if not isinstance(inputs, dict) or "answers" in inputs or not isinstance(response, dict):
        return
    if not isinstance(session, str) or not session or response.get("afkTimeoutMs") is not None:
        return
    questions, answers = inputs.get("questions"), response.get("answers")
    if not isinstance(questions, list) or not isinstance(answers, dict):
        return

    def save(state: dict) -> dict | None:
        pending = state.get(_PENDING)
        if not isinstance(pending, dict):
            return None
        direct = state.get(_DIRECT)
        direct = dict(direct) if isinstance(direct, dict) else {}
        changed = False
        for key, denial in pending.items():
            request = denial.get("request") if isinstance(denial, dict) else None
            if not operation_approvals.validate_request(request):
                continue
            for question in questions:
                if not isinstance(question, dict) or not isinstance(question.get("question"), str):
                    continue
                text = question["question"]
                answer = answers.get(text)
                if not isinstance(answer, str) or operation_approvals.request_display(request) not in text:
                    continue
                options = question.get("options")
                choices = (
                    [option.get("label") for option in options if isinstance(option, dict)] if isinstance(options, list) else []
                )
                metadata = {"permission_request": request, "choices": choices}
                old = direct.get(key)
                if isinstance(old, dict) and isinstance(old.get("metadata"), dict):
                    metadata["operation_approval"] = old["metadata"].get("operation_approval")
                operation_approvals.bind_answer(metadata, text, answer)
                direct[key] = {"metadata": metadata, "question": text, "answer": answer}
                changed = True
        if changed:
            state[_DIRECT] = dict(list(direct.items())[-_LIMIT:])
            return state
        return None

    update_state(session, save)


def _actual_request(request: object, operation: dict[str, Any]) -> dict[str, Any] | None:
    request = operation_approvals.parse_request(request)
    if request is None or operation_approvals.operation_limit(request) is None:
        return None
    return {**request, **operation}


def pretooluse(payload: dict[str, Any]) -> Authorization | None:
    if payload.get("agent_id") is not None or delegated_session.is_delegated(os.environ):
        return None
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        return None
    if not isinstance(payload.get("tool_use_id"), str) or not payload["tool_use_id"]:
        return None
    state = read_state(session)
    pending = state.get(_PENDING)
    if not isinstance(pending, dict) or not any(
        isinstance(value, dict)
        and isinstance(value.get("request"), dict)
        and value["request"].get("tool_name") == payload.get("tool_name")
        and value["request"].get("tool_input") == payload.get("tool_input")
        for value in pending.values()
    ):
        return None
    call = _call(payload)
    if call is None:
        return None
    key, operation = call
    denial = pending.get(key)
    if not isinstance(denial, dict):
        return None
    reason = denial.get("reason")
    denied_request = denial.get("request")
    if (
        not isinstance(reason, str)
        or not isinstance(denied_request, dict)
        or not operation_approvals.validate_request(denied_request)
        or denial.get("soft_verdict") is not True
        or denial.get("revoked") is True
    ):
        return None
    direct = state.get(_DIRECT)
    saved = direct.get(key) if isinstance(direct, dict) else None
    if (
        isinstance(saved, dict)
        and isinstance(saved.get("metadata"), dict)
        and isinstance(saved.get("question"), str)
        and isinstance(saved.get("answer"), str)
    ):
        request = _actual_request(saved["metadata"].get("permission_request"), operation)
        if (
            request is not None
            and operation_approvals.fingerprint(request) == operation_approvals.fingerprint(denied_request)
            and operation_approvals.matches(saved["metadata"], saved["question"], saved["answer"], request)
        ):
            classified = approval_classifier.inspect(reason, cwd=operation["cwd"])
            if not classified.eligible:
                return Authorization(context=classified.diagnostic)
            if classified.config_hash != denial.get("classifier_config_hash"):
                return Authorization(context="拒否後に実効設定が変わったため、保存済み承認を自動適用しない。")
            if (
                function_hook_requests.interactive_available(
                    session,
                    expected_version=classified.version,
                    context_check=functools.partial(approval_classifier.native_settings_match, classified),
                )
                is not True
            ):
                return Authorization(
                    context="対話画面・対象のバージョン・実効autoModeの一致を確認できない。通常の権限確認へ戻す。"
                )
            consumed = False

            def consume(state_now: dict) -> dict | None:
                nonlocal consumed
                direct_now = state_now.get(_DIRECT)
                current = direct_now.get(key) if isinstance(direct_now, dict) else None
                if (
                    not isinstance(current, dict)
                    or not isinstance(current.get("metadata"), dict)
                    or current.get("question") != saved["question"]
                    or current.get("answer") != saved["answer"]
                    or current["metadata"].get("permission_request") != saved["metadata"].get("permission_request")
                ):
                    return None
                consumed = operation_approvals.consume(current["metadata"], current["question"], current["answer"], request)
                if consumed:
                    _remember_use(
                        state_now,
                        key,
                        payload,
                        request,
                        source="direct_answer",
                        ongoing=current["metadata"]["operation_approval"].get("state") == "approved",
                    )
                return state_now if consumed else None

            if update_state(session, consume) and consumed:
                return Authorization(
                    allow=True,
                    context=(
                        f"直接回答の具体的承認と実入力が一致した。Claude Code {classified.version}、"
                        f"softラベル: {', '.join(classified.labels)}。"
                    ),
                )
    return _consume_uwi(
        operation,
        reason,
        expected_request=denied_request,
        session=session,
        key=key,
        payload=payload,
        config_hash=denial.get("classifier_config_hash"),
    )


def _consume_uwi(
    operation: dict[str, Any],
    reason: str,
    *,
    expected_request: dict,
    session: str,
    key: str,
    payload: dict,
    config_hash: str | None,
) -> Authorization | None:
    root = uwi_scan.private_notes_root()
    if root is None:
        return None
    try:
        candidates = list(entries.iter_entries(root, WI_ACTIVE_STATES, operation["target_repo"], "uwi"))
        for path, _repo, text, _state, _kind in candidates:
            parsed = frontmatter.parse_frontmatter(text)
            if parsed is None or ANSWER_MARKER not in parsed[1]:
                continue
            metadata, body = parsed
            request = _actual_request(metadata.get("permission_request"), operation)
            question, answer = body.rsplit(ANSWER_MARKER, 1)
            legacy = "permission_request" not in metadata
            if legacy:
                request = operation_approvals.legacy_request(
                    metadata,
                    question,
                    answer,
                    [item["request"] for item in pending_requests(session) if isinstance(item.get("request"), dict)],
                )
            if (
                request is None
                or operation_approvals.fingerprint(request) != operation_approvals.fingerprint(expected_request)
                or not legacy
                and not operation_approvals.matches(metadata, question, answer, request)
            ):
                continue
            classified = approval_classifier.inspect(reason, cwd=operation["cwd"])
            if not classified.eligible:
                return Authorization(context=classified.diagnostic)
            if classified.config_hash != config_hash:
                return Authorization(context="拒否後に実効設定が変わったため、保存済み承認を自動適用しない。")
            if (
                function_hook_requests.interactive_available(
                    session,
                    expected_version=classified.version,
                    context_check=functools.partial(approval_classifier.native_settings_match, classified),
                )
                is not True
            ):
                return Authorization(
                    context="対話画面・対象のバージョン・実効autoModeの一致を確認できない。通常の権限確認へ戻す。"
                )
            with sync.DEFAULT_STORAGE.mutation(root) as mutation:
                latest = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
                if (
                    latest is None
                    or latest[1] != body
                    or latest[0].get("permission_request") != metadata.get("permission_request")
                    or (legacy and latest != parsed)
                ):
                    return None
                metadata = latest[0]
                if legacy:
                    metadata["legacy_operation_approval"] = {
                        "request_hash": operation_approvals.fingerprint(request),
                        "question_hash": operation_approvals.fingerprint(question),
                        "answer_hash": operation_approvals.fingerprint(answer),
                        "state": "consumed",
                    }
                elif not operation_approvals.consume(metadata, question, answer, request):
                    return None
                metadata["operation_approval_last_use"] = {
                    "at": datetime.datetime.now(datetime.UTC).isoformat(),
                    "session_id": session,
                    "tool_use_id": payload["tool_use_id"],
                }
                frontmatter.write_entry_text(path, frontmatter.serialize_frontmatter(metadata, body))
                mutation.commit("chore: 操作承認を消費する", [str(path.relative_to(root))])
            grant_ongoing = not legacy and metadata["operation_approval"].get("state") == "approved"

            def remember(
                state: dict,
                request_value: dict = request,
                source_name: str = path.name,
                ongoing: bool = grant_ongoing,
            ) -> dict:
                _remember_use(
                    state,
                    key,
                    payload,
                    request_value,
                    source=source_name,
                    ongoing=ongoing,
                )
                return state

            if not update_state(session, remember):
                return Authorization(context="承認の消費を実行記録へ対応付けられないため、通常の権限確認へ戻す。")
            return Authorization(
                allow=True,
                context=(
                    f"UWI {path.name}の具体的承認と実入力が一致した。Claude Code {classified.version}、"
                    f"softラベル: {', '.join(classified.labels)}。"
                ),
            )
    except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
        return Authorization(context="具体的承認を保存して実入力との一致を確認する処理が完了していない。通常の権限確認へ戻す。")
    return None


def observe_result(payload: dict[str, Any]) -> None:
    session = payload.get("session_id")
    event = payload.get("hook_event_name")
    if not isinstance(session, str) or not session or event not in ("PostToolUse", "PostToolUseFailure"):
        return
    try:
        input_hash = operation_approvals.fingerprint(payload.get("tool_input"))
    except TypeError, ValueError:
        return

    def save(state: dict) -> dict | None:
        results = state.get(_APPLIED)
        if not isinstance(results, dict):
            return None
        changed = False
        for result in results.values():
            if not isinstance(result, dict) or result.get("status") != "authorized":
                continue
            if result.get("tool_use_id") != payload.get("tool_use_id") or result.get("input_hash") != input_hash:
                continue
            if result.get("tool_name") != payload.get("tool_name"):
                continue

            result["status"] = "failed" if event == "PostToolUseFailure" else "returned"
            result["finished_at"] = datetime.datetime.now(datetime.UTC).isoformat()
            changed = True
        return state if changed else None

    update_state(session, save)


def prepare_request(session_id: str, request_id: str, *, scope: str = "once") -> dict[str, Any] | None:
    if operation_approvals.operation_limit({"scope": scope}) is None:
        return None
    prepared: dict[str, Any] | None = None

    def save(state: dict) -> dict | None:
        nonlocal prepared
        pending = state.get(_PENDING)
        if not isinstance(pending, dict):
            return None
        for value in pending.values():
            request = value.get("request") if isinstance(value, dict) else None
            if not operation_approvals.validate_request(request) or request["request_id"] != request_id:
                continue
            if value.get("revocation_incomplete") is True:
                return None
            request = {**request, "scope": scope, "request_id": secrets.token_hex(16)}
            value["request"] = request
            value.pop("revoked", None)
            prepared = dict(request)
            return state
        return None

    return prepared if update_state(session_id, save) else None


def adopt_request(session_id: str, source_session: str, request_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", source_session):
        return None
    pending = read_state(source_session).get(_PENDING)
    if not isinstance(pending, dict):
        return None
    for key, denial in pending.items():
        request = denial.get("request") if isinstance(denial, dict) else None
        if not operation_approvals.validate_request(request) or request["request_id"] != request_id:
            continue
        if request.get("source_session") != source_session or denial.get("revoked") is True:
            return None
        accepted = False

        def save(state: dict, request_key: str = key, source_denial: dict = denial) -> dict | None:
            nonlocal accepted
            destination = state.get(_PENDING)
            destination = dict(destination) if isinstance(destination, dict) else {}
            existing = destination.get(request_key)
            if isinstance(existing, dict) and (
                existing.get("reason") != source_denial.get("reason")
                or existing.get("classifier_config_hash") != source_denial.get("classifier_config_hash")
                or existing.get("soft_verdict") is not True
                or existing.get("revoked") is True
            ):
                return None
            if request_key not in destination and len(destination) >= _LIMIT:
                return None
            destination[request_key] = json.loads(json.dumps(source_denial, ensure_ascii=False))
            state[_PENDING] = destination
            accepted = True
            return state

        return dict(request) if update_state(session_id, save) and accepted else None
    return None


def permission_status(session_id: str, *, request_id: str | None = None) -> dict[str, Any]:
    state = read_state(session_id)
    pending = pending_requests(session_id)
    requests = {
        value["request"]["request_id"]: value["request"]
        for value in pending
        if operation_approvals.validate_request(value.get("request"))
    }
    results = state.get(_APPLIED)
    results = list(results.values()) if isinstance(results, dict) else []
    for value in results:
        if isinstance(value, dict) and operation_approvals.validate_request(value.get("request")):
            requests[value["request"]["request_id"]] = value["request"]
    grants: list[dict[str, Any]] = []
    legacy_answers: list[dict[str, Any]] = []
    direct = state.get(_DIRECT)
    for value in direct.values() if isinstance(direct, dict) else ():
        if not isinstance(value, dict) or not isinstance(value.get("metadata"), dict):
            continue
        metadata = value["metadata"]
        request = operation_approvals.parse_request(metadata.get("permission_request"))
        if request is not None:
            trusted = requests.get(request["request_id"])
            valid = (
                isinstance(trusted, dict)
                and isinstance(value.get("question"), str)
                and isinstance(value.get("answer"), str)
                and operation_approvals.fingerprint(request) == operation_approvals.fingerprint(trusted)
                and operation_approvals.matches(metadata, value["question"], value["answer"], trusted)
            )
            grants.append(
                {
                    "request": request,
                    "source": "direct_answer",
                    "approval": metadata.get("operation_approval"),
                    "valid": valid,
                }
            )
    root = uwi_scan.private_notes_root()
    complete = True
    if root is not None:
        try:
            for repo in {request["target_repo"] for request in requests.values()}:
                for path, _repo, text, _state, _kind in entries.iter_entries(root, WI_ACTIVE_STATES, repo, "uwi"):
                    parsed = frontmatter.parse_frontmatter(text)
                    if parsed is None or ANSWER_MARKER not in parsed[1]:
                        continue
                    metadata, body = parsed
                    request = operation_approvals.parse_request(metadata.get("permission_request"))
                    question, answer = body.rsplit(ANSWER_MARKER, 1)
                    if "permission_request" not in metadata and answer.strip():
                        for identifier, trusted in requests.items():
                            if request_id is not None and identifier != request_id:
                                continue
                            inputs = trusted["tool_input"]
                            literal = inputs.get("command", inputs.get("file_path", inputs.get("code")))
                            if (
                                isinstance(literal, str)
                                and f"`{literal}`" in question
                                and f"`{trusted['tool_name']}`" in question
                                and any(f"`{trusted[field]}`" in question for field in ("cwd", "target_repo"))
                            ):
                                legacy_answers.append(
                                    {
                                        "source": path.name,
                                        "request_id": identifier,
                                        **({"question": question, "answer": answer} if request_id is not None else {}),
                                        "consumption": metadata.get("legacy_operation_approval"),
                                    }
                                )
                    if request is not None and request["request_id"] in requests:
                        trusted = requests[request["request_id"]]
                        valid = operation_approvals.fingerprint(request) == operation_approvals.fingerprint(
                            trusted
                        ) and operation_approvals.matches(metadata, question, answer, trusted)
                        grants.append(
                            {
                                "request": request,
                                "source": path.name,
                                "approval": metadata.get("operation_approval"),
                                "valid": valid,
                            }
                        )
        except OSError, ValueError:
            complete = False

    def view(value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        request = operation_approvals.parse_request(value.get("request"))
        if request is None or request_id is not None and request["request_id"] != request_id:
            return None
        if request_id is not None:
            return value
        summary = {
            **value,
            "request": {name: data for name, data in request.items() if name != "tool_input"},
            "details_available": True,
        }
        reason = summary.get("reason")
        if isinstance(reason, str) and len(reason) > 512:
            summary["reason"] = reason[:512]
            summary["reason_truncated"] = True
        return summary

    return {
        "pending": [item for value in pending if (item := view(value)) is not None],
        "approvals": [item for value in grants if (item := view(value)) is not None],
        "results": [item for value in results if (item := view(value)) is not None],
        "legacy_answers": legacy_answers,
        "complete": complete,
    }


def revoke_permission(session_id: str, request_id: str) -> dict[str, bool]:
    status = permission_status(session_id, request_id=request_id)
    known = [value["request"] for value in status["pending"] if operation_approvals.validate_request(value.get("request"))]
    known += [value["request"] for value in status["approvals"] if operation_approvals.validate_request(value.get("request"))]
    known = [request for request in known if request["request_id"] == request_id]
    if not known:
        return {"changed": False, "complete": status["complete"]}
    changed = False

    def save(state: dict) -> dict:
        nonlocal changed
        pending = state.get(_PENDING)
        for value in pending.values() if isinstance(pending, dict) else ():
            request = operation_approvals.parse_request(value.get("request")) if isinstance(value, dict) else None
            if request is not None and request["request_id"] == request_id:
                changed |= value.get("revoked") is not True
                value.update(revoked=True, revocation_incomplete=True)
        direct = state.get(_DIRECT)
        for value in direct.values() if isinstance(direct, dict) else ():
            if not isinstance(value, dict) or not isinstance(value.get("metadata"), dict):
                continue
            metadata = value["metadata"]
            request = operation_approvals.parse_request(metadata.get("permission_request"))
            if (
                request is not None
                and request["request_id"] == request_id
                and isinstance(metadata.get("operation_approval"), dict)
                and metadata["operation_approval"].get("state") != "revoked"
            ):
                metadata["operation_approval"]["state"] = "revoked"
                changed = True
        return state

    if not update_state(session_id, save):
        return {"changed": False, "complete": False}
    root = uwi_scan.private_notes_root()
    if root is not None:
        try:
            with sync.DEFAULT_STORAGE.mutation(root) as mutation:
                paths: list[str] = []
                for repo in {request["target_repo"] for request in known}:
                    for path, _repo, text, _state, _kind in entries.iter_entries(root, WI_ACTIVE_STATES, repo, "uwi"):
                        parsed = frontmatter.parse_frontmatter(text)
                        if parsed is None:
                            continue
                        metadata, body = parsed
                        request = operation_approvals.parse_request(metadata.get("permission_request"))
                        if request is None or request["request_id"] != request_id:
                            continue
                        approval = metadata.get("operation_approval")
                        if isinstance(approval, dict) and approval.get("state") != "revoked":
                            approval["state"] = "revoked"
                            frontmatter.write_entry_text(path, frontmatter.serialize_frontmatter(metadata, body))
                            paths.append(str(path.relative_to(root)))
                if paths:
                    mutation.commit("chore: 操作承認を取り消す", paths)
                    changed = True
        except OSError, ValueError, RuntimeError, subprocess.SubprocessError:
            return {"changed": changed, "complete": False}
    if status["complete"]:

        def finish(state: dict) -> dict:
            pending = state.get(_PENDING)
            for value in pending.values() if isinstance(pending, dict) else ():
                request = operation_approvals.parse_request(value.get("request")) if isinstance(value, dict) else None
                if request is not None and request["request_id"] == request_id:
                    value["revocation_incomplete"] = False
            return state

        if not update_state(session_id, finish):
            return {"changed": changed, "complete": False}
    return {"changed": changed, "complete": status["complete"]}
