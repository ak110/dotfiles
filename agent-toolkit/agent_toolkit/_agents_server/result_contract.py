"""exploreの完了状態行とtaskの続行不能返却を公開前に検査する。"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._agents_server.turn_state import ResultContractWait
from agent_toolkit._common import task_documents
from agent_toolkit._common.next_action import ActionableError

_REASON_PREFIX = "続行できない理由:"
_CLASSIFICATION_PREFIX = "続行できない区分:"
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


def invalid_result_reason(session: SessionState, result: dict[str, Any]) -> str | None:
    """自由本文exploreの成功行と、explore・taskの続行不能2行を検査する。"""
    if result.get("status") != "completed":
        return None
    document = task_documents.launched_task_document(session.prompt)
    freeform_explore = session.launch_kind == "explore" and document is None
    if not freeform_explore and (document is None or not task_documents.is_agent_toolkit_task_document(document)):
        return None
    message = result.get("agent_message")
    if not isinstance(message, str):
        return (
            "探索の返却本文が必要です"
            if freeform_explore or document is not None and document.name == "reader-fit-review.subagent.md"
            else None
        )
    if re.fullmatch(r"待機中: [^\r\n]+", message.strip()):
        return None
    lines = message.splitlines()
    has_reason = False
    for index, line in enumerate(lines):
        match = re.fullmatch(r"(?P<indent>\s*)" + re.escape(_REASON_PREFIX) + r".*", line)
        if match is None:
            continue
        has_reason = True
        if not line.removeprefix(match.group("indent") + _REASON_PREFIX).strip():
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
    if freeform_explore and not has_reason:
        lines = message.rstrip().splitlines()
        if not lines or not (lines[0] == "状態: completed" or lines[-1] == "状態: completed"):
            return "exploreの返却が完了を示しません。先頭または最終行に状態: completedが必要です"
    if document is not None and document.name == "reader-fit-review.subagent.md":
        return _reader_fit_reason(session, document, message)
    return None


def _current_named_input(session: SessionState, document: pathlib.Path, name: str) -> str:
    """名前付き入力の現在値を返す。

    宣言された継続指示が同じsessionで値を更新していれば、最も新しい継続入力を使う。
    役割別の返却検査が入力を参照するときは、継続による更新を見落とさないよう本関数を通す。
    """
    declaration = task_documents.read_declaration(document)
    instructions = (
        {item.instruction for item in declaration.continuations if name in item.accepted}
        if isinstance(declaration, task_documents.TaskDocumentDeclaration)
        else set()
    )
    for followup in reversed(session.followup_prompts):
        lines = followup.strip().splitlines()
        # 継続本文は`continuation_prompt`が指示の1行と名前付き入力へ正規化して保持している。
        if lines and lines[0] in instructions:
            params = task_documents.parse_named_inputs(lines[1:])
            if name in params:
                return params[name]
    return task_documents.task_prompt_inputs(session.prompt)[name]


def _reader_fit_reason(session: SessionState, document: pathlib.Path, message: str) -> str | None:
    """入力成果物と個別返却を同じ集合で検収する。集約記述を個別完了へ代用しない。"""
    try:
        value = _current_named_input(session, document, "成果物").strip()
        targets = json.loads(value) if value.startswith("[") else [value]
    except (ActionableError, KeyError, json.JSONDecodeError) as error:
        return f"reader-fitの入力成果物を復元できません: {error}"
    if not isinstance(targets, list) or not targets or any(not isinstance(path, str) or not path for path in targets):
        return "reader-fitの入力成果物が空でないパス配列ではありません"
    if len(set(targets)) != len(targets):
        return "reader-fitの入力成果物に重複があります"
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
        # 対象別の字下げ欄の後に続く、全体の指摘・新要件はこの対象の欄へ混ぜない。
        item_lines = []
        for line in block.splitlines():
            if line.strip() and not line.startswith("  "):
                break
            item_lines.append(line)
        entries = re.findall(r"(?m)^  ([^:\n]+): (.+)$", "\n".join(item_lines))
        if len(entries) != len({key for key, _ in entries}):
            return f"{match[1]}: 対象別の項目に重複があります"
        fields = dict(entries)
        state = fields.get("完了状態")
        if state not in {"completed", "blocked"}:
            return f"{match[1]}: 完了状態はcompletedまたはblockedを1つ記載してください"
        for field in ("比較資料", "根拠"):
            if not fields.get(field, "").strip():
                return f"{match[1]}: {field}が不足しています"
        if state == "completed" and not fields.get("判定", "").strip():
            return f"{match[1]}: 完了した対象の判定が不足しています"
        if state == "completed" and ("続行できない区分" in fields or "続行できない理由" in fields):
            return f"{match[1]}: 続行不能の対象は完了状態をblockedにしてください"
        if state == "blocked":
            blocked = True
            if fields.get("続行できない区分") not in _VALID_CLASSIFICATIONS or not fields.get("続行できない理由", "").strip():
                return f"{match[1]}: blockedには有効な区分と非空の理由が必要です"
    overall = re.findall(r"(?m)^状態: (.+)$", message)
    if blocked and overall:
        return "reader-fitの個別未完了対象がある返却へ全体の状態: completedを付けないでください"
    if not blocked and overall != ["completed"]:
        return "reader-fitの全対象完了時には全体の状態: completedが1行必要です"
    return None


def hold_invalid_result(session: SessionState, result: dict[str, Any]) -> bool:
    """契約に違反した結果を1回だけ保留し、監視側の同一session継続へ渡す。"""
    reason = invalid_result_reason(session, result)
    if reason is None:
        return False
    if session.result_contract_recovery_consumed:
        error = result.get("error")
        details = dict(error) if isinstance(error, dict) else {}
        details["resultContract"] = reason
        details["nextAction"] = RECOVERY_NEXT_ACTION
        result["error"] = details
        if session.launch_kind == "explore":
            result["status"] = "failed"
        return False
    session.hold_result(ResultContractWait(result, reason))
    session.status = "running"
    session.record_turn_completed()
    session.touch()
    return True
