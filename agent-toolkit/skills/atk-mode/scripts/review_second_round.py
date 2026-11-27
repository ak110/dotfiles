from __future__ import annotations

import argparse
import collections
import datetime
import json
import re
import sys
from pathlib import Path
from typing import Any

import draft_review_rounds
from agent_toolkit._atk import review_table_format as format_table
from agent_toolkit._common import host_homes, private_notes, session_record_paths
from cost_metrics import _history, saved_review_tables
from session_evidence_stats import _parse_cli_timestamp

_CLASSIFICATIONS = ("前回から残存", "修正に起因", "前回の見落とし", "新しい変更", "要求追加", "区分なし")


def rate(passed: int, failed: int) -> float | str:
    return passed / (passed + failed) if passed + failed else "判定不能"


def _withdrawn(row: list[str]) -> bool:
    origin = format_table.carried_origin(row[3])
    return origin is not None and row[6] == format_table.withdrawn_reason(origin, int(row[0]))


def exec_period(tables: list[list[list[str]]]) -> dict[str, Any]:
    passed = failed = undetermined = initial_issues = 0
    classifications: collections.Counter[str] = collections.Counter()
    maximum: collections.Counter[int] = collections.Counter()
    for rows in tables:
        track = [(number, row) for number, row in enumerate(rows, 1) if row[1] == "exec-review"]
        maximum[max((int(row[0]) for _, row in track), default=0)] += 1
        issues = [
            (number, row) for number, row in track if not row[2].strip().startswith("担当の記録:") and not _withdrawn(row)
        ]
        for _, row in issues:
            if int(row[0]) >= 2:
                category = format_table.classification(row[3])
                classifications[category if category in _CLASSIFICATIONS else "区分なし"] += 1
        first = [number for number, row in issues if int(row[0]) == 1]
        initial_issues += bool(first)
        if any(int(row[0]) == 2 for _, row in issues):
            failed += 1
        elif first and all(
            any("再確認: ラウンド2 完了" in cell.splitlines() for cell in format_table.display_row(rows, number)[5:7])
            for number in first
        ):
            passed += 1
        elif first:
            undetermined += 1
    return {
        "tables": len(tables),
        "initial_issues": initial_issues,
        "passed": passed,
        "failed": failed,
        "undetermined": undetermined,
        "pass_rate": rate(passed, failed),
        "later_classifications": {category: classifications[category] for category in _CLASSIFICATIONS},
        "maximum_round_distribution": dict(sorted(maximum.items())),
    }


def _period(
    notes: Path, history: list[tuple[str, str, datetime.datetime]], since: datetime.datetime, until: datetime.datetime
) -> dict[str, Any]:
    saved = saved_review_tables(notes, history, since, until)
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        **exec_period([table.raw_rows for table in saved.tables]),
        "unreadable_tables": saved.unreadable,
        "tables_without_history": saved.without_history,
    }


def _fields(body: str, name: str) -> tuple[str, ...]:
    return tuple(re.findall(rf"^(?:- )?{re.escape(name)}:[ \t]*(\S[^\n]*)$", body, re.MULTILINE))


def _other_review_period(
    collected: draft_review_rounds._Collected, role: str, since: datetime.datetime, until: datetime.datetime
) -> dict[str, Any]:
    counts = dict.fromkeys(("passed", "failed", "first_pass", "no_second", "unassigned", "unconfirmed_second"), 0)
    tracks = collections.defaultdict(list)
    for receipt in collected.receipts:
        if collected.roles.get(receipt.session) != role or receipt.timestamp > until:
            continue
        body = receipt.body
        round_labels = _fields(body, "判定の回数")
        verdicts = _fields(body, "判定")
        if role in {"reader-fit-review", "external-write-review"}:
            issues = _fields(body, "未解決の指摘数")
            if role == "reader-fit-review":
                documents = tuple(sorted(_fields(body, "成果物")))
            else:
                documents = ("投稿文面",)
                if not issues and _fields(body, "再レビュー"):
                    continue
            verdict = (
                ("合格" if int(issues[0]) == 0 else "不合格")
                if len(issues) == 1 and issues[0].isascii() and issues[0].isdigit() and _fields(body, "状態") == ("completed",)
                else ""
            )
        elif role == "usability-review":
            documents = ("画面",) if len(round_labels) == 1 and _fields(body, "レビューしたHEAD") else ()
            verdict = verdicts[0] if len(verdicts) == 1 else ""
        else:
            documents = _fields(body, "対象資料")
            verdict = verdicts[0] if len(verdicts) == 1 else ""
        if not documents or verdict not in {"合格", "不合格", "修正要", "未確定"}:
            counts["unassigned"] += int(since <= receipt.timestamp)
            continue
        tracks[collected.series[receipt.session], documents].append((receipt, verdict, round_labels))
    classifications: collections.Counter[str] = collections.Counter()
    for track in tracks.values():
        first, initial, first_round = track[0]
        if not since <= first.timestamp:
            continue
        if (first_round and first_round != ("1",)) or initial == "未確定":
            counts["unassigned"] += 1
        elif initial == "合格":
            counts["first_pass"] += 1
        elif len(track) < 2:
            counts["no_second"] += 1
        else:
            second, verdict, second_round = track[1]
            requested = any(
                first.timestamp <= request.timestamp <= second.timestamp and request.rejudgment
                for request in collected.requests.get(second.session, ())
            )
            if verdict == "未確定" or (second_round != ("2",) if second_round else not requested):
                counts["unconfirmed_second"] += 1
                continue
            counts["passed" if verdict == "合格" else "failed"] += 1
            classifications.update(_fields(second.body, "区分"))
            if any(value != "なし" for value in _fields(second.body, "新しい要件")):
                classifications["要求追加"] += 1
    return {
        **counts,
        "pass_rate": rate(counts["passed"], counts["failed"]),
        "second_classifications": {name: classifications[name] for name in _CLASSIFICATIONS},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="レビューの2回目の判定と、実行レビューの指摘の内訳を期間別に集計する。")
    parser.add_argument("--since", required=True, metavar="DATE", help="今回の期間の開始日時。時差なしはローカル時刻。")
    parser.add_argument("--until", metavar="DATE", help="今回の期間の終了日時。省略時は現在時刻。")
    parser.add_argument("--release-date", metavar="DATE", help="累計を数え始める配布日。")
    parser.add_argument("--private-notes", type=Path, metavar="DIR", help="管理表を保存したprivate-notesの作業ツリー。")
    parser.add_argument("--draft-review", action="store_true", help="投入前レビューの受領記録も集計する。")
    parser.add_argument("--other-reviews", action="store_true", help="計画・読者別・画面・投稿前の受領記録も集計する。")
    parser.add_argument(
        "--record-root", type=Path, metavar="DIR", action="append", help="受領を集計するセッション記録のルート。反復可。"
    )
    args = parser.parse_args(argv)
    try:
        since = _parse_cli_timestamp(args.since)
        until = _parse_cli_timestamp(args.until) if args.until else datetime.datetime.now().astimezone()
        release = _parse_cli_timestamp(args.release_date) if args.release_date is not None else None
        if since >= until:
            raise ValueError("開始が終了以後である")
        notes = (args.private_notes or private_notes.default_private_notes()).expanduser().resolve()
        history = _history(notes)
        current = _period(notes, history, since, until)
        previous = _period(notes, history, since - (until - since), since - datetime.timedelta(microseconds=1))
        result = {"current": current, "previous": previous}
        if release is not None:
            if release > until:
                raise ValueError("配布日が集計の終了より後である")
            result["since_release"] = _period(notes, history, release, until)
        if args.draft_review or args.other_reviews:
            roots = args.record_root or [
                host_homes.claude_config_dir() / "projects",
                *session_record_paths.codex_record_roots(host_homes.codex_home()),
            ]
            collected = draft_review_rounds.collect(roots, index=draft_review_rounds.index_path(roots))
            previous_since, previous_until = since - (until - since), since - datetime.timedelta(microseconds=1)
            if args.draft_review:
                result["draft_review"] = {
                    "current": draft_review_rounds.period(collected, since, until),
                    "previous": draft_review_rounds.period(collected, previous_since, previous_until),
                }
            if args.other_reviews:
                result["other_reviews"] = {
                    role: {
                        **({"coverage": "historical_formal_plan_review_only"} if role == "plan-review" else {}),
                        "current": _other_review_period(collected, role, since, until),
                        "previous": _other_review_period(collected, role, previous_since, previous_until),
                        **(
                            {"since_release": _other_review_period(collected, role, release, until)}
                            if release is not None
                            else {}
                        ),
                    }
                    for role in draft_review_rounds.OTHER_ROLES
                }
    except (OSError, ValueError) as error:
        print(f"2回目の合否を集計できない: {error}", file=sys.stderr)
        print("次の操作: 日時、private-notesの作業ツリーとGit履歴の読取を確認して再実行する", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
