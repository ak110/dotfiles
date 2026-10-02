"""`atk agents logs`の単一記録と一括変換の対象選択・出力。"""

from __future__ import annotations

import dataclasses
import datetime
import os
import pathlib
import re
import sys
import typing

from agent_toolkit._agents_server import logs_markdown, record_paths
from agent_toolkit._atk.serve import sessions as session_records
from agent_toolkit._common.next_action import report

# 記録を特定できない場合の次の操作。`atk agents logs`の各処理と`atk agents logs <id>`の表示で共有する。
MISSING_RECORD_NEXT_ACTION = "`atk agents list --include-terminated`でsession_idを確かめてから再実行する"
UNREADABLE_RECORD_NEXT_ACTION = "記録ファイルの権限を確かめてから再実行する"
BROKEN_LINES_NEXT_ACTION = "対応不要（解析できた行の出力は継続した）"
_OUTPUT_DIR_NEXT_ACTION = "`--output-dir`を書込可能で同名のファイルが無いディレクトリへ変えて再実行する"


@dataclasses.dataclass(frozen=True)
class RecordTarget:
    """1件のローカルのセッション記録と開始時刻。"""

    engine: str
    path: pathlib.Path
    session_id: str
    started_at: str | None


def export_logs(
    *,
    session_id: str | None,
    project_dir: pathlib.Path | None,
    latest: int | None,
    output_format: str,
    output_dir: pathlib.Path | None,
    include_thinking: bool,
    include_subagents: bool,
    tool_details: bool,
) -> int:
    """公開CLIの選択条件で記録を読み、標準出力か1件1ファイルへ出力する。"""
    if session_id is not None:
        selected = record_paths.find_session_record(session_id)
        if selected is None:
            report(f"sessionの記録が見つかりません: {session_id}", next_action=MISSING_RECORD_NEXT_ACTION)
            return 2
        targets = [_target(selected.engine, selected.paths[0], session_id)]
    else:
        targets = _bulk_targets(project_dir)
        if latest is not None:
            targets = targets[:latest]
    if not targets:
        report(
            "対象のsession記録が見つかりません",
            next_action="`--project-dir`の作業ディレクトリを確かめるか、`--all`で全プロジェクトの記録を対象にする",
        )
        return 2

    destinations: list[pathlib.Path] | None = None
    if output_dir is not None:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            destinations = _destinations(output_dir, targets)
        except (OSError, FileExistsError) as error:
            report(f"出力先を準備できません: {error}", next_action=_OUTPUT_DIR_NEXT_ACTION)
            return 2

    for index, target in enumerate(targets):
        try:
            records, broken = session_records.parse_records(target.path.read_text(encoding="utf-8", errors="replace"))
        except OSError as error:
            report(f"sessionの記録を読めません: {target.session_id}: {error}", next_action=UNREADABLE_RECORD_NEXT_ACTION)
            return 2
        if broken:
            report(f"解析できない行: {broken}: {target.path}", next_action=BROKEN_LINES_NEXT_ACTION)
        if output_format == "markdown":
            rendered = logs_markdown.render_session(
                target.engine,
                records,
                target.session_id,
                target.path,
                include_thinking=include_thinking,
                include_subagents=include_subagents,
                tool_details=tool_details,
            )
        else:
            rendered = _render_text(target, records, bulk=session_id is None)
        if destinations is None:
            sys.stdout.write(rendered)
            if index + 1 < len(targets):
                sys.stdout.write("\n")
        else:
            destination = destinations[index]
            try:
                with destination.open("x", encoding="utf-8") as stream:
                    stream.write(rendered)
            except OSError as error:
                report(f"出力ファイルを書けません: {destination}: {error}", next_action=_OUTPUT_DIR_NEXT_ACTION)
                return 2
            print(f"出力: {destination}")
    return 0


def _bulk_targets(project_dir: pathlib.Path | None) -> list[RecordTarget]:
    """Claude Code親とCodexの全記録を件数制限なしに列挙する。"""
    targets: list[RecordTarget] = []
    project = os.path.abspath(os.fspath(project_dir)) if project_dir is not None else None
    claude_projects = session_records.default_claude_home() / "projects"
    if claude_projects.is_dir():
        if project is None:
            project_roots = sorted(path for path in claude_projects.iterdir() if path.is_dir())
        else:
            encoded = project.replace("/", "-").replace(".", "-")
            project_roots = [claude_projects / encoded]
        for root in project_roots:
            if not root.is_dir():
                continue
            for path in sorted(root.glob(f"*{session_records.RECORD_SUFFIX}")):
                if path.is_file():
                    targets.append(_target("claude", path, path.stem))
    codex_sessions = session_records.default_codex_home() / "sessions"
    if codex_sessions.is_dir():
        for path in sorted(codex_sessions.glob(f"*/*/*/{session_records.CODEX_ROLLOUT_PREFIX}*.jsonl")):
            if not path.is_file():
                continue
            if project is not None and _first_codex_cwd(path) != project:
                continue
            targets.append(_target("codex", path, session_records.codex_session_id(path)))
    targets.sort(key=lambda item: (_timestamp(item.started_at), item.session_id), reverse=True)
    return targets


def _target(engine: str, path: pathlib.Path, session_id: str) -> RecordTarget:
    """先頭の有効な記録から開始日時を取得する。"""
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                records, _ = session_records.parse_records(line)
                if not records:
                    continue
                record = records[0]
                timestamp = record.get("timestamp")
                if not isinstance(timestamp, str) and engine == "codex":
                    payload = record.get("payload")
                    timestamp = payload.get("timestamp") if isinstance(payload, dict) else None
                if isinstance(timestamp, str):
                    return RecordTarget(engine, path, session_id, timestamp)
    except OSError:
        pass
    return RecordTarget(engine, path, session_id, None)


def _first_codex_cwd(path: pathlib.Path) -> str | None:
    """Codexの先頭`session_meta`だけをプロジェクト選択の根拠にする。"""
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            first_line = next(stream, "")
    except OSError:
        return None
    records, _ = session_records.parse_records(first_line)
    if not records or records[0].get("type") != "session_meta":
        return None
    payload = records[0].get("payload")
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    return cwd if isinstance(cwd, str) else None


def _timestamp(value: str | None) -> datetime.datetime:
    """欠けた開始時刻を末尾へ置く比較値を返す。"""
    if value is None:
        return datetime.datetime.min.replace(tzinfo=datetime.UTC)
    try:
        parsed = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.datetime.min.replace(tzinfo=datetime.UTC)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=datetime.UTC)


def _destinations(output_dir: pathlib.Path, targets: list[RecordTarget]) -> list[pathlib.Path]:
    """日時名の衝突を識別子で解消し、既存ファイルを上書きしない。"""
    destinations: list[pathlib.Path] = []
    chosen: set[pathlib.Path] = set()
    for target in targets:
        started = _timestamp(target.started_at)
        stem = started.strftime("%Y%m%d_%H%M%S") if target.started_at and started.year > 1 else _safe_stem(target.session_id)
        primary = output_dir / f"{stem}.md"
        if primary.exists() or primary in chosen:
            primary = output_dir / f"{stem}_{_safe_stem(target.session_id)}.md"
        if primary.exists() or primary in chosen:
            raise FileExistsError(primary)
        chosen.add(primary)
        destinations.append(primary)
    return destinations


def _safe_stem(value: str) -> str:
    """記録識別子を出力先ディレクトリ内のファイル名に限定する。"""
    return re.sub(r"[^0-9A-Za-z_-]", "-", value) or "session"


def _render_text(target: RecordTarget, records: list[dict[str, typing.Any]], *, bulk: bool) -> str:
    """単一記録は形式指定がない場合と同じ書式で出力し、複数件には識別子の区切りを付ける。"""
    lines = [f"### {target.session_id}"] if bulk else []
    for event in session_records.record_events(target.engine, records):
        detail = event.text or event.name or ""
        lines.append(f"[{event.timestamp or '-'}] {event.kind}: {detail}")
    return "\n".join(lines) + "\n"
