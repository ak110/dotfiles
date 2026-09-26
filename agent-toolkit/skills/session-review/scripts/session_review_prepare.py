"""セッション振り返りの入力を抽出し、会話の流れ、問題候補の一覧及びセッション統計を作業ディレクトリへ書く。

1回の実行で証拠bundleを抽出し、メインが同じセッション内で読む3つの文書を書いて、所在と件数を1行のJSONで返す。
原因と対策の確定、AWIの起草と投入はメインが自身のコンテキストで行うため、本スクリプトはキューを変更しない。
候補一覧は1候補を1行の要約と記録位置で示し、全文が要る候補だけをメインが抽出器の`--detail`で照会する。

本スクリプトは検査スクリプトではなくデータ取得ツールであるため、
`agent-toolkit:writing-standards`の`references/check-script-design.md`が定める「成功時無出力」規定は適用せず、
引数誤用と準備項目を取得できない実行前エラーを終了コード2とする区分だけを踏襲する。
"""

from __future__ import annotations

import argparse
import collections
import contextlib
import datetime
import io
import json
import pathlib
import re
import subprocess
import sys
from typing import Any

import session_review_evidence  # pylint: disable=import-error

from agent_toolkit._atk import run_script

CONVERSATION_FILENAME = "conversation.md"
CANDIDATES_FILENAME = "candidates.md"
STATS_FILENAME = "stats.md"

_UTTERANCE_FULL_LIMIT = 1000
_UTTERANCE_EDGE_LENGTH = 500
"""会話の流れへ全文を載せる発話の上限と、それを超える発話で載せる先頭・末尾の文字数。

実測では自動挿入本文を除いた発話の大半が1000字以下で、超える発話は記録あたり0〜2件だった。
流れをたどるには先頭と末尾で足り、全文が要る発話は記録位置から照会する。
"""
_SUMMARY_LENGTH = 200
"""候補一覧の1行の要約、対象のツール呼び出し及び直前のアシスタント発話へ載せる文字数。"""
_SLOW_CALL_LIMIT = 10
_FULL_TEXT_KINDS = frozenset({"user-intervention", "escalation"})
"""候補一覧へ本文の全文を載せる候補種別。利用者の是正と上位判断の要求は要約すると趣旨が変わるため全文を載せる。"""


def _build_parser() -> argparse.ArgumentParser:
    """コマンドライン引数を定義する。"""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    session = parser.add_mutually_exclusive_group(required=True)
    session.add_argument("--transcript", metavar="PATH", help="Claude Codeのtranscriptパス。")
    session.add_argument("--codex-thread-id", metavar="ID", help="Codexのthread ID。")
    parser.add_argument(
        "--work-dir",
        metavar="PATH",
        required=True,
        help="メインが作成した管理対象一時領域の絶対パス。3つの文書と証拠bundleをこの直下へ書く。",
    )
    parser.add_argument(
        "--target-repo",
        metavar="PATH",
        help="振り返り対象のリポジトリ。対象リポジトリ固有の振り返り参照文書の解決に使う。",
    )
    return parser


def _missing(item: str, detail: str | None = None) -> int:
    """取得できなかった準備項目を報告する。"""
    print(f"不足: {item}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    return 2


def _reference_document(target_repo: pathlib.Path | None, *, codex: bool) -> pathlib.Path | None:
    """Git共通dirから対象リポジトリ固有の振り返り参照文書を解決する。"""
    if target_repo is None:
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(target_repo), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError:
        return None
    lines = result.stdout.splitlines()
    if result.returncode != 0 or len(lines) != 1:
        return None
    common_dir = pathlib.Path(lines[0])
    if not common_dir.is_absolute():
        common_dir = target_repo / common_dir
    repository_name = common_dir.resolve().parent.name
    path = pathlib.Path.home() / (".codex" if codex else ".claude") / "docs" / f"session-review-{repository_name}.md"
    return path if path.is_file() else None


def _read_jsonl(path: pathlib.Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _extract_bundle(session_arguments: list[str], bundle_dir: pathlib.Path) -> str | None:
    """抽出器の集約実行で証拠bundleを作成する。失敗時は診断の文字列を返す。

    抽出器は標準出力へ要約イベントを書くため、1行JSONの出力契約を保つよう取り込んで破棄する。
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = session_review_evidence.main([*session_arguments, "--bundle", str(bundle_dir)])
    return None if exit_code == 0 else captured.getvalue() or f"抽出器の終了コード: {exit_code}"


def _fence(body: str, info: str = "text") -> list[str]:
    """本文中のバッククォート連続より長いフェンスで囲み、本文中の見出しを見出しとして扱わせない。"""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", body.rstrip("\n"), fence]


def _one_line(text: str, limit: int = _SUMMARY_LENGTH) -> str:
    """空白を正規化した先頭の文字列を返す。"""
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[:limit] + "…"


def _conversation_document(utterances: list[dict[str, Any]], detail_command: str) -> str:
    """会話の流れを、発話ごとの役割、時刻、記録位置と本文で組み立てる。"""
    lines = [
        "# 会話の流れ",
        "",
        "メイン記録の利用者発話とアシスタント発話を時系列で並べる。自動挿入本文、実行環境の挿入、スキル展開、"
        f"ツール呼び出しとツール結果は含まない。{_UTTERANCE_FULL_LIMIT}字を超える発話は先頭と末尾の"
        f"{_UTTERANCE_EDGE_LENGTH}字ずつを載せる。全文は`{detail_command} --detail <記録位置>`で取得する。",
    ]
    if not utterances:
        lines.extend(["", "発話は0件である。"])
    for item in utterances:
        role = "利用者" if item["role"] == "user" else "アシスタント"
        locator = f"{item['record']}:{item['line']}"
        text = str(item["text"]).strip()
        lines.extend(["", f"## {role}（{item.get('timestamp') or '時刻なし'}、{locator}）", ""])
        if len(text) > _UTTERANCE_FULL_LIMIT:
            omitted = len(text) - 2 * _UTTERANCE_EDGE_LENGTH
            body = (
                f"{text[:_UTTERANCE_EDGE_LENGTH]}\n…（中間の{omitted}字を省略。全文は記録位置{locator}）…\n"
                f"{text[-_UTTERANCE_EDGE_LENGTH:]}"
            )
        else:
            body = text
        lines.extend(_fence(body))
    return "\n".join(lines) + "\n"


def _readable_entry_text(text: str) -> str:
    """抽出器がエントリ全体をJSONで返した詳細から、人が読む本文を取り出す。

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


def _tool_use_summary(event: dict[str, Any]) -> str:
    """対象のツール呼び出しを、ツール名と入力の要約1行で表す。"""
    tool_input = event.get("input")
    if isinstance(tool_input, dict) and isinstance(tool_input.get("command"), str):
        summary = tool_input["command"]
    else:
        summary = json.dumps(tool_input, ensure_ascii=False)
    return f"{event.get('name', '')} {_one_line(summary)}"


def _preceding_assistant_text(timeline: list[dict[str, Any]], record: str, line: int) -> str | None:
    """同じ記録で指定行以前にある最後のアシスタント発話を返す。"""
    preceding = [
        event
        for event in timeline
        if event.get("kind") in {"assistant", "final-result"}
        and event.get("record") == record
        and isinstance(event.get("line"), int)
        and int(event["line"]) <= line
        and isinstance(event.get("text"), str)
    ]
    return str(max(preceding, key=lambda event: int(event["line"]))["text"]) if preceding else None


def _candidate_lines(candidate: dict[str, Any], evidence: dict[str, Any], timeline: list[dict[str, Any]]) -> list[str]:
    """候補1件を、1行の要約、記録位置及び判断に要る補足で組み立てる。

    hook通知は通知が判定した入力を読まないと是非を判断できないため、直前のアシスタント発話を添える。
    """
    kind = str(candidate["candidate_kind"])
    occurrence = candidate.get("occurrence_count", candidate.get("count", 1))
    events = evidence.get("events", [])
    bodies = [
        _readable_entry_text(str(event["text"]))
        for event in events
        if event.get("kind") == "detail" and isinstance(event.get("text"), str) and event["text"].strip()
    ]
    text = bodies[0] if bodies else str(candidate.get("text", ""))
    lines = [f"- {candidate['candidate_id']} {kind}（発生{occurrence}件）: {_one_line(text) or '（本文なし）'}"]
    locators = ", ".join(f"{locator['record']}:{locator['line']}" for locator in candidate["locators"])
    omitted = candidate.get("omitted_locator_count", 0)
    lines.append(f"  - 記録位置: {locators}" + (f"（同じ種類の{omitted}件は省略）" if omitted else ""))
    lines.extend(f"  - 対象: {_tool_use_summary(event)}" for event in events if event.get("kind") == "tool-use")
    if kind == "hook-notice":
        first = candidate["locators"][0]
        preceding = _preceding_assistant_text(timeline, str(first["record"]), int(first["line"]))
        lines.append(f"  - 直前のアシスタント発話: {_one_line(preceding) if preceding else '（なし）'}")
    if kind in _FULL_TEXT_KINDS:
        lines.extend(["", *("  " + line if line else "" for line in _fence("\n\n".join(bodies) if bodies else text)), ""])
    return lines


def _candidates_document(
    candidates: list[dict[str, Any]],
    summary: dict[str, Any],
    evidence_by_id: dict[str, dict[str, Any]],
    timeline: list[dict[str, Any]],
    detail_command: str,
) -> str:
    """問題候補の一覧を組み立てる。"""
    counts = collections.Counter(str(item["candidate_kind"]) for item in candidates)
    count_text = "、".join(f"{kind} {count}件" for kind, count in sorted(counts.items())) or "なし"
    excluded = summary.get("excluded", {})
    excluded_text = "、".join(f"{name} {count}件" for name, count in sorted(excluded.items())) or "なし"
    lines = [
        "# 問題候補の一覧",
        "",
        f"- 候補: {len(candidates)}件（{count_text}）",
        f"- 抽出器が除外した件数: {excluded_text}",
        f"- 記録位置の全文は`{detail_command} --detail <記録位置>`で取得する",
        "",
    ]
    if not candidates:
        lines.append("抽出器が問題候補を返さなかった。")
    for candidate in candidates:
        lines.extend(_candidate_lines(candidate, evidence_by_id.get(str(candidate["candidate_id"]), {}), timeline))
    return "\n".join(lines).rstrip("\n") + "\n"


def _stats_document(stats: list[dict[str, Any]]) -> str:
    """セッション統計を組み立てる。"""
    by_kind: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for event in stats:
        by_kind[str(event.get("kind"))].append(event)
    total = next(iter(by_kind.get("stats-total", [])), {})
    lines = [
        "# セッション統計",
        "",
        f"- 経過秒: {total.get('elapsed_seconds', 'unknown')}秒（セッションの最初の記録から準備時点まで）",
    ]
    critical = next(iter(by_kind.get("stats-critical-path", [])), None)
    if critical is not None:
        segments = "、".join(f"{item.get('owner')} {item.get('exclusive_seconds')}秒" for item in critical.get("segments", []))
        lines.append(
            f"- 律速区間: メインだけの区間{critical.get('main_only_seconds')}秒、agent thread別の排他区間: {segments or 'なし'}"
        )
        if critical.get("unmeasured_threads"):
            lines.append(f"- 未計測のthread: {'、'.join(str(item) for item in critical['unmeasured_threads'])}")
    compaction = next(iter(by_kind.get("stats-compaction-total", [])), {})
    lines.append(
        f"- コンパクション: {compaction.get('count', 0)}回、合計{compaction.get('total_duration_seconds', 0)}秒"
        f"（所要時間不明{compaction.get('duration_unknown_count', 0)}回）"
    )
    threads = by_kind.get("stats-agent-thread", [])
    if threads:
        lines.extend(["", "| thread | 実行系 | 経過秒 | 応答回数 |", "| --- | --- | --- | --- |"])
        lines.extend(
            "| "
            + " | ".join(str(item.get(key, "unknown")) for key in ("thread", "engine", "elapsed_seconds", "api_messages"))
            + " |"
            for item in threads
        )
    slow = sorted(by_kind.get("stats-slow-call", []), key=lambda item: -float(item.get("seconds", 0)))[:_SLOW_CALL_LIMIT]
    if slow:
        lines.extend(["", "遅い呼び出しの上位:", ""])
        for item in slow:
            hint = " ".join(str(item.get("hint", "")).split())
            lines.append(f"- {item.get('tool')} {item.get('seconds')}秒" + (f": {hint}" if hint else ""))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, *, now: datetime.datetime | None = None) -> int:
    """振り返りの入力を作業ディレクトリへ書き、所在と件数を1行のJSONで出力する。"""
    args = _build_parser().parse_args(argv)
    local_evidence_script = pathlib.Path(__file__).resolve().with_name("session_review_evidence.py")
    try:
        evidence_script = run_script.registered_script_path("session-review-evidence")
    except ValueError:
        return _missing("evidence_script")
    if local_evidence_script != evidence_script:
        return _missing("evidence_script")

    work_dir = pathlib.Path(args.work_dir).expanduser()
    if not work_dir.is_absolute() or not work_dir.is_dir():
        return _missing("work_dir")
    transcript_path = pathlib.Path(args.transcript).expanduser().resolve() if args.transcript is not None else None
    if transcript_path is not None and not transcript_path.is_file():
        return _missing("transcript_path")
    target_repo = pathlib.Path(args.target_repo).expanduser().resolve() if args.target_repo is not None else None

    session_arguments = [str(transcript_path)] if transcript_path is not None else ["--codex-thread-id", args.codex_thread_id]
    bundle_dir = work_dir / "bundle"
    bundle_dir.mkdir(exist_ok=True)
    if (failure := _extract_bundle(session_arguments, bundle_dir)) is not None:
        return _missing("bundle", failure)
    try:
        candidate_rows = _read_jsonl(bundle_dir / "candidates.jsonl")
        stats = _read_jsonl(bundle_dir / "stats.jsonl")
        timeline = _read_jsonl(bundle_dir / "timeline.jsonl")
        utterances = _read_jsonl(bundle_dir / "conversation.jsonl")
        candidates = [row for row in candidate_rows if row.get("kind") == "candidate"]
        summary = next((row for row in candidate_rows if row.get("kind") == "candidate-summary"), {})
        evidence_by_id = {
            str(row["candidate_id"]): json.loads((bundle_dir / row["path"]).read_text(encoding="utf-8"))
            for row in _read_jsonl(bundle_dir / "candidate-evidence.jsonl")
        }
    except (OSError, ValueError, KeyError) as error:
        return _missing("bundle", str(error))

    # 抽出器の`--transcript`と`--codex-thread-id`へそのまま渡せる形を、全文を照会するコマンドとして示す。
    detail_command = (
        f"atk run-script session-review-evidence -- {transcript_path}"
        if transcript_path is not None
        else f"atk run-script session-review-evidence -- --codex-thread-id {args.codex_thread_id}"
    )
    conversation_path = work_dir / CONVERSATION_FILENAME
    candidates_path = work_dir / CANDIDATES_FILENAME
    stats_path = work_dir / STATS_FILENAME
    conversation_path.write_text(_conversation_document(utterances, detail_command), encoding="utf-8")
    candidates_path.write_text(
        _candidates_document(candidates, summary, evidence_by_id, timeline, detail_command), encoding="utf-8"
    )
    stats_path.write_text(_stats_document(stats), encoding="utf-8")

    reference_document = _reference_document(target_repo, codex=args.codex_thread_id is not None)
    total = next((event for event in stats if event.get("kind") == "stats-total"), {})
    compaction = next((event for event in stats if event.get("kind") == "stats-compaction-total"), {})
    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    role_counts = collections.Counter(str(item["role"]) for item in utterances)
    record = {
        "work_dir": str(work_dir),
        "conversation_path": str(conversation_path),
        "candidates_path": str(candidates_path),
        "stats_path": str(stats_path),
        "reference_document": str(reference_document) if reference_document is not None else None,
        "prepared_at": current.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "utterance_counts": {"user": role_counts.get("user", 0), "assistant": role_counts.get("assistant", 0)},
        "candidate_total": len(candidates),
        "candidate_counts": dict(sorted(collections.Counter(str(item["candidate_kind"]) for item in candidates).items())),
        "excluded_counts": dict(sorted(summary.get("excluded", {}).items())),
        "elapsed_seconds": total.get("elapsed_seconds"),
        "compaction_count": compaction.get("count", 0),
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
