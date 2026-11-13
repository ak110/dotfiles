"""exploreの完了状態行と、役割taskの固定行・続行不能返却を公開前に検査する。"""

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
# 「返却形式の受け渡し」が全役割に定める正常結果の状態行。役割固有の固定行は宣言の`result_heads`が加える。
_STATE_HEAD = "状態:"
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
    """自由本文exploreの成功行と、役割taskの固定行、explore・taskの続行不能2行を検査する。"""
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
    if freeform_explore and not has_reason:
        lines = message.rstrip().splitlines()
        if not lines or not (lines[0] == "状態: completed" or lines[-1] == "状態: completed"):
            return "exploreの返却が完了を示しません。先頭または最終行に状態: completedが必要です"
    if document is not None and document.name == "reader-fit-review.subagent.md":
        return _reader_fit_reason(session, document, message)
    if document is not None and not has_reason:
        return _role_head_reason(document, message)
    return None


def _role_head_reason(document: pathlib.Path, message: str) -> str | None:
    """役割taskの返却が、状態行か役割文書の宣言する固定行を持つかを検査する。

    低い推論量の担当が作業途中で状態行も区分も無い自由文を返すと、委譲元へ完了として届く。
    固定行の一覧は返却形式を所有する役割文書の宣言から得て、検査と文書の食い違いを避ける。
    """
    declaration = task_documents.read_declaration(document)
    if not isinstance(declaration, task_documents.TaskDocumentDeclaration):
        # 宣言を読めない役割は返却形式も確かめられないため、正しい返却の保留を避けて検査しない。
        return None
    heads = (_STATE_HEAD, *declaration.result_heads)
    for line in message.splitlines():
        stripped = line.strip().removeprefix("- ")
        if any(_head_matches(stripped, head) for head in heads):
            return None
    accepted = "、".join(f"`{head}`" for head in heads)
    return (
        "役割文書の返却形式の固定行も、続行できない区分と理由の2行もありません。"
        f"担当内の作業を完遂して固定行（{accepted}）で返すか、続行不能の単位を区分と理由の2行で返してください"
    )


def _head_matches(line: str, head: str) -> bool:
    """`:`で終わる固定行は前方一致、それ以外は行全体か直後の全角括弧の補足付きで一致させる。"""
    if head.endswith(":"):
        return line.startswith(head)
    return line == head or line.startswith(head + "（")


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


_MULTILINE_VALUE_HINT = "値は欄名と同じ行の`:`の後に書くか、次行へ3文字以上字下げするか先頭2空白の`  - `で続けてください"

_READER_FIT_FIELD = re.compile(r"^  ([^\s:-][^:\n]*):(?: (.*))?$")


def _reader_fit_fields(item_lines: list[str]) -> list[tuple[str, str, str]]:
    """対象別の欄を、欄名・同じ行の値・続く行を含む値の組で返す。

    欄の行は2文字の字下げに続く`- `以外の文字で始まる。欄名の行の下で`  - `か3文字以上の字下げで続く行は、
    直前の欄の値に含め、その中の`- 逐語引用1: …`のような行を別の欄として数えない。
    雛形は値を1行に限らず、根拠には複数の事実と引用が入るためである。
    """
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
    # Markdownの改行用の行末空白は表記だけの差であり、パスや値の一部として比べない。
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
        # 対象別の字下げ欄の後に続く、全体の指摘・新要件はこの対象の欄へ混ぜない。
        item_lines = []
        for line in block.splitlines():
            if line.strip() and not line.startswith("  "):
                break
            item_lines.append(line)
        entries = _reader_fit_fields(item_lines)
        if len(entries) != len({key for key, _, _ in entries}):
            return f"{match[1]}: 対象別の項目に重複があります"
        # 閉集合の欄は同じ行の1語で判定し、自由記述の欄は字下げして続く行も値に含める。
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


def hold_invalid_result(session: SessionState, result: dict[str, Any]) -> bool:
    """形式違反とexploreの不足申告を同じ1回の保留・継続へ渡す。"""
    effort_error = session.effort_request_results.get("result-contract")
    if effort_error is not None:
        error = result.get("error")
        result["error"] = {**(error if isinstance(error, dict) else {}), "effortRecovery": effort_error}
    reason = invalid_result_reason(session, result)
    if reason is None:
        # 有効な不足申告の真偽は機械判定せず、exploreだけ同じ回復枠で一度再確認する。
        message = result.get("agent_message")
        if (
            session.launch_kind != "explore"
            or result.get("status") != "completed"
            or not isinstance(message, str)
            or not re.search(r"(?m)^\s*続行できない理由:", message)
            or session.result_contract_recovery_consumed
        ):
            return False
        reason = "不足の前提を再確認し、担当内で補える残工程を完遂してください"
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
