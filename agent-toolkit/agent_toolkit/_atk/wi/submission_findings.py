"""`atk wi submission end`の正常終了で、投入作業中の発話・投入前レビューの指摘と指摘対応記録に対応付いているかを検査する。

検査するのは対応の有無と必須欄の欠落だけである。考慮漏れか新しい情報かという分類の意味と処置の十分性は、
add-awi-by-userのメインと投入前レビューが検収する。実発話は`session-review-evidence --user-events`と同じ抽出処理で
得るため、機械注入・子会話の除外規則を別に持たない。
"""

from __future__ import annotations

import dataclasses
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from agent_toolkit._atk import run_script as _run_script
from agent_toolkit._atk import session_records as _session_records
from agent_toolkit._common import plugin_environment, process_tree
from agent_toolkit._common.json_lines import split_lines

CLASSIFICATIONS = ("考慮漏れ", "ユーザーだけが持つ新しい情報", "前提の誤り", "採用しない提案")
"""add-awi-by-user「指摘の分類と処置」の4分類。追加要求などの要求管理上の扱いは分類の代わりにならない。"""

OMISSION = CLASSIFICATIONS[0]

_EVIDENCE_TIMEOUT_SECONDS = 120
"""実発話の抽出に許す時間。セッション記録全体の解析を含むため、通常の数秒に余裕を持たせる。"""


@dataclasses.dataclass(frozen=True)
class Findings:
    """読み込んだ指摘対応記録。"""

    confirmation_rounds: int
    entries: list[dict[str, Any]]


def load_findings(path: pathlib.Path) -> Findings:
    """指摘対応記録を読み、構造の不正をValueErrorで返す。"""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"指摘対応記録を読めない: {path}: {error}") from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"指摘対応記録がUTF-8のJSONではない: {path}: {error}") from error
    if not isinstance(document, dict):
        raise ValueError(f"指摘対応記録の最上位がobjectではない: {path}")
    rounds = document.get("confirmation_rounds")
    entries = document.get("entries")
    if not isinstance(rounds, int) or isinstance(rounds, bool) or rounds < 0:
        raise ValueError("指摘対応記録の`confirmation_rounds`が0以上の整数ではない")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        raise ValueError("指摘対応記録の`entries`がobjectの配列ではない")
    return Findings(rounds, entries)


def _normalized(text: str) -> str:
    return " ".join(text.split())


def review_findings(path: pathlib.Path) -> list[str]:
    """投入前レビューの返却から`指摘:`の箇条（`なし`を除く）を返す。複数要求の返却は全ての`指摘:`欄を読む。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"投入前レビューの返却を読めない: {path}: {error}") from error
    found: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        if stripped == "指摘:":
            collecting = True
            continue
        if not collecting:
            continue
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            if item and item != "なし":
                found.append(item)
            continue
        collecting = False
    return found


def _transcript(session_id: str) -> pathlib.Path:
    for path, _engine, candidate in _session_records.candidate_paths():
        if candidate == session_id:
            return path
    raise ValueError(f"会話ID {session_id} のセッション記録が見つからない")


def user_events(session_id: str, started_at: str) -> list[dict[str, Any]]:
    """作業開始より後の実ユーザー発話のうち、指摘対応記録へ対応付ける対象を返す。

    提示した選択肢をそのまま選んだ確認回答は通常の回答として除き、自由記述を含む回答は対象にする。
    """
    script = _run_script.registered_script_path("session-review-evidence")
    try:
        # 同じ配布物の登録済みスクリプトを引数配列で起動し、時間上限では子孫まで停止する。
        # `atk`は起動時に起動層が加えた`PYTHONPATH`などのモジュール検索パスを環境から外すため、
        # 子が`agent_toolkit`を読めるよう配布rootを明示して渡す。
        completed = process_tree.run_with_timeout(
            [sys.executable, str(script), "--transcript", str(_transcript(session_id)), "--user-events", "--since", started_at],
            env=plugin_environment.direct_environment(_run_script.PLUGIN_ROOT, os.environ),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_EVIDENCE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"実ユーザー発話の抽出が{_EVIDENCE_TIMEOUT_SECONDS}秒で終わらない") from error
    if completed.returncode != 0:
        raise ValueError(f"実ユーザー発話を抽出できない（終了コード{completed.returncode}）: {completed.stderr.strip()[-500:]}")
    events = []
    for line in split_lines(completed.stdout):
        event = json.loads(line)
        if event.get("kind") != "user":
            continue
        if "user_response" in event and not event.get("answer_intervention"):
            continue
        events.append(event)
    return events


def _missing_fields(entry: dict[str, Any], names: Sequence[str]) -> list[str]:
    return [name for name in names if not isinstance(entry.get(name), str) or not entry[name].strip()]


def _entry_problems(label: str, entry: dict[str, Any]) -> list[str]:
    finding = entry.get("finding")
    if not isinstance(finding, bool):
        return [f"{label}: `finding`が真偽値ではない"]
    if not finding:
        return [f"{label}: 指摘でない理由`reason`が無い"] if _missing_fields(entry, ["reason"]) else []
    classification = entry.get("classification")
    if classification not in CLASSIFICATIONS:
        return [f"{label}: 分類`{classification}`が4分類（{'・'.join(CLASSIFICATIONS)}）のいずれでもない"]
    problems = [f"{label}: `{name}`が無い" for name in _missing_fields(entry, ["summary", "basis", "adoption", "result"])]
    if classification == OMISSION:
        if _missing_fields(entry, ["cause_analysis"]):
            problems.append(f"{label}: 考慮漏れの原因分析`cause_analysis`が無い")
        prevention = entry.get("prevention")
        if not isinstance(prevention, dict) or _missing_fields(prevention, ["decision", "handoff"]):
            problems.append(f"{label}: 考慮漏れの再発防止策の採否`prevention.decision`と引渡し先`prevention.handoff`が無い")
    return problems


def check(findings: Findings, events: Sequence[dict[str, Any]], reviews: Sequence[str]) -> list[str]:
    """発話・レビュー指摘と指摘対応記録の対応、および必須欄の欠落を1件1行で返す。"""
    problems: list[str] = []
    by_position = {entry.get("position"): entry for entry in findings.entries if entry.get("source") == "user"}
    by_text = {
        _normalized(entry["text"]): entry
        for entry in findings.entries
        if entry.get("source") == "review" and isinstance(entry.get("text"), str)
    }
    for event in events:
        position = f"{event.get('record')}:{event.get('line')}"
        excerpt = _normalized(str(event.get("text", "")))[:60]
        entry = by_position.get(position)
        if entry is None:
            problems.append(f"実ユーザー発話{position}（{excerpt}）の項目が無い")
            continue
        problems += _entry_problems(f"実ユーザー発話{position}", entry)
    for review in reviews:
        entry = by_text.get(_normalized(review))
        label = f"投入前レビューの指摘（{_normalized(review)[:60]}）"
        if entry is None:
            problems.append(f"{label}の項目が無い")
            continue
        problems += _entry_problems(label, entry)
    return problems


def summary_lines(findings: Findings) -> list[str]:
    """作業完了報告へ載せる要約の行を返す。Stop hookはこの行が最終応答にあるかを検査する。"""
    lines = ["指摘の分類と処置:", f"- 確認の往復: {findings.confirmation_rounds}回"]
    indicated = [entry for entry in findings.entries if entry.get("finding") is True]
    if not indicated:
        return [*lines, "- 指摘: なし"]
    for entry in indicated:
        raw_prevention = entry.get("prevention")
        prevention: dict[str, Any] = raw_prevention if isinstance(raw_prevention, dict) else {}
        decision = _normalized(str(prevention.get("decision") or "対象外"))
        handoff = _normalized(str(prevention.get("handoff") or "なし"))
        lines.append(
            f"- {_normalized(str(entry['summary']))}: {entry['classification']}（再発防止: {decision}、登録先: {handoff}）"
        )
    return lines
