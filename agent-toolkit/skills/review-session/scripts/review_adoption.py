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
from pathlib import Path
from typing import Any

from cost_metrics import _history, saved_review_tables
from session_evidence_stats import _parse_cli_timestamp

from agent_toolkit._atk import review_table
from agent_toolkit._common import private_notes
from agent_toolkit._common.json_lines import split_lines
from agent_toolkit._common.runtime_identity import distinct_identities

# 実行レビュー担当の記録だけを選ぶ標識。agents_serverは役割文書の所在を`出所:`の行で最初の入力へ配送する。
# 委譲元の記録にも同じ管理表の名前が現れるため、名前の一致だけでは帰属させない。
_ROLE_MARKER = re.compile(r"出所: \S*share/exec-review\.subagent\.md")
# Codexの記録で、モデルの出力として現れる`response_item`の種類。これより前の入力だけを依頼文の候補にする。
_CODEX_OUTPUT_TYPES = frozenset({"reasoning", "function_call", "custom_tool_call", "local_shell_call", "web_search_call"})
_MODEL_UNKNOWN = "モデル不明"
_LOCATION, _LEVEL, _NO_RESPONSE = 2, 4, 6


def _input_texts(entry: dict[str, Any]) -> list[str] | None:
    """記録の1行から、user役割の入力本文を返す。モデルの出力ならNoneを返す。"""
    message = entry.get("message")
    payload = entry.get("payload")
    content: Any = None
    if entry.get("type") == "assistant":
        return None
    if entry.get("type") == "user" and isinstance(message, dict):
        content = message.get("content")
    elif entry.get("type") == "response_item" and isinstance(payload, dict):
        if payload.get("type") in _CODEX_OUTPUT_TYPES:
            return None
        if payload.get("type") == "message":
            if payload.get("role") == "assistant":
                return None
            if payload.get("role") == "user":
                content = payload.get("content")
    if isinstance(content, str):
        return [content]
    if isinstance(content, list):
        return [block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)]
    return []


def _is_review_record(path: Path) -> bool:
    """最初のモデル出力より前の入力本文に、実行レビュー担当の役割文書の配送があるかを返す。

    依頼文より前に置かれる内容（道具の定義、規範の配送、環境の状態）の大きさは配布物の量で変わるため、
    先頭からの固定長では依頼文に届かない記録がある。記録の構造から依頼文の位置を決める。
    最初のモデル出力より後ろは読まない。標識の文字列を引用しただけの会話を、レビュー担当の記録と区別するためである。
    """
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                texts = _input_texts(entry)
                if texts is None:
                    return False
                if any(_ROLE_MARKER.search(text) for text in texts):
                    return True
    except OSError:
        return False
    return False


def _record_identity(path: Path, text: str) -> str:
    """実行レビュー担当の記録の本文から、観測したモデルを`engine:model/effort`で返す。"""
    runtime = "codex" if "/.codex/" in path.as_posix() or path.name.startswith("rollout-") else "claude"
    entries = []
    for line in split_lines(text):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    identities = distinct_identities(entries, runtime)
    return ",".join(identity.machine for identity in identities) or _MODEL_UNKNOWN


def _table_models(names: list[str], roots: list[Path]) -> dict[str, str]:
    """管理表のファイル名ごとに、その名前を本文に含む実行レビュー担当の記録から求めたモデルを返す。

    配布先にripgrepがあるとは限らないため、外部コマンドを使わずに走査する。
    全記録の本文を読むと重いため、最初のモデル出力より前の役割標識で対象を実行レビュー担当の記録に限定してから本文を読む。
    """
    if not names:
        return {}
    models: dict[str, set[str]] = collections.defaultdict(set)
    for root in roots:
        if not root.is_dir():
            continue
        for record in sorted(root.rglob("*.jsonl")):
            if not record.is_file() or not _is_review_record(record):
                continue
            try:
                text = record.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            matched = [name for name in names if name in text]
            if not matched:
                continue
            identity = _record_identity(record, text)
            for name in matched:
                models[name].add(identity)
    return {name: ",".join(sorted(values)) for name, values in models.items()}


def _empty_counts() -> dict[str, int]:
    return {"adopted": 0, "rejected": 0, "transferred": 0, "unanswered": 0}


def _period_events(
    period: str, since: datetime.datetime, until: datetime.datetime, tables: list[Any], models: dict[str, str]
) -> list[dict[str, Any]]:
    summary: dict[str, Any] = {
        "kind": "review-adoption-period",
        "period": period,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "table_count": len(tables),
        "excluded_judgment_count": 0,
        "excluded_judgments": [],
        **_empty_counts(),
        "by_level": collections.defaultdict(_empty_counts),
        "by_model": collections.defaultdict(lambda: {**_empty_counts(), "tables": 0}),
        "by_reason": collections.Counter(),
    }
    rejections: list[dict[str, Any]] = []
    for table in tables:
        model = models.get(table.path.name, _MODEL_UNKNOWN)
        summary["by_model"][model]["tables"] += 1
        summary["excluded_judgment_count"] += len(table.judgment_rows)
        summary["excluded_judgments"].extend({"table": str(table.path), "row": number} for number, _ in table.judgment_rows)
        for number, row in table.rows:
            state = review_table.response_state(row)
            level = row[_LEVEL] or "未記録"
            for counts in (summary, summary["by_level"][level], summary["by_model"][model]):
                counts[state] += 1
            if state == "rejected":
                reason = review_table.rejection_reason_class(row[_NO_RESPONSE])
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
    parser = argparse.ArgumentParser(description=__doc__)
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
    except (ValueError, OSError) as error:
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
