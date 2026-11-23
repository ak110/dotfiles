from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from agent_toolkit._agents_server.runtime_result_evidence import has_runtime_evidence
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._agents_server.tool_descriptions import FREEFORM_RESULT_CONTRACT_KINDS
from agent_toolkit._agents_server.turn_state import ResultContractWait
from agent_toolkit._common import task_documents
from agent_toolkit._common.next_action import ActionableError

_REASON_PREFIX = "続行できない理由:"
_CLASSIFICATION_PREFIX = "続行できない区分:"

_STATE_HEAD = "状態: completed"


def is_wait_statement(message: object) -> bool:
    """既存の待機表明だけを、通常の終了結果と区別する。"""
    return isinstance(message, str) and re.fullmatch(r"待機中: [^\r\n]+", message.strip()) is not None


RECOVERY_NEXT_ACTION = (
    "agents_server.send_messageのsession_idにこの結果のsession_idを指定し、promptで同じsessionへ、"
    "診断に従い、正常返却は所定の成功行、続行不能は同じインデントの有効な区分と非空の理由の"
    "連続する2行を用い、成果と追加報告を保持して再提出するよう指示する。"
    "送信できない場合は、session_id・返却本文・診断を委譲元へ報告する。"
)
_VALID_CLASSIFICATIONS = frozenset(
    {
        "user_decision",
        "authorization",
        "owner_input",
        "inaccessible_input",
        "permission_denied",
        "external_wait",
        "runtime_unavailable",
    }
)


def _visible_result_lines(message: str) -> list[str]:
    lines: list[str] = []
    fence = ""
    for line in message.splitlines():
        match = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if match and match[1].startswith(fence) and not match[2].strip():
                fence = ""
            lines.append("")
        elif match:
            fence = match[1]
            lines.append("")
        else:
            lines.append(line)
    return lines


def invalid_result_reason(session: SessionState, result: dict[str, Any]) -> str | None:
    if result.get("status") != "completed":
        return None
    message = result.get("agent_message")
    if isinstance(message, str) and session.launch_kind in FREEFORM_RESULT_CONTRACT_KINDS:
        unavailable = re.finditer(
            r"(?m)^(?P<indent>[ \t]*)続行できない区分:[ \t]*runtime_unavailable[ \t]*\r?\n"
            r"(?P=indent)続行できない理由:[ \t]*(?P<reason>[^\r\n]*)",
            message,
        )
        visible_lines = _visible_result_lines(message)
        for claim in unavailable:
            if not visible_lines[len(message[: claim.start()].splitlines())]:
                continue
            reason_parts = [claim["reason"]]

            for line in message[claim.end() :].splitlines():
                if not line.strip():
                    continue
                if not (line.startswith(claim["indent"] + " ") or line.startswith(claim["indent"] + "- ")):
                    break
                reason_parts.append(line.strip())
            if not has_runtime_evidence(session, "\n".join(reason_parts)):
                return (
                    "runtime_unavailableの理由に対応する当turnの実行診断を確認できません。"
                    "続けられる作業を続けるか、当turnの失敗した呼出しの結果から"
                    "空白正規化後24文字以上の逐語診断を引用して再提出してください。"
                    "予算・時間枠・ツール回数枠の自己推定は実行不能の証拠になりません"
                )
    document = task_documents.launched_task_document(session.prompt)
    freeform_checked = session.launch_kind in FREEFORM_RESULT_CONTRACT_KINDS and document is None
    if not freeform_checked and (document is None or not task_documents.is_agent_toolkit_task_document(document)):
        return None
    if not isinstance(message, str):
        return "委譲の返却本文が必要です"
    if is_wait_statement(message):
        return None
    lines = _visible_result_lines(message)
    has_reason = False
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(?P<indent>\s*)" + re.escape(_REASON_PREFIX) + r".*", line)
        if match is None:
            continue
        has_reason = True
        if not line.removeprefix(match.group("indent") + _REASON_PREFIX).strip() and (
            document is None or document.name != "reader-fit-review.subagent.md"
        ):
            return f"{index + 1}行目の続行できない理由が空です"
        if index == 0:
            return f"{index + 1}行目の続行できない理由の直前に同じインデントの続行できない区分がありません"
        expected = match.group("indent") + _CLASSIFICATION_PREFIX
        previous = lines[index - 1]
        if not previous.startswith(expected):
            return f"{index + 1}行目の続行できない理由の直前に同じインデントの続行できない区分がありません"
        classification = previous.removeprefix(expected).strip()
        if classification not in _VALID_CLASSIFICATIONS:
            return f"{index}行目の続行できない区分が閉集合外です: {classification}"
    if freeform_checked and not has_reason:
        lines = "\n".join(lines).rstrip().splitlines()
        if not lines or _STATE_HEAD not in (lines[0].rstrip(" \t"), lines[-1]):
            return f"{session.launch_kind}の返却が完了を示しません。先頭または最終行に状態: completedが必要です"
    if document is not None and document.name == "reader-fit-review.subagent.md":
        return _reader_fit_reason(session, document, message)
    if document is not None and not has_reason:
        return _role_head_reason(document, message)
    return None


def _role_head_reason(document: pathlib.Path, message: str) -> str | None:
    declaration = task_documents.read_declaration(document)
    heads = (
        _STATE_HEAD,
        *(declaration.result_heads if isinstance(declaration, task_documents.TaskDocumentDeclaration) else ()),
    )
    for line in _visible_result_lines(message):
        stripped = line.strip().removeprefix("- ")
        if any(_head_matches(stripped, head) for head in heads):
            return None
    accepted = "、".join(f"`{head}`" for head in heads)
    return (
        "役割文書の返却形式の固定行も、続行できない区分と理由の2行もありません。"
        f"担当内の作業を完遂して固定行（{accepted}）で返すか、続行不能の単位を区分と理由の2行で返してください"
    )


def _head_matches(line: str, head: str) -> bool:
    if head.endswith(":"):
        return line.startswith(head)
    if head.startswith("状態:"):
        return line == head
    return line == head or line.startswith(head + "（")


def _current_named_input(session: SessionState, document: pathlib.Path, name: str) -> str:
    declaration = task_documents.read_declaration(document)
    instructions = (
        {item.instruction for item in declaration.continuations if name in item.accepted}
        if isinstance(declaration, task_documents.TaskDocumentDeclaration)
        else set()
    )
    for followup in reversed(session.followup_prompts):
        lines = followup.strip().splitlines()

        if lines and lines[0] in instructions:
            params = task_documents.parse_named_inputs(lines[1:])
            if name in params:
                return params[name]
    return task_documents.task_prompt_inputs(session.prompt)[name]


_MULTILINE_VALUE_HINT = "値は欄名と同じ行の`:`の後に書くか、次行へ3文字以上字下げするか先頭2空白の`  - `で続けてください"

_READER_FIT_FIELD = re.compile(r"^  ([^\s:-][^:\n]*):(?: (.*))?$")


def _reader_fit_fields(item_lines: list[str]) -> list[tuple[str, str, str]]:
    entries: list[tuple[str, str, str]] = []
    for line in item_lines:
        field = _READER_FIT_FIELD.match(line)
        if field is not None:
            inline = field[2] or ""
            entries.append((field[1], inline, inline))
        elif entries and line.strip() and (line.startswith("   ") or line.startswith("  - ")):
            key, inline, value = entries[-1]
            entries[-1] = (key, inline, value + "\n" + line.strip())
    return entries


def _reader_fit_reason(session: SessionState, document: pathlib.Path, message: str) -> str | None:
    try:
        value = _current_named_input(session, document, "成果物").strip()
        targets = json.loads(value) if value.startswith("[") else [value]
    except (ActionableError, KeyError, json.JSONDecodeError) as error:
        return f"reader-fitの入力成果物を復元できません: {error}"
    if not isinstance(targets, list) or not targets or any(not isinstance(path, str) or not path for path in targets):
        return "reader-fitの入力成果物が空でないパス配列ではありません"
    if len(set(targets)) != len(targets):
        return "reader-fitの入力成果物に重複があります"

    message = "\n".join(line.rstrip() for line in message.splitlines())
    blocks = list(re.finditer(r"(?m)^- 成果物: (.+)$", message))
    paths = [match[1] for match in blocks]
    if len(paths) != len(set(paths)):
        return "reader-fitの返却成果物に重複があります"
    if set(paths) != set(targets):
        missing = sorted(set(targets) - set(paths))
        extra = sorted(set(paths) - set(targets))
        return f"reader-fitの対象集合が一致しません。欠落: {missing}、依頼外: {extra}。各成果物を個別に返してください"
    blocked = False
    for index, match in enumerate(blocks):
        block = message[match.end() : blocks[index + 1].start() if index + 1 < len(blocks) else len(message)]

        item_lines = []
        for line in block.splitlines():
            if line.strip() and not line.startswith("  "):
                break
            item_lines.append(line)
        entries = _reader_fit_fields(item_lines)
        if len(entries) != len({key for key, _, _ in entries}):
            return f"{match[1]}: 対象別の項目に重複があります"

        inline = {key: value for key, value, _ in entries}
        values = {key: value for key, _, value in entries}
        state = inline.get("完了状態")
        if state not in {"completed", "blocked"}:
            return f"{match[1]}: 完了状態はcompletedまたはblockedを1つ記載してください"
        for field in ("比較資料", "根拠"):
            if not values.get(field, "").strip():
                return f"{match[1]}: {field}が不足しています。{_MULTILINE_VALUE_HINT}"
        if state == "completed" and not values.get("判定", "").strip():
            return f"{match[1]}: 完了した対象の判定が不足しています。{_MULTILINE_VALUE_HINT}"
        if state == "completed" and ("続行できない区分" in inline or "続行できない理由" in inline):
            return f"{match[1]}: 続行不能の対象は完了状態をblockedにしてください"
        if state == "blocked":
            blocked = True
            if inline.get("続行できない区分") not in _VALID_CLASSIFICATIONS or not values.get("続行できない理由", "").strip():
                return f"{match[1]}: blockedには有効な区分と非空の理由が必要です。{_MULTILINE_VALUE_HINT}"
    overall = re.findall(r"(?m)^状態: (.+)$", message)
    if blocked and overall:
        return "reader-fitの個別未完了対象がある返却へ全体の状態: completedを付けないでください"
    if not blocked and overall != ["completed"]:
        return "reader-fitの全対象完了時には全体の状態: completedが1行必要です"
    return None


def hold_invalid_result(session: SessionState, result: dict[str, Any], *, allow_hold: bool = True) -> bool:
    reason = invalid_result_reason(session, result)
    if reason is None:
        message = result.get("agent_message")
        document = task_documents.launched_task_document(session.prompt)
        role = document is not None and task_documents.is_agent_toolkit_task_document(document)
        if (
            (session.launch_kind != "explore" and not role)
            or result.get("status") != "completed"
            or not isinstance(message, str)
            or not any(line.lstrip().startswith(_REASON_PREFIX) for line in _visible_result_lines(message))
            or session.result_contract_recovery_consumed
        ):
            return False
        reason = "不足の前提を再確認し、担当内で補える残工程を完遂してください"
    if not allow_hold or session.result_contract_recovery_consumed:
        error = result.get("error")
        details = dict(error) if isinstance(error, dict) else {}
        details["resultContract"] = reason
        if allow_hold:
            details["nextAction"] = RECOVERY_NEXT_ACTION
            result["status"] = "failed"
        result["error"] = details
        return False
    session.hold_result(ResultContractWait(result, reason))
    session.status = "running"
    session.record_turn_completed()
    session.touch()
    return True
