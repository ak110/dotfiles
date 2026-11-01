"""現行の作業契約の本文と、回答後に元の工程へ戻る基準を通知する。"""

from __future__ import annotations

import os
import pathlib

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks.notice import resolve_notice

RECORD_NAME = "work-contract.md"

NOTICE_BODY_LIMIT = 4_000
"""通知へ載せる契約本文の上限（文字）。

Claude Codeは`additionalContext`を10,000文字で保存先とプレビューへ置き換えるため、
同じ入力で届く他の通知と合わせても上限へ収まる値にする。超える本文は所在だけを示す。
"""

RETURN_CRITERION = (
    "質問や状況照会への回答、部分修正を終えたら、取り下げ・置換・正当な待機が無い限り、"
    "この契約と工程記録が示す元の未完了工程へ戻れ（`share/work-contract.md`「追加入力の統合」）。"
    "今回の原入力との統合と意味上の完了はエージェントが判断する。"
)


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
        lambda state: {key: value for key, value in state.items() if key != "work_contract_notified_sequence"},
    )
    resolve_notice(session_id, "pretooluse", "main", subject="main")


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
            f"この絶対パスを読み、share/work-contract.mdに従って現在の目的・要件を確かめよ。{RETURN_CRITERION}"
        )
    if len(body) > budget:
        return (
            f"現行の作業契約: {path}\n"
            f"本文が通知の上限{budget}文字を超えるため所在だけを示す。全文を読み、現在の目的・要件を確かめよ。{RETURN_CRITERION}"
        )
    return f"現行の作業契約（機械生成の参照資料。原本: {path}）:\n{body}\n\n{RETURN_CRITERION}"
