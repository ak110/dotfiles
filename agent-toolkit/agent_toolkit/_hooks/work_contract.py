"""現行の作業契約の所在を通知する。"""

from __future__ import annotations

import os
import pathlib

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.delegated_session import is_delegated
from agent_toolkit._common.session_state import update_state
from agent_toolkit._hooks.notice import resolve_notice

RECORD_NAME = "work-contract.md"


def record_path(session_id: str) -> pathlib.Path | None:
    """既存のセッションのmanaged-tempから、メインが所有する記録先を解決する。"""
    directory = managed_temp.find_session_temp(session_id)
    return directory / RECORD_NAME if directory is not None else None


def observe_input(session_id: str) -> str | None:
    """実ユーザー入力の後に、既存の作業契約の所在だけを通知する。"""
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


def notice(session_id: str) -> str | None:
    """既存の作業契約の所在を通知する。要求の統合と達成はエージェントが判定する。"""
    path = record_path(session_id)
    if path is None or not path.is_file():
        return None
    return (
        f"現行の作業契約: {path}\n"
        "記録と今回の原入力を読み、share/work-contract.mdに従って現在の目的・要件へ整理してから変更・追送・完了報告へ進め。"
    )
