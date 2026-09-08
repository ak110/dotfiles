"""保存済みのセッション記録から前の振り返り対象を特定する。"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import pathlib
from collections.abc import Iterator
from typing import Any

from _atk import help_text as _atk_help
from _atk.serve.sessions import (
    CODEX_ROLLOUT_PREFIX,
    RECORD_SUFFIX,
    codex_session_id,
    default_claude_home,
    default_codex_home,
)
from _atk.wi.repo import resolve_repo_id


def _session_id(value: str) -> str:
    """セッション識別子を検証してargparseへ返す。"""
    if not value or "/" in value or "\\" in value:
        raise argparse.ArgumentTypeError("セッション識別子は空でなく、パス区切り文字を含まない値で指定してください")
    return value


def _transcript(value: str) -> str:
    """transcriptパスから空でないセッション識別子を取得できることを検証する。"""
    _session_id(pathlib.Path(value).stem)
    return value


def _candidate_paths() -> Iterator[tuple[pathlib.Path, str, str]]:
    """Claude CodeとCodexの本体セッション候補を列挙する。"""
    projects = default_claude_home() / "projects"
    if projects.is_dir():
        for project_dir in projects.iterdir():
            if project_dir.is_dir():
                for path in project_dir.glob(f"*{RECORD_SUFFIX}"):
                    if path.is_file():
                        yield path, "claude", path.stem

    sessions = default_codex_home() / "sessions"
    if sessions.is_dir():
        for path in sessions.glob(f"*/*/*/{CODEX_ROLLOUT_PREFIX}*{RECORD_SUFFIX}"):
            if path.is_file():
                yield path, "codex", codex_session_id(path)


def _parsed_records(path: pathlib.Path) -> Iterator[dict[str, Any]]:
    """JSON Linesから解釈できる辞書レコードだけを返す。"""
    with path.open(encoding="utf-8") as record_file:
        for line in record_file:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _session_cwd(path: pathlib.Path, engine: str) -> str | None:
    """本体セッションなら判定に用いるcwdを返す。"""
    try:
        for record in _parsed_records(path):
            if engine == "claude" and record.get("type") == "user":
                cwd = record.get("cwd")
                return cwd if record.get("entrypoint") == "cli" and isinstance(cwd, str) else None
            if engine == "codex" and record.get("type") == "session_meta":
                payload = record.get("payload")
                if not isinstance(payload, dict):
                    return None
                cwd = payload.get("cwd")
                return cwd if payload.get("originator") == "codex-tui" and isinstance(cwd, str) else None
    except OSError:
        return None
    return None


def _resolved_repo(cwd: str, cache: dict[str, str | None]) -> str | None:
    """cwdごとにリポジトリ識別子を1回だけ解決する。"""
    if cwd not in cache:
        with contextlib.redirect_stderr(io.StringIO()):
            try:
                cache[cwd] = resolve_repo_id(None, cwd=pathlib.Path(cwd))
            except (OSError, SystemExit, ValueError):
                cache[cwd] = None
    return cache[cwd]


def _select_target(target_repo: str, current_session_id: str) -> dict[str, str] | None:
    """更新時刻が最新の対象リポジトリの本体セッションを返す。"""
    candidates: list[tuple[float, pathlib.Path, str, str]] = []
    for path, engine, session_id in _candidate_paths():
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            continue
        candidates.append((modified_at, path, engine, session_id))

    repository_cache: dict[str, str | None] = {}
    for _modified_at, path, engine, session_id in sorted(candidates, reverse=True):
        if session_id == current_session_id:
            continue
        cwd = _session_cwd(path, engine)
        if cwd is None or _resolved_repo(cwd, repository_cache) != target_repo:
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
    target = _select_target(repository, current_session_id)
    if target is not None:
        print(json.dumps(target, ensure_ascii=False, separators=(",", ":")))
    return 0
