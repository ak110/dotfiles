"""現行の作業契約の所在と実ユーザー入力の処理済み位置を通知する。"""

from __future__ import annotations

import pathlib
import re

from agent_toolkit._atk import managed_temp
from agent_toolkit._common.session_state import read_state, update_state

_INPUT_SEQUENCE_KEY = "work_contract_input_sequence"
RECORD_NAME = "work-contract.md"


def record_path(session_id: str) -> pathlib.Path | None:
    """既存のセッションのmanaged-tempから、メインが所有する記録先を解決する。"""
    directory = managed_temp.find_session_temp(session_id)
    return directory / RECORD_NAME if directory is not None else None


def _sequence(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def observe_input(session_id: str) -> str | None:
    """実ユーザー入力の連番を排他下で進め、既存記録がある場合だけ通知する。"""

    def advance(state: dict) -> dict:
        state[_INPUT_SEQUENCE_KEY] = _sequence(state.get(_INPUT_SEQUENCE_KEY)) + 1
        return state

    if not update_state(session_id, advance):
        path = record_path(session_id)
        if path is None or not path.is_file():
            return None
        return (
            f"現行の作業契約: {path}\n"
            "今回の実ユーザー入力の位置を保存できなかった。今回入力の処理済み連番は更新せず、"
            "会話原本の位置を記録して分類・統合し、元の未完了工程へ戻る。"
            "状態保存の診断を確認してから連番の記録を再開する。"
        )
    return notice(session_id)


def notice(session_id: str) -> str | None:
    """記録内の処理済み位置と最新入力の連番を比べ、再読が必要な入力を通知する。要求の達成はエージェントが判定する。"""
    path = record_path(session_id)
    if path is None or not path.is_file():
        return None
    latest = _sequence(read_state(session_id).get(_INPUT_SEQUENCE_KEY))
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        return f"現行の作業契約: {path}。読取不能: {error}。記録を回復し、原文の要求に対して残る工程を確かめてから続行する。"
    marker = re.search(rf"^処理済み入力: {re.escape(session_id)}:(\d+)$", text, re.MULTILINE)
    processed = int(marker[1]) if marker is not None else 0
    lines = [f"現行の作業契約: {path}"]
    if processed < latest:
        lines.append(f"未処理入力: {session_id}:{processed + 1}..{latest}（今回までの実ユーザー入力の連番）。")
    elif processed > latest:
        lines.append("処理済み入力が観測済みの連番を超えている。原入力と記録の対応を確かめる。")
    lines.append(
        "記録と未処理の原入力を読み、share/work-contract.mdに従って現在の結論へ整理してから変更・追送・完了報告へ進む。"
        "質問への応答後は記録の元の未完了工程へ戻る。処理済み位置は分類・統合や達成の正しさの証拠にはならない。"
    )
    return "\n".join(lines)
