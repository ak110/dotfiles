"""現行の作業契約の本文と、回答後に元の工程へ戻る基準を通知する。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks import document_reads
from agent_toolkit._hooks import notice as notice_module
from agent_toolkit._hooks.notice import resolve_notice

RECORD_NAME = "work-contract.md"
DELEGATED_NOTICE_KEY = "whole_contract_notified"

NOTICE_BODY_LIMIT = 4_000
"""通知へ載せる契約本文の上限（文字）。

Claude Codeは`additionalContext`を10,000文字で保存先とプレビューへ置き換えるため、
同じ入力で届く他の通知と合わせても上限へ収まる値にする。超える本文は所在だけを示す。
"""

RETURN_CRITERION = (
    "質問や状況照会への回答、部分修正を終えたら、取り下げ・置換・正当な待機が無い限り、"
    "この契約と工程記録が示す元の未完了工程へ戻れ（`share/work-contract.md`「追加入力の統合」）。"
    "ターンを終える前に原要求全体と実行可能な残工程を突き合わせ、部分的な回答・調査・説明・計画・途中報告だけで依頼全体を完了扱いにするな。"
    "今回の原入力との統合と意味上の完了はエージェントが判断する。"
)


SHORT_NOTICE = "現行の作業契約: 同じ領域の`work-contract.md`を全文読み、回答後は元の未完了工程へ戻れ。"
"""通知の上限に本文が収まらない場合に、一時領域の通知へ加える所在の1行。"""


def exists(session_id: str) -> bool:
    """メインの作業契約が実在するかを返す。"""
    path = record_path(session_id)
    return path is not None and path.is_file()


def record_path(session_id: str) -> pathlib.Path | None:
    """既存のセッションのmanaged-tempから、メインが所有する記録先を解決する。"""
    directory = managed_temp.find_session_temp(session_id)
    return directory / RECORD_NAME if directory is not None else None


def observe_input(session_id: str) -> str | None:
    """実ユーザー入力の後に、既存の作業契約の本文を通知する。"""
    update_state(
        session_id,
        lambda state: {**state, "work_contract_input_sequence": state.get("work_contract_input_sequence", 0) + 1},
    )
    resolve_notice(session_id, "pretooluse", "main", subject="main")
    return notice(session_id)


def reset_notice(session_id: str) -> None:
    """圧縮で失われた未作成通知を、次の操作で再配送可能にする。"""
    update_state(
        session_id,
        lambda state: {
            key: value for key, value in state.items() if key not in {"work_contract_notified_sequence", DELEGATED_NOTICE_KEY}
        },
    )
    resolve_notice(session_id, "pretooluse", "main", subject="main")


def delegated_notice(session_id: str, records: dict, state: dict, *, agent: str = "main") -> str | None:
    """受領した全体契約の未読版を、本文配送や遮断をせず1回案内する。"""
    if not is_delegated(os.environ):
        return None
    delivered = state.get(DELEGATED_NOTICE_KEY, {})
    seen = delivered.get(agent, []) if isinstance(delivered, dict) else []
    for key, actors in records.items():
        if not key.startswith("startup-input-target:") or agent not in actors:
            continue
        metadata = json.loads(key.removeprefix("startup-input-target:"))
        if "input:作業契約" not in metadata.get("selectors", [metadata.get("selector", "")]):
            continue
        path = pathlib.Path(metadata["path"])
        try:
            fingerprint = document_reads.content_fingerprint(path)
        except OSError:
            return f"全体作業契約を取得できない: {path}。委譲元へ所在と読取可能性を確認せよ。"
        if document_reads.content_ready(records, fingerprint, agent) or fingerprint in seen:
            continue
        text = (
            f"全体作業契約の現行版を再読せよ: {path}。"
            "「即時対応で確定した暫定規則」を含む要求を確かめ、配布前の暫定規則に従え。"
            f"Readまたは`cat -- {shlex.quote(str(path))}`で全文を取得してから判断・操作へ進め。"
        )
        notice_module.stage_delivery(text, session_id, DELEGATED_NOTICE_KEY, agent, fingerprint)
        return text
    return None


def missing_notice(session_id: str, *, agent_id: object = None) -> str | None:
    """未作成のメインへ、入力ごとの最初の操作で作成条件を示す。"""
    if not session_id or agent_id or is_delegated(os.environ):
        return None
    path = record_path(session_id)
    if path is None:
        path = managed_temp.session_temp_path(session_id) / RECORD_NAME
    if path.is_file():
        return None
    claimed = False

    def claim(state: dict) -> dict | None:
        nonlocal claimed
        sequence = state.get("work_contract_input_sequence", 0)
        if state.get("work_contract_notified_sequence") == sequence:
            return None
        claimed = True
        return {**state, "work_contract_notified_sequence": sequence}

    update_state(session_id, claim)
    if not claimed:
        return None
    return (
        f"現行の作業契約が未作成: `{path}`\n"
        "`share/work-contract.md`「作成と所有」に従い、成果物の作成・変更・操作または複数工程の調査に着手するなら、"
        "この絶対パスへ作業契約を作成せよ。同じ応答で完結し未完了の工程を残さない事実回答では作成しない。"
    )


def notice(session_id: str, *, budget: int = NOTICE_BODY_LIMIT) -> str | None:
    """既存の作業契約の本文を参照資料として返す。要求の統合と達成はエージェントが判定する。

    本文を毎回届けるのは、所在だけの通知では質問へ回答した後に契約を読み直さず、元の工程へ戻らない事例があったためである。
    `budget`を超える本文と読取に失敗した本文は、所在と理由だけを示す非遮断の案内にする。
    """
    path = record_path(session_id)
    if path is None or not path.is_file():
        return None
    try:
        body = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        return (
            f"現行の作業契約を読めない（{type(error).__name__}: {error}）: {path}\n"
            "権限や文字コードの原因を解消できればこの絶対パスを読み、share/work-contract.mdに従って現在の目的・要件を確かめよ。"
            "解消できなければ、会話の原文と計画・WIから現在の目的・要件を確かめて作業を続け、"
            "所在と診断を委譲元（委譲元が無ければユーザー）へ報告せよ。"
            f"{RETURN_CRITERION}"
        )
    if len(body) > budget:
        return (
            f"現行の作業契約: {path}\n"
            f"本文が通知の上限{budget}文字を超えるため所在だけを示す。全文を読み、現在の目的・要件を確かめよ。{RETURN_CRITERION}"
        )
    return f"現行の作業契約（機械生成の参照資料。原本: {path}）:\n{body}\n\n{RETURN_CRITERION}"
