"""証拠抽出の問題候補（session_evidence_candidates_users）。"""

from __future__ import annotations

from typing import Any

from session_evidence_extract import _generated_user_event

from agent_toolkit._atk.wi.constants import PROCESS_LOOP_GOAL_BODY as _PROCESS_LOOP_GOAL_BODY
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY as _PROCESS_WI_GOAL_BODY

# 過去の単一対象と現在の登録全体の起動を、後続の人間の介入を探すための初期要求として扱う。
_PROCESS_WI_GOAL_BODIES = (
    _PROCESS_LOOP_GOAL_BODY,
    _PROCESS_WI_GOAL_BODY,
    "agent-toolkit:process-wi all-reposを完遂する",
    "agent-toolkit:process-wi-all-reposを完遂する",
)


def _initial_request_locator(timeline: list[dict[str, Any]], *, main_record_id: str = "main") -> tuple[str, int] | None:
    """メイン記録の初期要求の位置を返す。

    人間の依頼で始まるセッションでは、最初の人間の発話を初期要求とする。
    `atk wi process-loop`が自動起動したセッションでは、起動の目的文を持つ入力を初期要求とし、
    その後の人間の発話は作業中の介入として候補に残す。起動の目的文は記録で前置きが付くため、包含で判定する。
    """
    for event in timeline:
        line = event.get("line")
        text = event.get("text")
        if event.get("kind") != "user" or event.get("record") != main_record_id or not isinstance(line, int):
            continue
        if not isinstance(text, str):
            continue
        if any(body in text for body in _PROCESS_WI_GOAL_BODIES):
            return (main_record_id, line)
        if _user_candidate_exclusion(event, main_record_id, line, None, main_record_id=main_record_id) is None:
            return (main_record_id, line)
    return None


def _user_candidate_exclusion(
    event: dict[str, Any],
    record: str,
    line: int,
    first_main_user: tuple[str, int] | None,
    *,
    initial_skill_request: tuple[str, int] | None = None,
    initial_skill_body: tuple[str, int] | None = None,
    main_record_id: str = "main",
) -> str | None:
    """構造と固定接頭辞だけでユーザー介入ではない入力を分類する。

    接頭辞は、実行環境がユーザーのメッセージへ挿入する本文、process-loopの通知、および定時promptの
    先頭に現れる固定文字列を実記録から採取したものとする。これらはユーザーの発話ではないため、
    残すとユーザー介入の候補が実際の介入件数を超える。
    接頭辞を持たない実行環境の生成は本文の形からは判別できないため、`_is_runtime_generated`が
    付けた標識で分類する。
    確認への回答は、生成側が付ける`user_response`の構造で判定し、表示用の本文の書式からは推定しない。
    本文の書式で判定すると、書式の変更で選択肢どおりの回答が候補に残り、同じ書式の通常の発話が回答として除外される。
    回答のうち`answer_intervention`を持つものは、
    従来の判断を是正した介入であるため、除外せず候補として残す。
    選択肢をそのまま選んだ回答へ返す`question-answer`はユーザー介入ではない区分であり、
    呼び出し元はその回答を除外せず、確認の要否を振り返る`confirmation-request`の候補とする。
    """
    if record != main_record_id:
        return "delegated-record"
    if event.get("runtime_generated") is True:
        return "runtime-meta"
    if initial_skill_request == (record, line):
        return "initial-skill-request"
    if initial_skill_body == (record, line):
        return "initial-skill-body"
    if _generated_user_event(event):
        return "runtime-inserted"
    if isinstance(event.get("user_response"), list):
        return None if event.get("answer_intervention") is True else "question-answer"
    if first_main_user == (record, line):
        return "initial-request"
    return None


def _initial_skill_input_locators(
    timeline: list[dict[str, Any]],
    *,
    main_record_id: str = "main",
) -> tuple[tuple[str, int] | None, tuple[str, int] | None]:
    """Codexの先頭スキル要求と、直後に挿入された対応本文の位置を返す。"""
    main_users = [
        event
        for event in timeline
        if event.get("kind") == "user"
        and event.get("record") == main_record_id
        and isinstance(event.get("line"), int)
        and isinstance(event.get("text"), str)
        and event.get("runtime_generated") is not True
    ]
    request_index = next(
        (
            index
            for index, event in enumerate(main_users)
            if _user_candidate_exclusion(event, main_record_id, int(event["line"]), None, main_record_id=main_record_id) is None
        ),
        None,
    )
    if request_index is None:
        return None, None
    request = main_users[request_index]
    request_text = str(request["text"]).strip()
    if not request_text.startswith("$") or any(character.isspace() for character in request_text):
        return None, None
    skill_name = request_text.removeprefix("$")
    if not skill_name or any(not (character.isalnum() or character in {"-", "_", ":"}) for character in skill_name):
        return None, None
    if request_index + 1 >= len(main_users):
        return None, None
    body = main_users[request_index + 1]
    body_text = str(body["text"]).lstrip()
    if not body_text.startswith("<skill>") or f"<name>{skill_name}</name>" not in body_text:
        return None, None
    return (main_record_id, int(request["line"])), (main_record_id, int(body["line"]))
