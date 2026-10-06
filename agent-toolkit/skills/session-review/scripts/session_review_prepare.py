"""セッション振り返りの入力を抽出し、会話の流れ、`candidates.md`およびセッション統計を作業ディレクトリへ書く。

1回の実行で証拠bundleを抽出し、メインが同じセッション内で読む3つの文書を書いて、所在と件数を1行のJSONで返す。
会話の流れはメイン記録の発話、ツール呼び出しおよび失敗の標識を時系列で並べ、メインが最初に全範囲を通読する。
原因と対策の確定、AWIの起草と投入はメインが自身のコンテキストで行うため、本スクリプトはキューを変更しない。
`candidates.md`は1候補を1行の要約と記録位置で示す。全文が要る候補だけを、
メインが`atk run-script session-review-evidence`の`--detail`で照会する。

本スクリプトはデータ取得を目的とし、合否を判定しないため、
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

from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import run_script
from agent_toolkit._common import atomic_file, file_lock
from agent_toolkit._common import next_action as _next_action

CONVERSATION_FILENAME = "conversation.md"
CANDIDATES_FILENAME = "candidates.md"
STATS_FILENAME = "stats.md"

_UTTERANCE_FULL_LIMIT = 1000
_UTTERANCE_EDGE_LENGTH = 500
"""会話の流れへ全文を載せる発話の上限と、それを超える発話で載せる先頭・末尾の文字数。

実際の記録では自動挿入本文を除いた発話の大半が1000字以下で、超える発話は記録あたり0〜2件だった。
流れをたどるには先頭と末尾で足り、全文が要る発話は記録位置から照会する。
ツール呼び出しと失敗の標識は1行（`_SUMMARY_LENGTH`字まで）で載せ、発話とは別の書式で区別する。
"""
_IMPROVEMENT_MARKER = "気付いた改善点:"
"""作業中に気付いた改善の機会をメインと委譲先が1行で伝える行の先頭。

振り返りは工程2でこの行を全件拾うため、長い発話の省略区間にあっても会話の流れへ残す。
"""
_SUMMARY_LENGTH = 200
"""`candidates.md`の1行の要約、対象のツール呼び出しおよび直前のアシスタント発話、会話の流れのツール呼び出しと失敗の標識へ載せる文字数。

ツール呼び出しの行を500字にすると大きい記録で会話の流れが約3割増えた一方、200字でコマンドとファイルパスを識別できた。
"""
_SLOW_CALL_LIMIT = 10
_FULL_TEXT_KINDS = frozenset({"user-intervention", "escalation"})
"""`candidates.md`へ本文の全文を載せる候補種別。ユーザーの是正と上位判断の要求は要約すると趣旨が変わるため全文を載せる。"""
_FAILURE_KINDS = frozenset({"command-failure", "tool-failure"})
_FAILURE_LEDGER_DAYS = 30


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
        help="メインがmanaged-tempに作成したディレクトリの絶対パス。3つの文書と証拠bundleをこの直下へ書く。",
    )
    parser.add_argument(
        "--target-repo",
        metavar="PATH",
        help="振り返り対象のリポジトリ。対象リポジトリ固有の振り返り参照文書の解決に使う。",
    )
    return parser


_MISSING_NEXT_ACTIONS = {
    "evidence_script": (
        "`atk run-script session-review-prepare -- <引数>`で起動する。"
        "解消しない場合はagent-toolkitの導入が壊れているため、ユーザーへ報告する"
    ),
    "work_dir": "`atk managed-temp create`で作成したディレクトリの絶対パスを`--work-dir`へ渡して再実行する",
    "transcript_path": (
        "`--transcript`へ実在するClaude Codeのtranscriptの絶対パスを渡すか、Codexでは`--codex-thread-id`を渡して再実行する"
    ),
    "bundle": (
        "表示した原因を確かめ、同じ引数で再実行する。記録が読めない場合は`--transcript`・`--codex-thread-id`の値を確かめる"
    ),
}
"""準備項目ごとの取得方法。項目名は標準エラーの`不足:`行と一致させる。"""


def _missing(item: str, detail: str | None = None) -> int:
    """取得できなかった準備項目を、項目に対応する引数と取得方法とともに報告する。"""
    print(f"不足: {item}", file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    print(_next_action.next_action_line(_MISSING_NEXT_ACTIONS[item]), file=sys.stderr)
    return 2


def _reference_document(target_repo: pathlib.Path | None, *, codex: bool) -> pathlib.Path | None:
    """Git共通ディレクトリ（`--git-common-dir`）から対象リポジトリ固有の振り返り参照文書を解決する。"""
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
    """`atk run-script session-review-evidence`の集約実行で証拠bundleを作成する。失敗時は診断の文字列を返す。

    `atk run-script session-review-evidence`は標準出力へ要約イベントを書くため、1行JSONの出力契約を保つよう取り込んで破棄する。
    """
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        exit_code = session_review_evidence.main([*session_arguments, "--bundle", str(bundle_dir)])
    return None if exit_code == 0 else captured.getvalue() or f"atk run-script session-review-evidenceの終了コード: {exit_code}"


def _fence(body: str, info: str = "text") -> list[str]:
    """本文中のバッククォート連続より長いフェンスで囲み、本文中の見出しを見出しとして扱わせない。"""
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{info}", body.rstrip("\n"), fence]


def _one_line(text: str, limit: int = _SUMMARY_LENGTH) -> str:
    """空白を正規化した先頭の文字列を返す。"""
    normalized = " ".join(text.split())
    return normalized if len(normalized) <= limit else normalized[:limit] + "…"


def _conversation_document(events: list[dict[str, Any]], detail_command: str) -> str:
    """会話の流れを、発話と、ツール呼び出し・失敗の標識の行で時系列に組み立てる。

    発話は本文を改変せずフェンスで囲む（逐語引用の出所に使われるため）。
    ツール呼び出しと失敗の標識はフェンスの外のリスト行とし、発話と区別できるようにする。
    """
    lines = [
        "# 会話の流れ",
        "",
        "メイン記録のユーザー発話、アシスタント発話およびツール呼び出しを時系列で並べる。"
        f"ツール呼び出しはツール名と代表入力（{_SUMMARY_LENGTH}字まで。`Write`・`Edit`は対象ファイルだけ）の1行で示し、"
        "失敗したツール結果は直後に診断の1行を示す。"
        "成功したツール結果の本文、自動挿入本文、実行環境の挿入、スキル展開、hookの追加コンテキスト、thinkingおよび委譲先の記録の内部は含まない。"
        f"{_UTTERANCE_FULL_LIMIT}字を超える発話は先頭と末尾の{_UTTERANCE_EDGE_LENGTH}字ずつを載せ、"
        f"省略した中間にある`{_IMPROVEMENT_MARKER}`で始まる行は省略の標識の後へ全て載せる。"
        f"発話とツール呼び出し・結果の全文は`{detail_command} --detail <記録位置>`で取得する。",
    ]
    if not events:
        lines.extend(["", "発話とツール呼び出しは0件である。"])
    calls: dict[str, int] = {}
    for item in events:
        locator = f"{item['record']}:{item['line']}"
        kind = item.get("kind", "utterance")
        if kind == "tool-call":
            if item.get("call_id"):
                calls[str(item["call_id"])] = len(lines)
            lines.extend(
                ["", f"- ツール呼び出し（{locator}）: {item.get('tool') or '名前なし'} {_one_line(str(item['text']))}".rstrip()]
            )
            continue
        if kind == "tool-failure":
            failure = f"  - 失敗（{locator}）: {_one_line(str(item['text'])) or '診断なし'}"
            position = calls.get(str(item.get("call_id")))
            if position is not None and position + 1 < len(lines) and lines[position + 1].startswith("- ツール呼び出し"):
                # 並列の呼び出しでは結果の行が後の呼び出しより後に現れるため、対応する呼び出しの直後へ置く。
                lines.insert(position + 2, failure)
                calls = {key: value + 1 if value > position else value for key, value in calls.items()}
            else:
                lines.append(failure)
            continue
        role = "ユーザー" if item["role"] == "user" else "アシスタント"
        text = str(item["text"]).strip()
        lines.extend(["", f"## {role}（{item.get('timestamp') or '時刻なし'}、{locator}）", ""])
        if len(text) > _UTTERANCE_FULL_LIMIT:
            omitted = len(text) - 2 * _UTTERANCE_EDGE_LENGTH
            kept = "".join(f"{line}\n" for line in _omitted_improvement_lines(text))
            body = (
                f"{text[:_UTTERANCE_EDGE_LENGTH]}\n…（中間の{omitted}字を省略。全文は記録位置{locator}）…\n"
                f"{kept}{text[-_UTTERANCE_EDGE_LENGTH:]}"
            )
        else:
            body = text
        lines.extend(_fence(body))
    return "\n".join(lines) + "\n"


def _omitted_improvement_lines(text: str) -> list[str]:
    """先頭と末尾の載せる範囲に全体が収まらない`気付いた改善点:`の行を、出現順に全文で返す。"""
    kept: list[str] = []
    start = 0
    tail_start = len(text) - _UTTERANCE_EDGE_LENGTH
    for line in text.splitlines(keepends=True):
        end = start + len(line)
        if line.lstrip().startswith(_IMPROVEMENT_MARKER) and end > _UTTERANCE_EDGE_LENGTH and start < tail_start:
            kept.append(line.strip())
        start = end
    return kept


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
    """候補1件を、1行の要約、記録位置および判断に要る補足で組み立てる。

    hook通知は通知が判定した入力を読まないと是非を判断できないため、直前のアシスタント発話を添える。
    その場のコードによる加工は、記録ごとの件数と、呼び出しごとの記録位置と代表入力の一覧で示す。
    加工の目的と反復は呼び出しの列を見比べて判定するため、代表の1件だけでは判断できない。
    """
    kind = str(candidate["candidate_kind"])
    occurrence = candidate.get("occurrence_count", candidate.get("count", 1))
    if kind == "adhoc-processing":
        record = candidate["locators"][0]["record"]
        lines = [f"- {candidate['candidate_id']} {kind}（発生{occurrence}件）: 記録{record}のその場のコードによる加工"]
        lines.extend(
            f"  - {call['record']}:{call['line']}: {_one_line(str(call.get('text', ''))) or '（入力なし）'}"
            for call in candidate.get("calls", [])
        )
        return lines
    events = evidence.get("events", [])
    bodies = [
        _readable_entry_text(str(event["text"]))
        for event in events
        if event.get("kind") == "detail" and isinstance(event.get("text"), str) and event["text"].strip()
    ]
    text = bodies[0] if bodies else str(candidate.get("text", ""))
    readable = candidate.get("failure_summary") if kind in _FAILURE_KINDS else text
    sessions = candidate.get("failure_session_count")
    session_count = f"、{sessions}セッション" if isinstance(sessions, int) else ""
    heading = f"- {candidate['candidate_id']} {kind}（発生{occurrence}件{session_count}）: "
    lines = [heading + (_one_line(str(readable)) or "（本文なし）")]
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
    single_failures: list[dict[str, Any]],
) -> str:
    """`candidates.md`の本文を組み立てる。"""
    counts = collections.Counter(str(item["candidate_kind"]) for item in candidates)
    count_text = "、".join(f"{kind} {count}件" for kind, count in sorted(counts.items())) or "なし"
    excluded = summary.get("excluded", {})
    excluded_text = "、".join(f"{name} {count}件" for name, count in sorted(excluded.items())) or "なし"
    lines = [
        "# 問題候補",
        "",
        f"- 候補: {len(candidates)}件（{count_text}）",
        f"- 候補から除いた件数: {excluded_text}",
        f"- 記録位置の全文は`{detail_command} --detail <記録位置>`で取得する",
        "",
    ]
    if not candidates:
        lines.append("候補として残す問題は無かった。")
    for candidate in candidates:
        lines.extend(_candidate_lines(candidate, evidence_by_id.get(str(candidate["candidate_id"]), {}), timeline))
    if single_failures:
        lines.extend(
            ["", "## 単発の失敗（件数のみ）", "", "| 失敗署名の要約 | 今回の発生件数 | 代表の記録位置 |", "| --- | --- | --- |"]
        )
        for candidate in single_failures:
            first = candidate["locators"][0]
            summary_text = str(candidate.get("failure_summary", "診断なし")).replace("|", "\\|")
            lines.append(
                f"| {_one_line(summary_text)} | {candidate.get('occurrence_count', candidate.get('count', 1))} | "
                f"{first['record']}:{first['line']} |"
            )
    return "\n".join(lines).rstrip("\n") + "\n"


def _failure_ledger(candidates: list[dict[str, Any]], session_id: str, now: datetime.datetime) -> tuple[dict[str, int], int]:
    """排他下で過去30日の失敗署名を更新し、署名ごとの最上位セッション数を返す。"""
    path = _atk_config.state_dir() / "session-review" / "failure-signatures.jsonl"
    lock_path = path.with_name(path.name + ".lock")
    cutoff = now.astimezone(datetime.UTC) - datetime.timedelta(days=_FAILURE_LEDGER_DAYS)
    skipped = 0
    records: dict[tuple[str, str], dict[str, str]] = {}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as lock:
            file_lock.acquire_lock(lock)
            try:
                try:
                    lines = path.read_text(encoding="utf-8").splitlines()
                except FileNotFoundError:
                    lines = []
                except (OSError, UnicodeError):
                    lines = []
                    skipped += 1
                for line in lines:
                    try:
                        item = json.loads(line)
                        if not isinstance(item, dict):
                            raise ValueError("記録がobjectではない")
                        signature, recorded_session, observed_at, summary = (
                            item["failure_signature"],
                            item["session_id"],
                            item["observed_at"],
                            item["failure_summary"],
                        )
                        if not all(
                            isinstance(value, str) and value for value in (signature, recorded_session, observed_at, summary)
                        ):
                            raise ValueError("記録欄が不正")
                        timestamp = datetime.datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
                        if timestamp.tzinfo is None:
                            raise ValueError("時刻のtimezoneが無い")
                    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
                        skipped += 1
                        continue
                    if timestamp >= cutoff:
                        records.setdefault((signature, recorded_session), item)
                for candidate in candidates:
                    signature = candidate.get("failure_signature")
                    if not isinstance(signature, str):
                        continue
                    records.setdefault(
                        (signature, session_id),
                        {
                            "failure_signature": signature,
                            "session_id": session_id,
                            "observed_at": now.astimezone(datetime.UTC).isoformat(),
                            "failure_summary": str(candidate.get("failure_summary", "診断なし")),
                        },
                    )
                payload = "".join(json.dumps(item, ensure_ascii=False) + "\n" for _, item in sorted(records.items()))
                atomic_file.atomic_write(path, payload)
            finally:
                file_lock.release_lock(lock)
    except OSError:
        skipped += 1
        return {}, skipped
    counts = collections.Counter(signature for signature, _ in records)
    return dict(counts), skipped


def _select_repeated_failures(
    candidates: list[dict[str, Any]], summary: dict[str, Any], session_id: str, now: datetime.datetime
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], int]:
    """`atk run-script session-review-evidence`が検出した失敗候補を別セッションの反復と単発へ分ける。"""
    failures = [item for item in candidates if item.get("candidate_kind") in _FAILURE_KINDS]
    session_counts, skipped = _failure_ledger(failures, session_id, now)
    retained: list[dict[str, Any]] = []
    singles: list[dict[str, Any]] = []
    excluded = collections.Counter(summary.get("excluded", {}))
    for candidate in candidates:
        if candidate.get("candidate_kind") not in _FAILURE_KINDS:
            retained.append(candidate)
            continue
        count = session_counts.get(str(candidate.get("failure_signature", "")), 1)
        if count >= 2:
            candidate["failure_session_count"] = count
            retained.append(candidate)
        else:
            singles.append(candidate)
            excluded["single-session-failure"] += int(candidate.get("occurrence_count", candidate.get("count", 1)))
    return retained, singles, {**summary, "excluded": dict(sorted(excluded.items()))}, skipped


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
        lines.extend(["", "| thread | 実行系 | 観測identity | 経過秒 | 応答回数 |", "| --- | --- | --- | --- | --- |"])
        lines.extend(
            "| "
            + " | ".join(
                [
                    str(item.get("thread", "unknown")),
                    str(item.get("engine", "unknown")),
                    ", ".join(
                        f"{identity.get('engine')}:{identity.get('model')}/{identity.get('effort')}"
                        for identity in item.get("observed_identities", [])
                    )
                    or "unknown",
                    str(item.get("elapsed_seconds", "unknown")),
                    str(item.get("api_messages", "unknown")),
                ]
            )
            + " |"
            for item in threads
        )
    slow = sorted(by_kind.get("stats-slow-call", []), key=lambda item: -float(item.get("seconds", 0)))[:_SLOW_CALL_LIMIT]
    if slow:
        lines.extend(["", "遅い呼び出しの上位:", ""])
        for item in slow:
            hint = " ".join(str(item.get("hint", "")).split())
            lines.append(f"- {item.get('tool')} {item.get('seconds')}秒" + (f": {hint}" if hint else ""))
    if critical is not None:
        for segment in critical.get("segments", []):
            lines.extend(_thread_breakdown_lines(segment, threads, by_kind))
    return "\n".join(lines) + "\n"


def _thread_breakdown_lines(
    segment: dict[str, Any], threads: list[dict[str, Any]], by_kind: dict[str, list[dict[str, Any]]]
) -> list[str]:
    """律速threadの内部の工程、待機および反復の内訳の節を組み立てる。

    委譲先の返却本文の説明ではなく記録から得た内訳を、振り返りが追加の照会なしで読めるようにする。
    件数の上限はメイン記録の集計と同じであり、記録位置は`--detail`へそのまま渡せる。
    """
    owner = segment.get("owner")
    thread = next((item for item in threads if item.get("thread") == owner), {})

    def of_thread(kind: str) -> list[dict[str, Any]]:
        return [item for item in by_kind.get(kind, []) if item.get("thread") == owner]

    def with_hint(text: str, item: dict[str, Any]) -> str:
        hint = _one_line(str(item.get("hint", "")))
        return f"{text}: {hint}" if hint else text

    lines = [
        "",
        f"## 律速threadの内訳: {owner}",
        "",
        f"- 排他区間: {segment.get('exclusive_seconds')}秒",
        f"- turnの経過秒: {thread.get('turn_elapsed_seconds', 'unknown')}秒",
    ]
    tools = of_thread("stats-thread-tool")
    lines.append(
        "- ツール別: "
        + ("、".join(f"{item.get('tool')} {item.get('count')}件{item.get('total_seconds')}秒" for item in tools) or "なし")
    )
    gaps = of_thread("stats-thread-gap")
    lines.append("- 60秒以上の間隔:" + ("" if gaps else " なし"))
    lines.extend(f"  - {item.get('seconds')}秒: {item.get('before')}〜{item.get('after')}" for item in gaps)
    repeats = of_thread("stats-thread-repeat")
    lines.append("- 反復:" + ("" if repeats else " なし"))
    lines.extend(
        with_hint(f"  - {item.get('tool')} {item.get('count')}回（{'、'.join(item.get('locations', []))}）", item)
        for item in repeats
    )
    slow = of_thread("stats-thread-slow-call")
    lines.append("- 遅い呼び出しの上位:" + ("" if slow else " なし"))
    lines.extend(with_hint(f"  - {item.get('tool')} {item.get('seconds')}秒 {item.get('location')}", item) for item in slow)
    return lines


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
        conversation = _read_jsonl(bundle_dir / "conversation.jsonl")
        candidates = [row for row in candidate_rows if row.get("kind") == "candidate"]
        summary = next((row for row in candidate_rows if row.get("kind") == "candidate-summary"), {})
        evidence_by_id = {
            str(row["candidate_id"]): json.loads((bundle_dir / row["path"]).read_text(encoding="utf-8"))
            for row in _read_jsonl(bundle_dir / "candidate-evidence.jsonl")
        }
    except (OSError, ValueError, KeyError) as error:
        return _missing("bundle", str(error))

    # `atk run-script session-review-evidence`の`--transcript`と`--codex-thread-id`へそのまま渡せる形を、
    # 全文を照会するコマンドとして示す。
    detail_command = (
        f"atk run-script session-review-evidence -- {transcript_path}"
        if transcript_path is not None
        else f"atk run-script session-review-evidence -- --codex-thread-id {args.codex_thread_id}"
    )
    conversation_path = work_dir / CONVERSATION_FILENAME
    candidates_path = work_dir / CANDIDATES_FILENAME
    stats_path = work_dir / STATS_FILENAME
    conversation_path.write_text(_conversation_document(conversation, detail_command), encoding="utf-8")
    current = now if now is not None else datetime.datetime.now(datetime.UTC)
    session_id = transcript_path.stem if transcript_path is not None else str(args.codex_thread_id)
    candidates, single_failures, summary, ledger_skipped = _select_repeated_failures(candidates, summary, session_id, current)
    candidates_path.write_text(
        _candidates_document(candidates, summary, evidence_by_id, timeline, detail_command, single_failures), encoding="utf-8"
    )
    stats_path.write_text(_stats_document(stats), encoding="utf-8")

    reference_document = _reference_document(target_repo, codex=args.codex_thread_id is not None)
    total = next((event for event in stats if event.get("kind") == "stats-total"), {})
    compaction = next((event for event in stats if event.get("kind") == "stats-compaction-total"), {})
    role_counts = collections.Counter(
        str(item["role"]) for item in conversation if item.get("kind", "utterance") == "utterance"
    )
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
        "failure_ledger_skipped": ledger_skipped,
        "elapsed_seconds": total.get("elapsed_seconds"),
        "compaction_count": compaction.get("count", 0),
    }
    print(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
