"""前のセッションの振り返り待ちを対象リポジトリごとに記録する。"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
from collections.abc import Iterator
from pathlib import Path

from _common import file_lock as _file_lock
from _common.atomic_file import atomic_write

from _atk import config as _config
from _atk import help_text as _atk_help
from _atk.wi.repo import resolve_repo_id


def _record_path() -> Path:
    """振り返り待ちの記録ファイルを返す。"""
    return _config.state_dir() / "session-review-queue.json"


def _lock_path() -> Path:
    """振り返り待ちの記録を直列化する固定ロックを返す。"""
    return Path.home() / ".claude" / ".atk-locks" / "session-review-queue" / "session-review-queue.lock"


def _read_records(path: Path) -> dict[str, dict[str, dict[str, str]]]:
    """記録を読み、解釈できないファイルと最上位が辞書でないファイルを空として扱う。"""
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(records, dict):
        return {}
    return records


def _repository_records(
    records: dict[str, dict[str, dict[str, str]]],
    repository: str,
) -> dict[str, dict[str, str]]:
    repository_records = records.get(repository, {})
    return repository_records if isinstance(repository_records, dict) else {}


def _session_id(value: str) -> str:
    """セッション識別子を検証してargparseへ返す。"""
    if not value or "/" in value or "\\" in value:
        raise argparse.ArgumentTypeError("セッション識別子は空でなく、パス区切り文字を含まない値で指定してください")
    return value


def _transcript(value: str) -> str:
    """transcriptパスから空でないセッション識別子を取得できることを検証する。"""
    _session_id(Path(value).stem)
    return value


def _print_entries(records: dict[str, dict[str, str]], *, exclude: str | None = None) -> None:
    entries = (
        {
            "engine": entry["engine"],
            "session_id": session_id,
            "registered_at": entry["registered_at"],
        }
        for session_id, entry in records.items()
        if session_id != exclude
    )
    for entry in sorted(entries, key=lambda value: (value["registered_at"], value["session_id"])):
        print(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))


def _write_records(path: Path, records: dict[str, dict[str, dict[str, str]]]) -> None:
    atomic_write(path, json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n", fsync=True)


@contextlib.contextmanager
def _record_lock() -> Iterator[None]:
    """記録ファイルの固定ロックを取得し、離脱時に解放する。"""
    lock_path = _lock_path()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            yield
        finally:
            _file_lock.release_lock(lock_file)


def _claim(target_repo: str | None, session_id: str, engine: str) -> int:
    repository = resolve_repo_id(target_repo)
    path = _record_path()
    with _record_lock():
        records = _read_records(path)
        repository_records = _repository_records(records, repository)
        records[repository] = repository_records
        pending = {key: value for key, value in repository_records.items() if key != session_id}
        repository_records.setdefault(
            session_id,
            {
                "engine": engine,
                "registered_at": datetime.datetime.now(datetime.UTC).isoformat(),
            },
        )
        _write_records(path, records)
        _print_entries(pending)
    return 0


def _done(target_repo: str | None, session_ids: list[str]) -> int:
    repository = resolve_repo_id(target_repo)
    path = _record_path()
    with _record_lock():
        records = _read_records(path)
        repository_records = _repository_records(records, repository)
        remaining = repository_records.copy()
        for session_id in session_ids:
            remaining.pop(session_id, None)
        if remaining != repository_records:
            records[repository] = remaining
            _write_records(path, records)
        _print_entries(remaining)
    return 0


def build_parser(parent: argparse._SubParsersAction) -> None:
    """`session-review-queue`配下のサブコマンドを登録する。"""
    queue = _atk_help.add_command(
        parent,
        "session-review-queue",
        **_atk_help.HELP["atk session-review-queue"],
    )
    subcommands = _atk_help.add_subcommands(
        queue,
        dest="session_review_queue_subcommand",
        required=False,
        show_help_when_missing=True,
    )
    claim = _atk_help.add_command(subcommands, "claim", **_atk_help.HELP["atk session-review-queue claim"])
    claim.add_argument("--target-repo", help="対象リポジトリ。省略時はカレント作業ディレクトリから解決する。")
    identity = claim.add_mutually_exclusive_group(required=True)
    identity.add_argument("--transcript", type=_transcript, help="Claude Codeのtranscriptパス。")
    identity.add_argument("--codex-thread-id", type=_session_id, help="Codexのthread ID。")
    done = _atk_help.add_command(subcommands, "done", **_atk_help.HELP["atk session-review-queue done"])
    done.add_argument("--target-repo", help="対象リポジトリ。省略時はカレント作業ディレクトリから解決する。")
    done.add_argument(
        "session_ids", metavar="SESSION_ID", nargs="+", type=_session_id, help="振り返りを完了したセッション識別子。"
    )


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果を振り返り待ちの記録操作へ振り分ける。"""
    if args.session_review_queue_subcommand == "claim":
        if args.transcript is not None:
            return _claim(args.target_repo, Path(args.transcript).stem, "claude")
        return _claim(args.target_repo, args.codex_thread_id, "codex")
    if args.session_review_queue_subcommand == "done":
        return _done(args.target_repo, args.session_ids)
    raise ValueError(f"未知のsession-review-queueサブコマンド: {args.session_review_queue_subcommand}")
