"""現行の作業契約の所在を通知する。"""

from __future__ import annotations

import pathlib

from agent_toolkit._atk import managed_temp

RECORD_NAME = "work-contract.md"


def record_path(session_id: str) -> pathlib.Path | None:
    """既存のセッションのmanaged-tempから、メインが所有する記録先を解決する。"""
    directory = managed_temp.find_session_temp(session_id)
    return directory / RECORD_NAME if directory is not None else None


def observe_input(session_id: str) -> str | None:
    """実ユーザー入力の後に、既存の作業契約の所在だけを通知する。"""
    return notice(session_id)


def notice(session_id: str) -> str | None:
    """既存の作業契約の所在を通知する。要求の統合と達成はエージェントが判定する。"""
    path = record_path(session_id)
    if path is None or not path.is_file():
        return None
    return (
        f"現行の作業契約: {path}\n"
        "記録と今回の原入力を読み、share/work-contract.mdに従って現在の目的・要件へ整理してから変更・追送・完了報告へ進め。"
    )
