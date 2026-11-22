"""具体的な実行要求とユーザー回答の一致を確かめる。保存の排他は呼出側が所有する。"""

import hashlib
import json
import re
from typing import Any, TypeGuard


def fingerprint(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_request(value: Any) -> TypeGuard[dict[str, Any]]:
    if not isinstance(value, dict):
        return False
    required = ("request_id", "tool_name", "cwd", "target_repo", "scope", "allow_choice")
    if any(not isinstance(value.get(key), str) or not value[key].strip() for key in required):
        return False
    if not isinstance(value.get("tool_input"), dict):
        return False
    if value["allow_choice"].removesuffix(" (Recommended)") not in (
        "許可する",
        "今回の操作を許可する",
        "偽陽性として実行を承認する",
    ):
        return False
    try:
        fingerprint(value)
    except TypeError, ValueError:
        return False
    return True


def request_display(request: dict[str, Any]) -> str:
    return json.dumps(request, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2)


def parse_request(value: Any) -> dict[str, Any] | None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    return value if isinstance(value, dict) and validate_request(value) else None


def operation_limit(request: dict[str, Any]) -> int | None:
    scope = request.get("scope")
    if scope == "once":
        return 1
    matched = re.fullmatch(r"count:([1-9][0-9]{0,5})", scope) if isinstance(scope, str) else None
    return int(matched.group(1)) if matched else None


def legacy_request(
    metadata: dict[str, Any],
    question: str,
    answer: str,
    requests: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if "permission_request" in metadata or "legacy_operation_approval" in metadata:
        return None
    if answer.strip().removesuffix(" (Recommended)") not in {
        "許可する",
        "今回の操作を許可する",
        "実行を許可する",
        "偽陽性として実行を承認する",
    }:
        return None
    choices = metadata.get("choices")
    if isinstance(choices, str):
        choices = [choice.strip() for choice in choices.split(",")]
    if not isinstance(choices, list) or choices.count(answer.strip()) != 1:
        return None

    def quoted(value: str) -> bool:

        return (
            f"`{value}`" in question
            or json.dumps(value, ensure_ascii=False) in question
            or any(f"```{language}\n{value}\n```" in question for language in ("", "sh", "bash", "javascript", "js"))
        )

    matched: dict[str, dict[str, Any]] = {}
    for request in requests:
        if not validate_request(request) or request["scope"] != "once":
            continue
        inputs = request["tool_input"]
        field = {"Bash": "command", "Read": "file_path", "REPL": "code"}.get(request["tool_name"])
        if field is None or set(inputs) - {field, "description"} or not isinstance(inputs.get(field), str):
            continue
        if (
            quoted(request["tool_name"])
            and quoted(inputs[field])
            and (quoted(request["cwd"]) or quoted(request["target_repo"]))
        ):
            matched[fingerprint(request)] = request
    return next(iter(matched.values())) if len(matched) == 1 else None


def _remaining(approval: dict[str, Any], limit: int) -> int | None:
    value = approval.get("remaining")
    if not isinstance(value, str) or re.fullmatch(r"0|[1-9][0-9]{0,5}", value) is None:
        return None
    remaining = int(value)
    return remaining if remaining <= limit else None


def bind_answer(metadata: dict[str, Any], question: str, answer: str) -> None:
    previous = metadata.pop("operation_approval", None)

    def invalidate() -> None:
        if isinstance(previous, dict):
            metadata["operation_approval"] = {**previous, "state": "revoked"}

    request = parse_request(metadata.get("permission_request"))
    if request is None:
        invalidate()
        return
    choices = metadata.get("choices")

    if isinstance(choices, str):
        choices = [choice.strip() for choice in choices.split(",")]
    if not isinstance(choices, list) or choices.count(request["allow_choice"]) != 1:
        invalidate()
        return
    if (
        answer.strip().removesuffix(" (Recommended)") != request["allow_choice"].removesuffix(" (Recommended)")
        or request_display(request) not in question
    ):
        invalidate()
        return
    approval: dict[str, Any] = {
        "request_hash": fingerprint(request),
        "answer_hash": fingerprint(answer),
        "question_hash": fingerprint(question),
        "state": "approved",
    }
    limit = operation_limit(request)
    if limit is not None and limit > 1:
        approval["remaining"] = str(limit)

    if isinstance(previous, dict) and previous.get("request_hash") == approval["request_hash"]:
        approval["state"] = previous.get("state", "invalid")
        if limit is not None and limit > 1:
            remaining = _remaining(previous, limit)
            if remaining is not None:
                approval["remaining"] = str(remaining)
            else:
                approval["state"] = "invalid"
    metadata["operation_approval"] = approval


def matches(metadata: dict[str, Any], question: str, answer: str, operation: dict[str, Any]) -> bool:
    candidate = dict(metadata)
    bind_answer(candidate, question, answer)
    approval = candidate.get("operation_approval")
    limit = operation_limit(operation)
    return (
        validate_request(operation)
        and limit is not None
        and isinstance(approval, dict)
        and approval == metadata.get("operation_approval")
        and approval.get("state") == "approved"
        and approval.get("request_hash") == fingerprint(operation)
        and (limit == 1 or (_remaining(approval, limit) or 0) > 0)
    )


def consume(metadata: dict[str, Any], question: str, answer: str, operation: dict[str, Any]) -> bool:
    """一致する許可を消費する。呼出側は同じ保存排他内で永続化する。"""
    limit = operation_limit(operation)
    if limit is None or not matches(metadata, question, answer, operation):
        return False
    approval = {**metadata["operation_approval"]}
    if limit > 1:
        remaining = _remaining(approval, limit)
        if remaining is None or remaining <= 0:
            return False
        approval["remaining"] = str(remaining - 1)
        if remaining == 1:
            approval["state"] = "consumed"
    else:
        approval["state"] = "consumed"
    metadata["operation_approval"] = approval
    return True
