"""`atk wi submission end`の正常終了で、投入作業中の発話・投入前レビューの指摘と指摘対応記録に対応付いているかを検査する。

URLを含む発話を受けた投入では、資料の主張別の対応表`material_claims`の欄も検査する。
URLの判定には、作業中の発話に加えて起動発話（`/atk-mode add-awi-by-user <URL>`のような直近の起動コマンドから
`begin`までの全発話）も含める。起動コマンドと`begin`の間にユーザーが資料を足す場合があるためである。
検査するのは対応の有無と必須欄の欠落だけである。考慮漏れか新しい情報かという分類の意味と処置の十分性は、
AWI投入のメインと投入前レビューが検収する。実発話は`session-review-evidence --user-events`と同じ抽出処理で
得るため、機械注入・子会話の除外規則を別に持たない。
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Sequence
from typing import Any

from agent_toolkit._atk import run_script as _run_script
from agent_toolkit._atk import session_records as _session_records
from agent_toolkit._common import plugin_environment, process_tree, session_activity, skill_modes
from agent_toolkit._common.json_lines import split_lines

CLASSIFICATIONS = ("考慮漏れ", "ユーザーだけが持つ新しい情報", "前提の誤り", "採用しない提案")
"""`agent-toolkit:atk-mode/playbooks/add-awi-by-user.md`「指摘の分類と処置」の4分類。追加要求などの要求管理上の扱いは分類の代わりにならない。"""

OMISSION = CLASSIFICATIONS[0]

MATERIAL_CLAIM_FIELDS = ("material", "claim", "judgement", "observation")
"""資料の主張別の対応表`material_claims`の各項目が持つ欄。"""

SATISFIED = "充足"

_URL = re.compile(r"https?://\S+")

_EVIDENCE_TIMEOUT_SECONDS = 120
"""実発話の抽出に許す時間。セッション記録全体の解析を含むため、通常の数秒に余裕を持たせる。"""


@dataclasses.dataclass(frozen=True)
class Findings:
    """読み込んだ指摘対応記録。"""

    confirmation_rounds: int
    entries: list[dict[str, Any]]
    material_claims: list[dict[str, Any]] | None = None


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
    claims = document.get("material_claims")
    if claims is not None and (not isinstance(claims, list) or not all(isinstance(claim, dict) for claim in claims)):
        raise ValueError("指摘対応記録の`material_claims`がobjectの配列ではない")
    return Findings(rounds, entries, claims)


def _normalized(text: str) -> str:
    return " ".join(text.split())


_NO_FINDING_PATTERN = re.compile(r"なし(?:\s*(?:（[^（）]*）|\([^()]*\)))?")
"""指摘0件を表す箇条。返却契約（`agent-toolkit:atk-mode/roles/wi-draft-review.subagent.md`「出力」）は、前回の指摘の解消などの補足を
`なし`に続く全角または半角の丸括弧の中へ書く形を許す。括弧の注記でない文が続く箇条は指摘として数える。"""


def _is_no_finding(item: str) -> bool:
    return _NO_FINDING_PATTERN.fullmatch(item) is not None


def review_findings(path: pathlib.Path) -> list[str]:
    """各`指摘:`欄の最上位箇条の先頭本文だけを、記録のtextと比較するキーとして返す。"""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"投入前レビューの返却を読めない: {path}: {error}") from error
    found: list[str] = []
    collecting = False
    item_indent: int | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == "指摘:":
            collecting = True
            item_indent = None
            continue
        if not collecting:
            continue
        if not stripped:
            continue
        indent = len(line) - len(line.lstrip())
        if stripped.startswith("- "):
            if item_indent is None:
                item_indent = indent
            if indent > item_indent:
                continue
            item = stripped[2:].strip()
            if item and not _is_no_finding(item):
                found.append(item)
            continue
        if item_indent is not None and indent > item_indent:
            continue
        collecting = False
    return found


def _transcript(session_id: str) -> pathlib.Path:
    for path, _engine, candidate in _session_records.candidate_paths():
        if candidate == session_id:
            return path
    raise ValueError(f"会話ID {session_id} のセッション記録が見つからない")


@dataclasses.dataclass(frozen=True)
class Utterances:
    """検査する実ユーザー発話。"""

    invocation: list[dict[str, Any]]
    """起動発話。直近の起動コマンドから`begin`までの発話であり、URLの判定にだけ使い、指摘対応記録の項目は求めない。"""
    after_begin: list[dict[str, Any]]
    """作業開始より後の発話のうち、指摘対応記録へ対応付ける対象。"""


def _extracted_events(transcript: pathlib.Path, since: str | None) -> list[dict[str, Any]]:
    """`session-review-evidence --user-events`の実発話を、記録の順に返す。"""
    script = _run_script.registered_script_path("session-review-evidence")
    try:
        # 同じ配布物の登録済みスクリプトを引数配列で起動し、時間上限では子孫まで停止する。
        # `atk`は起動時に起動層が加えた`PYTHONPATH`などのモジュール検索パスを環境から外すため、
        # 子が`agent_toolkit`を読めるよう配布rootを明示して渡す。
        completed = process_tree.run_with_timeout(
            [
                sys.executable,
                str(script),
                "--transcript",
                str(transcript),
                "--user-events",
                *(["--since", since] if since is not None else []),
            ],
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
    events = [json.loads(line) for line in split_lines(completed.stdout)]
    # 提示した選択肢をそのまま選んだ確認回答は通常の回答として除き、自由記述を含む回答は対象にする。
    return [
        event
        for event in events
        if event.get("kind") == "user" and ("user_response" not in event or event.get("answer_intervention"))
    ]


def _position(event: dict[str, Any]) -> str:
    return f"{event.get('record')}:{event.get('line')}"


_COMMAND_NAME = re.compile(r"<command-name>\s*[/$](\S+?)\s*</command-name>")
_COMMAND_ARGS = re.compile(r"<command-args>([\s\S]*?)</command-args>")
_TYPED_COMMAND = re.compile(r"\s*[/$](\S+)(?:\s+|$)")


def _starts_submission(event: dict[str, Any]) -> bool:
    """発話がAWI投入の起動コマンドかを返す。

    Claude Codeはスラッシュコマンドを`<command-name>`と`<command-args>`の要素で記録し、Codexは入力した本文
    （`$atk-mode add-awi-by-user <入力>`）のまま記録する。入口の起動は`skill_modes.resolve_work`で作業の名前へ読み替え、
    移行前の記録にあるスキルの直接の起動（`/add-awi-by-user`）は名前の一致で受ける。
    """
    text = str(event.get("text", ""))
    if name := _COMMAND_NAME.search(text):
        arguments = _COMMAND_ARGS.search(text)
        skill, rest = name[1], arguments[1] if arguments else None
    elif typed := _TYPED_COMMAND.match(text):
        skill, rest = typed[1], text[typed.end() :]
    else:
        return False
    work, _ = skill_modes.resolve_work(skill, rest)
    return work.removeprefix("agent-toolkit:") == session_activity.SUBMISSION_KIND


def user_events(session_id: str, started_at: str) -> Utterances:
    """起動発話と、作業開始より後の実ユーザー発話を返す。

    起動発話は、`begin`より前の直近の起動コマンドから`begin`までの全発話とする。
    記録に起動コマンドが無い場合（エージェントが作業を始めた会話など）は、`begin`の直前の1件とする。
    """
    transcript = _transcript(session_id)
    after_begin = _extracted_events(transcript, started_at)
    positions = {_position(event) for event in after_begin}
    before_begin = list(
        itertools.takewhile(lambda event: _position(event) not in positions, _extracted_events(transcript, None))
    )
    starts = [index for index, event in enumerate(before_begin) if _starts_submission(event)]
    return Utterances(before_begin[starts[-1] :] if starts else before_begin[-1:], after_begin)


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


def _material_claim_problems(findings: Findings, utterances: Utterances) -> list[str]:
    """URLを含む発話を受けた投入で、資料の主張ごとの判定と観測の欄がそろっているかを返す。

    資料を1文ずつ覆ったか、観測が判定を裏付けるかは投入前レビューが資料と対応表を読み比べて確かめる。
    """
    events = [*utterances.invocation, *utterances.after_begin]
    if not any(_URL.search(str(event.get("text", ""))) for event in events):
        return []
    if not findings.material_claims:
        return [
            "URLを含む発話を受けた投入なのに、資料の主張別の対応表`material_claims`が無い。"
            f"資料の主張1文ごとに`{'`・`'.join(MATERIAL_CLAIM_FIELDS)}`を持つ項目を加える"
        ]
    problems: list[str] = []
    for index, claim in enumerate(findings.material_claims):
        label = f"`material_claims[{index}]`"
        missing = _missing_fields(claim, MATERIAL_CLAIM_FIELDS)
        if claim.get("judgement") == SATISFIED and "observation" in missing:
            problems.append(
                f"{label}: 充足と判定した観測`observation`が無い。"
                "主張が実際に働いていることの観測（検査の実物、commit、記録の件数など）の所在を書くか、判定を未確認にする"
            )
            missing.remove("observation")
        problems += [f"{label}: `{name}`が無い" for name in missing]
    return problems


def check(findings: Findings, utterances: Utterances, reviews: Sequence[str]) -> list[str]:
    """発話・レビュー指摘と指摘対応記録の対応、および必須欄の欠落を1件1行で返す。"""
    problems: list[str] = []
    by_position = {entry.get("position"): entry for entry in findings.entries if entry.get("source") == "user"}
    by_text = {
        _normalized(entry["text"]): entry
        for entry in findings.entries
        if entry.get("source") == "review" and isinstance(entry.get("text"), str)
    }
    for event in utterances.after_begin:
        position = _position(event)
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
    return problems + _material_claim_problems(findings, utterances)


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
