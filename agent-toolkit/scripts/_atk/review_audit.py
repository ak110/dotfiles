"""自動コードレビュー監査で判定済みのreview識別子を記録する。"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import re
from collections.abc import Iterator
from pathlib import Path

from _common import file_lock as _file_lock
from _common.atomic_file import atomic_write

from _atk import config as _config
from _atk import help_text as _atk_help
from _atk import output_file as _output_file

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]*$")


def _record_path() -> Path:
    """判定済みreviewの記録ファイルを返す。"""
    return _config.state_dir() / "review-audit.json"


def _validate_repository(repository: str) -> None:
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise ValueError("リポジトリは<owner>/<repo>形式で指定する")


def _validate_identifiers(identifiers: list[str]) -> None:
    if any(_IDENTIFIER_RE.fullmatch(identifier) is None for identifier in identifiers):
        raise ValueError("識別子は10進数の正の整数で指定する")


def _read_records(path: Path) -> dict[str, dict[str, str]]:
    """記録を読み、解釈できないファイルと最上位が辞書でないファイルを空として扱う。"""
    try:
        records = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(records, dict):
        return {}
    return records


def _repository_records(records: dict[str, dict[str, str]], repository: str) -> dict[str, str]:
    repository_records = records.get(repository, {})
    return repository_records if isinstance(repository_records, dict) else {}


def _print_identifiers(records: dict[str, str]) -> None:
    for identifier in sorted(records, key=int):
        print(identifier)


@contextlib.contextmanager
def _record_lock() -> Iterator[None]:
    """記録ファイルの固定ロックを取得し、離脱時に解放する。"""
    lock_path = Path.home() / ".claude" / ".atk-locks" / "review-audit" / "review-audit.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        _file_lock.acquire_lock(lock_file)
        try:
            yield
        finally:
            _file_lock.release_lock(lock_file)


def _list(repository: str) -> int:
    _validate_repository(repository)
    _print_identifiers(_repository_records(_read_records(_record_path()), repository))
    return 0


def _mark(repository: str, identifiers: list[str]) -> int:
    _validate_repository(repository)
    _validate_identifiers(identifiers)
    path = _record_path()
    with _record_lock():
        records = _read_records(path)
        repository_records = _repository_records(records, repository)
        records[repository] = repository_records
        recorded_at = datetime.datetime.now(datetime.UTC).isoformat()
        for identifier in identifiers:
            repository_records.setdefault(identifier, recorded_at)
        atomic_write(path, json.dumps(records, ensure_ascii=False, indent=2, sort_keys=True) + "\n", fsync=True)
    _print_identifiers(repository_records)
    return 0


def build_parser(parent: argparse._SubParsersAction) -> None:
    """`review-audit`配下のサブコマンドを登録する。"""
    review_audit = _atk_help.add_command(parent, "review-audit", **_atk_help.HELP["atk review-audit"])
    subcommands = _atk_help.add_subcommands(
        review_audit,
        dest="review_audit_subcommand",
        required=False,
        show_help_when_missing=True,
    )
    list_parser = _atk_help.add_command(subcommands, "list", **_atk_help.HELP["atk review-audit list"])
    list_parser.add_argument("--repo", required=True, help="対象リポジトリ。<owner>/<repo>形式で指定する。")
    _output_file.add_output_file_arg(list_parser)
    mark_parser = _atk_help.add_command(subcommands, "mark", **_atk_help.HELP["atk review-audit mark"])
    mark_parser.add_argument("--repo", required=True, help="対象リポジトリ。<owner>/<repo>形式で指定する。")
    mark_parser.add_argument("identifiers", nargs="+", help="記録するreviewのdatabaseId。正の整数で指定する。")


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果を判定済みreviewの操作へ振り分ける。"""
    if args.review_audit_subcommand == "list":
        return _list(args.repo)
    if args.review_audit_subcommand == "mark":
        return _mark(args.repo, args.identifiers)
    raise ValueError(f"未知のreview-auditサブコマンド: {args.review_audit_subcommand}")
