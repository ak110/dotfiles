"""保存済みのセッション記録から前の振り返り対象を特定する。"""

from __future__ import annotations

import argparse
import datetime
import json
import pathlib
import sys

from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import session_records as _session_records
from agent_toolkit._atk.wi.repo import resolve_repo_id


class _CurrentSessionStartUnresolvableError(Exception):
    """現在のセッション記録から開始時刻を解決できない。"""


def _session_id(value: str) -> str:
    """セッション識別子を検証してargparseへ返す。"""
    if not value or "/" in value or "\\" in value:
        raise argparse.ArgumentTypeError("セッション識別子は空でなく、パス区切り文字を含まない値で指定してください")
    return value


def _transcript(value: str) -> str:
    """transcriptパスから空でないセッション識別子を取得できることを検証する。"""
    _session_id(pathlib.Path(value).stem)
    return value


def _current_session_started_at(candidates: list[tuple[float, pathlib.Path, str, str]], current_session_id: str) -> float:
    """現在のセッション記録にある最初の解析可能なtimestampをepoch秒へ変換する。"""
    current_paths = [path for _modified_at, path, _engine, session_id in candidates if session_id == current_session_id]
    for path in current_paths:
        try:
            for record in _session_records.parsed_records(path):
                timestamp = record.get("timestamp")
                if not isinstance(timestamp, str):
                    continue
                try:
                    return datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
        except OSError:
            continue
    raise _CurrentSessionStartUnresolvableError(current_session_id)


def _select_target(target_repo: str, current_session_id: str) -> dict[str, str] | None:
    """process-wiを起動した対象リポジトリの最新本体セッションを返す。"""
    candidates: list[tuple[float, pathlib.Path, str, str]] = []
    for path, engine, session_id in _session_records.candidate_paths():
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((modified_at, path, engine, session_id))

    current_started_at = _current_session_started_at(candidates, current_session_id)

    repository_cache: dict[str, str | None] = {}
    for modified_at, path, engine, session_id in sorted(candidates, reverse=True):
        if session_id == current_session_id:
            continue
        if modified_at >= current_started_at:
            continue
        cwd = _session_records.session_cwd(path, engine)
        if cwd is None or _session_records.resolved_repo(cwd, repository_cache) != target_repo:
            continue
        if not _session_records.invoked_process_wi(path, engine):
            continue
        return {"engine": engine, "session_id": session_id}
    return None


def build_parser(parent: argparse._SubParsersAction) -> None:
    """`session-review-target`サブコマンドを登録する。"""
    target = _atk_help.add_command(
        parent,
        "session-review-target",
        **_atk_help.HELP["atk session-review-target"],
    )
    target.add_argument("--target-repo", help="対象リポジトリ。省略時はカレント作業ディレクトリから解決する。")
    identity = target.add_mutually_exclusive_group(required=True)
    identity.add_argument("--transcript", type=_transcript, help="Claude Codeのtranscriptパス。")
    identity.add_argument("--codex-thread-id", type=_session_id, help="Codexのthread ID。")


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果から前の振り返り対象を特定する。"""
    repository = resolve_repo_id(args.target_repo)
    current_session_id = pathlib.Path(args.transcript).stem if args.transcript is not None else args.codex_thread_id
    try:
        target = _select_target(repository, current_session_id)
    except _CurrentSessionStartUnresolvableError:
        print(f"現在のセッションの開始時刻を解決できません: {current_session_id}", file=sys.stderr)
        return 2
    if target is not None:
        print(json.dumps(target, ensure_ascii=False, separators=(",", ":")))
    return 0
