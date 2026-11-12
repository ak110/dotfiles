"""振り返りの入力準備（session_review_prepare_candidates）。"""

from __future__ import annotations

import collections
import json
import re
import unicodedata
from typing import Any

from session_review_prepare_text import _SUMMARY_TOP_COUNT, _clip_utf8, _one_line

from agent_toolkit._atk.wi import layout as _wi_layout
from agent_toolkit._atk.wi import sections as _wi_sections
from agent_toolkit._atk.wi import uwi_scan as _uwi_scan

MANDATORY_KINDS = frozenset(
    {
        "user-intervention",
        "confirmation-request",
        "wi-user-response",
        "definition-body-read",
        "definition-read-unresolved",
        "escalation",
    }
)
"""評価を必須とする候補種別。

`escalation`（`続行できない理由:`を含む返却）は担当の作業遂行不能を示すため、後続で回復しても個別に評価する。
必須でない候補として残すと、振り返り結果報告が既存の対策へ集約したかどうかを候補ごとに追えなかった。

ユーザー介入、ユーザー確認（選択肢どおりの回答と投入したUWI）、WIの記入欄の是正は、
前例の有無によらず評価を要し、除外区分は`agent-basics:bugfix`の
`agent-toolkit:user-confirmation-and-report/references/analysis-operations-intervention.md`「ユーザー介入と確認の判定」で確かめる。探索の深さと再発防止策の要否・終了は`agent-basics:bugfix/references/root-cause-severity.md`「QとC・Dの区別」の重大度判断に従う。
参照資料を読み込まなくても必須だと分かるよう候補一覧と1行JSONへ示し、
振り返り結果報告は各候補の扱いを候補IDで示す（書式は`agent-toolkit:report-completion`の`references/report-formats.md`）。
`definition-body-read`は、準備が起動準備の疑いあり（`launch_check`の`status`が`suspected`）と判定した読取を
1件以上含む候補だけを必須とする。判定の条件は`is_mandatory`が持つ。
"""


def is_mandatory(candidate: dict[str, Any]) -> bool:
    """候補の評価が必須かを返す。候補一覧、保存索引、1行JSONはいずれも本関数で判定する。

    `definition-body-read`の候補は、後続に同じ役割の起動が無い読取と、読む主体の役割文書の読取を準備で判定済みにするため、
    起動準備の疑いがある読取を含む候補だけを必須とする。禁止事項（別の担当を起動する前にその手順書を読むこと）は
    読んだ主体がその役割を起動して初めて成立するためである。
    """
    kind = candidate["candidate_kind"]
    if kind not in MANDATORY_KINDS:
        return False
    if kind != "definition-body-read":
        return True
    return any(
        isinstance(call, dict)
        and isinstance(call.get("launch_check"), dict)
        and call["launch_check"].get("status") == "suspected"
        for call in candidate.get("calls", [])
    )


_SIMILAR_RECORD_KINDS = frozenset({"user-intervention", "wi-user-response"})
"""過去の同種記録を添える候補種別。ユーザーの発話を本文に持つ種別に限る。"""


_SIMILAR_SECTIONS = ("ユーザー指摘の逐語引用", "回答", "ユーザーコメント")
"""過去の同種記録を探すWIの節。ユーザーの発話を逐語で保持する節に限り、エージェントが書いた本文との一致を除く。"""


_SIMILAR_MIN_SENTENCE = 20


_SIMILAR_NGRAM = 5


_SIMILAR_RATIO = 0.8
"""過去の同種記録の一致条件。空白を除きNFKC正規化した20字以上の文の文字5-gramのうち8割以上を節の本文が含む場合に一致とする。

過去の振り返りの`user-intervention`候補207件とprivate-notesの全WIで比べ、12字以上・6割以上の条件では
スキル展開の定型文などの短い文が多数のWIへ一致した。20字以上・8割以上では、包含率9割5分未満の一致が
同じ是正の言い換えと同じ環境のログに限られ、無関係な一致は観測されなかった。
"""


_FAILURE_KINDS = frozenset({"command-failure", "tool-failure"})


_FAILURE_LEDGER_DAYS = 30


def _readable_entry_text(text: str) -> str:
    """`atk run-script session-review-evidence`がエントリ全体をJSONで返した詳細から、人が読む本文を取り出す。

    本文を取り出せないJSONと、切り詰めでJSONとして解釈できない文字列はそのまま返す。
    """
    try:
        entry = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(entry, dict):
        return text
    parts: list[str] = []
    for container in (entry.get("message"), entry.get("payload")):
        if not isinstance(container, dict):
            continue
        for key in ("content", "output", "message"):
            parts.extend(_text_parts(container.get(key)))
    attachment = entry.get("attachment")
    if isinstance(attachment, dict):
        for key in ("prompt", "content"):
            parts.extend(_text_parts(attachment.get(key)))
    joined = "\n".join(part for part in parts if part.strip())
    return joined or text


def _text_parts(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                for key in ("text", "content"):
                    parts.extend(_text_parts(item.get(key)))
        return parts
    return []


def _candidate_bodies(candidate: dict[str, Any], evidence: dict[str, Any]) -> tuple[list[str], str]:
    """候補の証拠から人が読む本文の一覧と、その先頭（無ければ候補の本文）を返す。"""
    bodies: list[str] = []
    for event in evidence.get("events", []):
        if event.get("kind") != "detail":
            continue
        for key in ("text", "input", "command", "stderr", "error"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                bodies.append(_readable_entry_text(value) if key == "text" else value)
            elif value is not None and key in {"input", "command", "error"}:
                bodies.append(json.dumps(value, ensure_ascii=False))
    return bodies, bodies[0] if bodies else str(candidate.get("text", ""))


def _provenance_text(records: list[str], timeline: list[dict[str, Any]]) -> str:
    """記録ごとの由来（役割文書、起動mode、起動元の記録とその役割文書）を、同じbundleの`record-provenance`から示す。

    候補の要約の文言（引数のパスやコマンド）からは実行主体を推せないため、記録位置へ由来を添えて
    読み手が別の照会をせずに実行主体を確かめられるようにする。由来の無い記録は取得できなかったことを示す。
    """
    provenance = {
        str(event["record"]): event
        for event in timeline
        if event.get("kind") == "record-provenance" and isinstance(event.get("record"), str)
    }

    def describe(record: str) -> str:
        item = provenance.get(record)
        if item is None:
            return f"{record}=由来を取得できなかった"
        source = item.get("source_record")
        if not isinstance(source, str):
            return f"{record}={'メイン' if item.get('role') == 'main' else '起動元なし'}"
        parent = provenance.get(source)
        if parent is None:
            parent_text = "起動元の由来を取得できなかった"
        elif parent.get("role") == "main" and not parent.get("source_record"):
            parent_text = "メイン"
        else:
            parent_text = f"役割文書{parent.get('role_document') or 'なし'}"
        line = item.get("source_line")
        return (
            f"{record}=役割文書{item.get('role_document') or 'なし'}・mode {item.get('mode') or '不明'}・"
            f"起動元{source}{f':{line}' if line is not None else ''}（{parent_text}）"
        )

    return "由来: " + "; ".join(describe(record) for record in dict.fromkeys(records))


def _normalize_for_similarity(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _ngrams(text: str) -> set[str]:
    return {text[index : index + _SIMILAR_NGRAM] for index in range(len(text) - _SIMILAR_NGRAM + 1)}


def _sentences(text: str) -> list[set[str]]:
    """発話を文へ分け、一致の判定に使う長さの文の5-gram集合を返す。"""
    pieces = re.split(r"[。！？!?\n]", text)
    return [_ngrams(piece) for piece in map(_normalize_for_similarity, pieces) if len(piece) >= _SIMILAR_MIN_SENTENCE]


def _past_wi_sections() -> list[tuple[str, str, str, list[set[str]]]]:
    """private-notesの全状態のWIから、ファイル名、状態、タイトルと、ユーザーの発話を持つ節ごとの5-gram集合を返す。"""
    root = _uwi_scan.private_notes_root()
    if root is None:
        return []
    records: list[tuple[str, str, str, list[set[str]]]] = []
    for path in _wi_layout.iter_entry_paths(root):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError, UnicodeError:
            continue
        state = _wi_layout.entry_state_of(path, text)
        sections = _wi_sections.h2_sections(text)
        grams = [_ngrams(_normalize_for_similarity(sections[name])) for name in _SIMILAR_SECTIONS if sections.get(name)]
        if grams:
            records.append((path.name, state, _wi_sections.title(text), grams))
    return records


def _similar_records(
    text: str, past: list[tuple[str, str, str, list[set[str]]]], *, exclude: str | None
) -> list[tuple[str, str, str]]:
    """発話のいずれかの文の5-gramの8割以上を1つの節が含むWIを、ファイル名、状態、タイトルで返す。"""
    sentences = _sentences(text)
    matched: list[tuple[str, str, str]] = []
    for filename, state, title, sections in past:
        if filename == exclude:
            continue
        if any(
            len(sentence & section) >= _SIMILAR_RATIO * len(sentence)
            for sentence in sentences
            for section in sections
            if sentence
        ):
            matched.append((filename, state, title))
    return sorted(matched)


def _candidates_document(
    candidates: list[dict[str, Any]],
    summary: dict[str, Any],
    timeline: list[dict[str, Any]],
    detail_command: str,
    single_failures: list[dict[str, Any]],
) -> str:
    """候補の全量を保存索引へ委ね、初回には種別・必須性・代表位置と短い概要を示す。"""
    counts = collections.Counter(str(item["candidate_kind"]) for item in candidates)
    mandatory = [item for item in candidates if is_mandatory(item)]
    lines = [
        "# 問題候補",
        "",
        f"- 候補: {len(candidates)}件（"
        + ("、".join(f"{kind} {count}件" for kind, count in sorted(counts.items())) or "なし")
        + "）",
        f"- 評価が必須: {len(mandatory)}件。全IDは保存索引のmandatory=trueで確認する。",
        "全候補ID・必須性・詳細への対応・過去の同種記録はbundle/review-index.jsonlに保持する。"
        "候補の全欄はbundle/candidates.jsonl、全文証拠の対応はbundle/candidate-evidence.jsonlを使う。",
        "各候補の表示は代表位置と概要であり、全発生位置・質問と選択肢・実回答・操作入力・由来は保存証拠で確認する。",
        f"原記録の全文は`{detail_command} --detail <記録位置>`で取得する。",
        "表示から省いた候補も全件対応付け、必須候補は全て評価する。"
        "探索の深さと対策の要否はbugfixの重大度判断に従う。"
        "definition-body-readは、準備が起動準備の疑いあり（calls[].launch_check.status=suspected）と判定した読取だけを分類し、判定済み（cleared）の読取は判定理由を確かめるだけでよい。"
        "definition-read-unresolvedは取得の有無・目的・結果の成否を別々に判定する。内側CommandExecutionを結び付けた候補は、外側セルと内側実行の両方の位置を保存索引で確認する。分類と起動準備の違反への対策必須化はreview-sessionのanalysis.mdに従う。",
        "",
        "## 記録の由来",
        "",
    ]
    records = sorted({str(locator["record"]) for item in candidates for locator in item.get("locators", [])})
    aliases = {record: f"r{index}" for index, record in enumerate(records, 1)}
    for record in records[:_SUMMARY_TOP_COUNT]:
        lines.append(f"- {aliases[record]}: {_clip_utf8(_provenance_text([record], timeline), 350)}")
    if len(records) > _SUMMARY_TOP_COUNT:
        lines.append(f"他{len(records) - _SUMMARY_TOP_COUNT}記録の由来はbundle/timeline.jsonlのrecord-provenanceを参照する。")
    lines.extend(["", "## 候補索引", ""])
    # 件数に応じて概要を短くする。位置や識別子だけでも収まらない範囲は保存索引で全件を識別する。
    per_candidate = min(400, max(70, 55_000 // max(1, len(candidates))))
    omitted: collections.Counter[str] = collections.Counter()
    budget = 58_000
    used = len("\n".join(lines).encode("utf-8"))
    for candidate in candidates:
        kind = str(candidate["candidate_kind"])
        first: dict[str, Any] = next(iter(candidate.get("locators", [])), {})
        record = str(first.get("record", ""))
        location = f"{aliases.get(record, record)}:{first.get('line', '?')}"
        readable = candidate.get("failure_summary", candidate.get("text", ""))
        contexts = candidate.get("assistant_context")
        if isinstance(contexts, list) and contexts and isinstance(contexts[0], dict):
            readable = contexts[0].get("question", readable)
        required = "、評価が必須" if is_mandatory(candidate) else ""
        sessions = f"、{candidate['failure_session_count']}セッション" if "failure_session_count" in candidate else ""
        prefix = (
            f"- {candidate['candidate_id']} {kind}（発生"
            f"{candidate.get('occurrence_count', candidate.get('count', 1))}件{sessions}{required}）: "
        )
        row = prefix + _clip_utf8(_one_line(str(readable)), max(0, per_candidate - len(prefix.encode("utf-8"))))
        row += f"（{location}）"
        size = len(row.encode("utf-8")) + 1
        if used + size > budget:
            omitted[kind] += 1
            continue
        lines.append(row)
        used += size
    if omitted:
        lines.extend(
            [
                "",
                "表示から省いた候補（全IDと詳細はbundle/review-index.jsonl）: "
                + "、".join(f"{kind} {count}件" for kind, count in sorted(omitted.items())),
            ]
        )
    if not candidates:
        lines.append("候補として残す問題は無かった。")
    excluded = "、".join(f"{name} {count}件" for name, count in sorted(summary.get("excluded", {}).items())) or "なし"
    lines.extend(
        [
            "",
            f"単発の失敗{len(single_failures)}件は同じ索引のsingle-failureとして保持する。",
            "候補から除いた件数: " + _clip_utf8(excluded, 1200),
        ]
    )
    return "\n".join(lines) + "\n"
