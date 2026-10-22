"""期間内のルートセッション、採用AWI、実行レビューをローカル週ごとに手動集計する。"""

from __future__ import annotations

import argparse
import collections
import dataclasses
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml
from session_evidence_catalog import _workflow_evidence
from session_evidence_extract import _CollectedRecord, _load_records, _parse_timestamp, _record_timestamp, _Runtime
from session_evidence_records import _agents_server_call_ids, _collect_records, _thread_ids_from_record
from session_evidence_stats import _add_tokens, _codex_normalized_tokens, _parse_cli_timestamp, _stats_summary_data

from agent_toolkit._agents_server import record_paths
from agent_toolkit._atk import review_table, session_record_format
from agent_toolkit._common import model_pricing, private_notes, session_cost
from agent_toolkit._git import command as git_command

_TOKEN_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")


@dataclasses.dataclass
class _Session:
    item: _CollectedRecord
    start: datetime.datetime | None


def _week(moment: datetime.datetime) -> str:
    day = moment.astimezone().date()
    return (day - datetime.timedelta(days=day.weekday())).isoformat()


def _empty_tokens() -> dict[str, int]:
    return dict.fromkeys(_TOKEN_KEYS, 0)


def _new_week(day: str) -> dict[str, Any]:
    return {
        "kind": "cost-metrics-week",
        "week": day,
        "adopted_awi_count": 0,
        "tokens": {
            scope: {"total": _empty_tokens(), "by_runtime": {runtime: _empty_tokens() for runtime in ("claude", "codex")}}
            for scope in ("all", "process-wi")
        },
        "root_session_count": {"all": 0, "process-wi": 0},
        "review": {
            "table_count": 0,
            "rounds": dict.fromkeys(("0", "1", "2", "3", "4", "5", "6+"), 0),
            "levels": dict.fromkeys((*review_table.LEVEL_VALUES, "未記録"), 0),
            "finding_count": 0,
            "without_modification_count": 0,
        },
    }


def _git(repo: Path, *args: str) -> str:
    result = git_command.run(args, cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


def _history(repo: Path) -> list[tuple[str, str, datetime.datetime]]:
    """各パスの追加commitと、そのcommit日時を履歴順で返す。"""
    history: list[tuple[str, str, datetime.datetime]] = []
    commit = ""
    timestamp: datetime.datetime | None = None
    for line in _git(
        repo,
        "-c",
        "core.quotePath=false",
        "log",
        "--no-renames",
        "--reverse",
        "--format=COMMIT %H %cI",
        "--name-only",
        "--diff-filter=A",
    ).splitlines():
        if line.startswith("COMMIT "):
            _, commit, time_text = line.split()
            timestamp = _parse_cli_timestamp(time_text)
        elif line and timestamp is not None:
            history.append((line, commit, timestamp))
    return history


def _queue_metrics(
    repo: Path, weeks: dict[str, dict[str, Any]], since: datetime.datetime, until: datetime.datetime
) -> dict[str, Any]:
    history = _history(repo)
    adopted: dict[str, set[str]] = collections.defaultdict(set)
    first_additions: dict[str, datetime.datetime] = {}
    for path, commit, timestamp in history:
        first_additions.setdefault(path, timestamp)
        if "adopted" not in Path(path).parts or not path.endswith(".md") or not since <= timestamp <= until:
            continue
        text = _git(repo, "show", f"{commit}:{path}")
        if text.startswith("---\n") and "\n---" in text[4:]:
            frontmatter = text[4:].split("\n---", 1)[0]
            metadata = yaml.safe_load(frontmatter)
            if isinstance(metadata, dict) and metadata.get("type") == "awi":
                adopted[_week(timestamp)].add(Path(path).name)
    for week, names in adopted.items():
        weeks[week]["adopted_awi_count"] = len(names)

    tables = sorted((repo / "plans").rglob("*.exec-review.tsv"))
    unreadable = 0
    without_history = 0
    for path in tables:
        timestamp = first_additions.get(path.relative_to(repo).as_posix())
        if timestamp is None:
            without_history += 1
            continue
        if not since <= timestamp <= until:
            continue
        try:
            rows = [row for row in review_table.read_rows(path) if row[1] == "exec-review"]
            rounds = max((int(row[0]) for row in rows), default=0)
        except OSError, UnicodeError, ValueError:
            unreadable += 1
            continue
        review = weeks[_week(timestamp)]["review"]
        review["table_count"] += 1
        review["rounds"][str(rounds) if rounds < 6 else "6+"] += 1
        review["finding_count"] += len(rows)
        for row in rows:
            level = row[4] or "未記録"
            review["levels"][level] = review["levels"].get(level, 0) + 1
            review["without_modification_count"] += bool(row[6].strip()) and not row[5].strip()
    saved_names = {path.name for path in tables}
    unsaved = sum(path.name not in saved_names for path in (Path.home() / ".claude" / "plans").glob("*.exec-review.tsv"))
    return {
        "unreadable_table_count": unreadable,
        "tables_without_history_count": without_history,
        "unsaved_table_count": unsaved,
    }


def _scan_sessions(
    roots: dict[str, Path], since: datetime.datetime
) -> tuple[list[_Session], dict[str, tuple[Path, _Runtime]], dict[str, Any]]:
    sessions: list[_Session] = []
    paths: dict[str, list[tuple[Path, _Runtime]]] = collections.defaultdict(list)
    referenced: set[str] = set()
    runtime_coverage: dict[str, dict[str, Any]] = {
        runtime: {"oldest_record_at": None, "unreadable_record_count": 0} for runtime in roots
    }
    coverage: dict[str, Any] = {
        "by_runtime": runtime_coverage,
        "unknown_start_count": 0,
    }
    for runtime, root in roots.items():
        oldest: datetime.datetime | None = None
        for path in sorted(root.rglob("*.jsonl")):
            if runtime == "agy" and (
                path.name not in {"transcript.jsonl", "transcript_full.jsonl"}
                or path.name == "transcript.jsonl"
                and path.with_name("transcript_full.jsonl").is_file()
            ):
                continue
            if runtime == "codex" and not path.name.startswith("rollout-"):
                continue
            try:
                if path.stat().st_mtime < since.timestamp():
                    identity = (
                        session_record_format.codex_session_id(path)
                        if runtime == "codex"
                        else session_record_format.agy_session_id(path)
                        if runtime == "agy"
                        else path.stem
                    )
                    paths[identity].append((path, "codex" if runtime == "codex" else "agy" if runtime == "agy" else "claude"))
                    continue
                records = _load_records(str(path))
            except OSError:
                records = None
            if not records:
                runtime_coverage[runtime]["unreadable_record_count"] += 1
                continue
            collected, _ = _collect_records(str(path), records, main_only=True)
            item = collected[0]
            if item.runtime is None:
                runtime_coverage[runtime]["unreadable_record_count"] += 1
                continue
            timestamps = [timestamp for record in records if (timestamp := _record_timestamp(record)) is not None]
            start = min(timestamps) if timestamps else None
            if start is not None:
                oldest = min(oldest, start) if oldest is not None else start
            else:
                coverage["unknown_start_count"] += 1
            session_id = item.record_id.partition(":")[2]
            paths[session_id].append((path, item.runtime))
            calls = _agents_server_call_ids(records)
            for record in records:
                referenced.update(_thread_ids_from_record(record, calls))
                payload = record.entry.get("payload")
                source = payload.get("source") if isinstance(payload, dict) else None
                if isinstance(source, dict) and "subagent" in source:
                    referenced.add(session_id)
            if path.parent.name == "subagents":
                referenced.add(session_id)
            # 走査全体では本文を保持せず、各ルートの集計時だけ再帰収集する。
            sessions.append(_Session(item._replace(records=[]), start))
        runtime_coverage[runtime]["oldest_record_at"] = oldest.isoformat() if oldest else None
    candidates = {identity: items[0] for identity, items in paths.items() if len(items) == 1}
    return [session for session in sessions if session.item.record_id.partition(":")[2] not in referenced], candidates, coverage


def _session_metrics(
    roots: dict[str, Path], weeks: dict[str, dict[str, Any]], since: datetime.datetime, until: datetime.datetime
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    sessions, candidates, coverage = _scan_sessions(roots, since)
    cli_resolved: dict[str, _Runtime] = {}
    cli_unreadable: dict[_Runtime, set[str]] = collections.defaultdict(set)

    def resolve(session_id: str) -> tuple[Path, _Runtime] | None:
        if session_id in candidates:
            return candidates[session_id]
        found = record_paths.find_session_record(session_id)
        if found is not None and found.engine in {"agy", "opencode"} and len(found.paths) == 1:
            runtime: _Runtime = "opencode" if found.engine == "opencode" else "agy"
            cli_resolved[session_id] = runtime
            return found.paths[0], runtime
        return None

    seen: set[Path] = set()
    unresolved: set[tuple[str, int, str]] = set()
    unreadable: set[Path] = set()
    session_rows: list[dict[str, Any]] = []
    for session in sorted(sessions, key=lambda value: (value.start or until, str(value.item.path))):
        if session.start is None or not since <= session.start <= until:
            continue
        records = _load_records(str(session.item.path))
        if records is None:
            unreadable.add(session.item.path)
            continue
        family, missing = _collect_records(str(session.item.path), records, session_resolver=resolve)
        unresolved.update((item.record_id, item.line, item.kind) for item in missing)
        for item in missing:
            if item.record_id in cli_resolved:
                cli_unreadable[cli_resolved[item.record_id]].add(item.record_id)
        week = weeks[_week(session.start)]
        amounts = session_cost.estimate_family(
            [
                session_cost.CostRecord(
                    item.record_id,
                    item.runtime or "unknown",
                    [record.entry for record in item.records],
                    item.role,
                    item.source_record,
                )
                for item in family
            ],
            incomplete=bool(missing),
        )
        amounts.pop("pricing")
        cwd = next(
            (
                entry.get("cwd") if isinstance(entry.get("cwd"), str) else entry["payload"].get("cwd")
                for record in records
                if isinstance((entry := record.entry).get("cwd"), str)
                or entry.get("type") == "session_meta"
                and isinstance(entry.get("payload"), dict)
                and isinstance(entry["payload"].get("cwd"), str)
            ),
            None,
        )
        session_rows.append(
            {
                "kind": "cost-metrics-session",
                "engine": session.item.runtime,
                "session_id": session.item.record_id.partition(":")[2],
                "started_at": session.start.isoformat(),
                "cwd": cwd,
                **amounts,
            }
        )
        workflow, _ = _workflow_evidence(family[0])
        scopes = ("all", "process-wi") if workflow == "process-wi" else ("all",)
        for scope in scopes:
            week["root_session_count"][scope] += 1
        for item in family:
            physical = item.path.resolve()
            if physical in seen or item.runtime is None:
                continue
            seen.add(physical)
            summary = _stats_summary_data(item.records, item.runtime)
            raw = summary.get("tokens", {})
            if item.runtime not in roots:
                runtime_info = coverage["by_runtime"].setdefault(
                    item.runtime, {"oldest_record_at": None, "unreadable_record_count": 0}
                )
                start_text = summary.get("start")
                if start_text and (
                    runtime_info["oldest_record_at"] is None
                    or _parse_timestamp(start_text) < _parse_timestamp(runtime_info["oldest_record_at"])
                ):
                    runtime_info["oldest_record_at"] = _parse_timestamp(start_text).isoformat()
            tokens = _codex_normalized_tokens(raw) if item.runtime == "codex" else raw
            for scope in scopes:
                target = week["tokens"][scope]
                _add_tokens(target["total"], tokens)
                _add_tokens(target["by_runtime"].setdefault(item.runtime, _empty_tokens()), tokens)
    coverage["unresolved_delegate_count"] = len(unresolved)
    coverage["records_lost_during_collection_count"] = len(unreadable)
    for runtime, unreadable_ids in cli_unreadable.items():
        coverage["by_runtime"].setdefault(runtime, {"oldest_record_at": None})["unreadable_record_count"] = len(unreadable_ids)
    coverage["pricing"] = model_pricing.pricing_metadata()
    return coverage, session_rows


def main(argv: list[str] | None = None) -> int:
    """公開CLIから期間と所在を受け取り、週ごとの指標と被覆範囲をJSON Linesで返す。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since", required=True, metavar="DATE", help="開始日時（必須）。日付・ISO 8601、時差なしはローカル時刻。"
    )
    parser.add_argument("--until", metavar="DATE", help="終了日時。省略時は実行時刻。日付はローカル0時。")
    parser.add_argument(
        "--claude-projects-dir", type=Path, metavar="DIR", help="Claude記録ディレクトリのパス。省略時は~/.claude/projects。"
    )
    parser.add_argument(
        "--codex-sessions-dir", type=Path, metavar="DIR", help="Codex記録ディレクトリのパス。省略時は~/.codex/sessions。"
    )
    parser.add_argument(
        "--agy-brain-dir",
        type=Path,
        metavar="DIR",
        help="Antigravity対話記録のbrainディレクトリ。省略時は~/.gemini/antigravity-cli/brain。",
    )
    parser.add_argument(
        "--private-notes",
        type=Path,
        metavar="DIR",
        help="private-notesのGit作業ツリーのパス。省略時はatk config get private_notesと同じ解決。",
    )
    args = parser.parse_args(argv)
    try:
        since = _parse_cli_timestamp(args.since)
        until = _parse_cli_timestamp(args.until) if args.until else datetime.datetime.now().astimezone()
        if since > until:
            raise ValueError("開始が終了より後である")
        weeks: dict[str, dict[str, Any]] = {}
        day = datetime.date.fromisoformat(_week(since))
        while day.isoformat() <= _week(until):
            weeks[day.isoformat()] = _new_week(day.isoformat())
            day += datetime.timedelta(days=7)
        roots = {
            "claude": args.claude_projects_dir or Path.home() / ".claude" / "projects",
            "codex": args.codex_sessions_dir or Path.home() / ".codex" / "sessions",
            "agy": args.agy_brain_dir or Path.home() / ".gemini" / "antigravity-cli" / "brain",
        }
        if args.agy_brain_dir is None and not roots["agy"].is_dir():
            roots.pop("agy")
        roots = {key: value.expanduser().resolve() for key, value in roots.items()}
        coverage, session_rows = _session_metrics(roots, weeks, since, until)
        notes = (args.private_notes or private_notes.default_private_notes()).expanduser().resolve()
        coverage.update(_queue_metrics(notes, weeks, since, until))
    except (ValueError, OSError, yaml.YAMLError, subprocess.CalledProcessError) as error:
        print(
            json.dumps(
                {
                    "kind": "error",
                    "text": str(error),
                    "next_action": "次の操作: 日時と記録・private-notesの所在、Git履歴の読取権限を確認して再実行する",
                },
                ensure_ascii=False,
            )
        )
        return 2
    for week in weeks.values():
        count = week["adopted_awi_count"]
        week["tokens_per_awi"] = {
            scope: {key: value / count for key, value in week["tokens"][scope]["total"].items()} if count else None
            for scope in ("all", "process-wi")
        }
        review = week["review"]
        review["five_or_more_ratio"] = (
            (review["rounds"]["5"] + review["rounds"]["6+"]) / review["table_count"] if review["table_count"] else None
        )
        review["without_modification_ratio"] = (
            review["without_modification_count"] / review["finding_count"] if review["finding_count"] else None
        )
        start = _parse_cli_timestamp(week["week"])
        week["missing_token_records"] = {
            runtime: info["oldest_record_at"] is None or start < _parse_cli_timestamp(info["oldest_record_at"])
            for runtime, info in coverage["by_runtime"].items()
        }
        print(json.dumps(week, ensure_ascii=False))
    for row in session_rows:
        print(json.dumps(row, ensure_ascii=False))
    print(
        json.dumps(
            {
                "kind": "cost-metrics-coverage",
                "since": since.isoformat(),
                "until": until.isoformat(),
                "scan_roots": {key: str(value) for key, value in roots.items()},
                **coverage,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
