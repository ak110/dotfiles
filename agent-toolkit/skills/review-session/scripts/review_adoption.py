"""保存済みの実行レビュー指摘管理表から、指摘の採用・不採用を期間ごとに集計する。

振り返りのbatchが、不採用の多いレビュー担当のモデルやlevel、同じ理由の不採用の反復を改善候補にするための入力を返す。
表の読込と期間の割当は`cost_metrics.saved_review_tables`を共有する。
レビュー担当のモデルは、表のファイル名を本文に含み、実行レビュー担当の役割文書を配送されたセッション記録から求める。
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from cost_metrics import _history, saved_review_tables
from session_evidence_stats import _parse_cli_timestamp

from agent_toolkit._common import private_notes
from agent_toolkit._common.runtime_identity import distinct_identities

# 実行レビュー担当の記録だけを選ぶ標識。agents_serverは役割文書の所在を`出所:`の行で最初の入力へ配送する。
# 委譲元の記録にも同じ管理表の名前が現れるため、名前の一致だけでは帰属させない。
_ROLE_MARKER = re.compile(r"出所: \S*share/exec-review\.subagent\.md")
_ROLE_SCAN_BYTES = 256 * 1024
# 判断主体を示す不採用（メイン・レーン担当・統括・ユーザーの判断による見送り）。それ以外はレビュー担当側の不採用とする。
_JUDGMENT_REASON = re.compile(r"根拠の所在:[\s`]*(メイン|レーン担当|統括|ユーザー)")
_MODEL_UNKNOWN = "モデル不明"
_LOCATION, _LEVEL, _RESPONSE, _NO_RESPONSE = 2, 4, 5, 6


def _reason_class(text: str) -> str:
    return "判断による見送り" if _JUDGMENT_REASON.search(text) else "レビュー担当の前提の誤り・撤回"


def _record_identity(path: Path) -> str | None:
    """実行レビュー担当の記録なら観測したモデルを`engine:model/effort`で返し、他の記録はNoneを返す。"""
    try:
        with path.open("rb") as stream:
            head = stream.read(_ROLE_SCAN_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return None
    if not _ROLE_MARKER.search(head):
        return None
    runtime = "codex" if "/.codex/" in path.as_posix() or path.name.startswith("rollout-") else "claude"
    entries = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    identities = distinct_identities(entries, runtime)
    return ",".join(identity.machine for identity in identities) or _MODEL_UNKNOWN


def _table_models(names: list[str], roots: list[Path]) -> dict[str, str]:
    """管理表のファイル名ごとに、その名前を本文に含む実行レビュー担当の記録から求めたモデルを返す。"""
    existing = [str(root) for root in roots if root.is_dir()]
    if not names or not existing:
        return {}
    result = subprocess.run(
        ["rg", "-l", "-F", "--glob", "*.jsonl", *[part for name in names for part in ("-e", name)], "--", *existing],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode not in (0, 1):
        raise OSError(f"セッション記録の検索が失敗した（終了コード{result.returncode}）: {result.stderr.strip()}")
    models: dict[str, set[str]] = collections.defaultdict(set)
    for record in result.stdout.splitlines():
        identity = _record_identity(Path(record))
        if identity is None:
            continue
        text = Path(record).read_text(encoding="utf-8", errors="replace")
        for name in names:
            if name in text:
                models[name].add(identity)
    return {name: ",".join(sorted(values)) for name, values in models.items()}


def _empty_counts() -> dict[str, int]:
    return {"adopted": 0, "rejected": 0, "unanswered": 0}


def _period_events(
    period: str, since: datetime.datetime, until: datetime.datetime, tables: list[Any], models: dict[str, str]
) -> list[dict[str, Any]]:
    summary: dict[str, Any] = {
        "kind": "review-adoption-period",
        "period": period,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "table_count": len(tables),
        **_empty_counts(),
        "by_level": collections.defaultdict(_empty_counts),
        "by_model": collections.defaultdict(lambda: {**_empty_counts(), "tables": 0}),
        "by_reason": collections.Counter(),
    }
    rejections: list[dict[str, Any]] = []
    for table in tables:
        model = models.get(table.path.name, _MODEL_UNKNOWN)
        summary["by_model"][model]["tables"] += 1
        for number, row in enumerate(table.rows, 1):
            if row[_RESPONSE].strip():
                state = "adopted"
            elif row[_NO_RESPONSE].strip():
                state = "rejected"
            else:
                state = "unanswered"
            level = row[_LEVEL] or "未記録"
            for counts in (summary, summary["by_level"][level], summary["by_model"][model]):
                counts[state] += 1
            if state == "rejected":
                reason = _reason_class(row[_NO_RESPONSE])
                summary["by_reason"][reason] += 1
                rejections.append(
                    {
                        "kind": "review-adoption-rejection",
                        "period": period,
                        "table": str(table.path),
                        "row": number,
                        "level": level,
                        "model": model,
                        "reason_class": reason,
                        "location": row[_LOCATION],
                    }
                )
    summary["by_level"] = dict(sorted(summary["by_level"].items()))
    summary["by_model"] = dict(sorted(summary["by_model"].items()))
    summary["by_reason"] = dict(sorted(summary["by_reason"].items()))
    return [summary, *rejections]


def main(argv: list[str] | None = None) -> int:
    """公開CLIから期間を受け取り、今回と直前の同じ長さの期間の採否集計をJSON Linesで返す。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True, metavar="DATE", help="今回の期間の開始日時（必須）。時差なしはローカル時刻。")
    parser.add_argument("--until", metavar="DATE", help="今回の期間の終了日時。省略時は実行時刻。")
    parser.add_argument(
        "--private-notes",
        type=Path,
        metavar="DIR",
        help="private-notesのGit作業ツリー。省略時はatk config get private_notesと同じ解決。",
    )
    parser.add_argument(
        "--claude-projects-dir", type=Path, metavar="DIR", help="Claude記録の所在。省略時は~/.claude/projects。"
    )
    parser.add_argument("--codex-sessions-dir", type=Path, metavar="DIR", help="Codex記録の所在。省略時は~/.codex/sessions。")
    args = parser.parse_args(argv)
    try:
        since = _parse_cli_timestamp(args.since)
        until = _parse_cli_timestamp(args.until) if args.until else datetime.datetime.now().astimezone()
        if since >= until:
            raise ValueError("開始が終了以後である")
        previous_since = since - (until - since)
        notes = (args.private_notes or private_notes.default_private_notes()).expanduser().resolve()
        history = _history(notes)
        current = saved_review_tables(notes, history, since, until)
        previous = saved_review_tables(notes, history, previous_since, since - datetime.timedelta(microseconds=1))
        roots = [
            (args.claude_projects_dir or Path.home() / ".claude" / "projects").expanduser(),
            (args.codex_sessions_dir or Path.home() / ".codex" / "sessions").expanduser(),
        ]
        models = _table_models([table.path.name for table in (*current.tables, *previous.tables)], roots)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "text": str(error),
                    "next_action": "次の操作: 日時、private-notesとセッション記録の所在、Git履歴の読取権限を確認して再実行する",
                },
                ensure_ascii=False,
            )
        )
        return 2
    events = [
        *_period_events("previous", previous_since, since, previous.tables, models),
        *_period_events("current", since, until, current.tables, models),
        {
            "kind": "review-adoption-coverage",
            "unreadable_table_count": current.unreadable,
            "tables_without_history_count": current.without_history,
            "record_roots": [str(root) for root in roots],
            "model_unknown_note": "記録が残っていない表と実行レビュー担当を特定できない表はモデル不明として数える",
        },
    ]
    for event in events:
        print(json.dumps(event, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
