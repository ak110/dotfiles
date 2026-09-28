"""自動コードレビュー監査で判定済みのreview識別子を記録する。"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_toolkit._atk import config as _config
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import output_file as _output_file
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common import json_command as _json_command
from agent_toolkit._common.atomic_file import atomic_write

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_IDENTIFIER_RE = re.compile(r"^[1-9][0-9]*$")
_GH_TIMEOUT = 30.0
_PULL_REQUESTS_QUERY = (
    "query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){"
    "pullRequests(first:100,after:$cursor,states:[OPEN,CLOSED,MERGED]){nodes{number "
    "reviews(first:20){nodes{databaseId author{__typename login}} pageInfo{hasNextPage}} "
    "reviewThreads(first:100){nodes{id isResolved comments(first:1){nodes{databaseId author{__typename login}}}} "
    "pageInfo{hasNextPage}}} pageInfo{hasNextPage endCursor}}}}"
)
_REVIEWS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){reviews(first:100,after:$cursor){nodes{databaseId author{__typename login}} "
    "pageInfo{hasNextPage endCursor}}}}}"
)
_THREADS_QUERY = (
    "query($owner:String!,$name:String!,$number:Int!,$cursor:String){repository(owner:$owner,name:$name){"
    "pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nodes{id isResolved "
    "comments(first:1){nodes{databaseId author{__typename login}}}} pageInfo{hasNextPage endCursor}}}}}"
)


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


def _query_graphql(owner: str, name: str, query: str, *, number: int | None = None, cursor: str | None = None) -> dict:
    """GraphQL応答を取得し、失敗と部分応答を0件から区別する。"""
    command = ["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"name={name}"]
    if number is not None:
        command.extend(("-F", f"number={number}"))
    if cursor is not None:
        command.extend(("-F", f"cursor={cursor}"))
    command.extend(("-f", f"query={query}"))

    def failure(error: _json_command.Failure) -> Exception:
        return ValueError(f"Copilot監査対象の取得に失敗した: {error.kind}: {error.detail or error.stderr.strip()}")

    response = _json_command.run(command, _GH_TIMEOUT, error_factory=failure, strict_stderr=False)
    if not isinstance(response, dict) or response.get("errors") or not isinstance(response.get("data"), dict):
        raise ValueError("Copilot監査対象のGraphQL応答が不正又は部分失敗である")
    return response["data"]


def _connection(data: dict, field: str, number: int | None) -> dict:
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise ValueError("Copilot監査対象のrepositoryを取得できない")
    parent = repository if number is None else repository.get("pullRequest")
    connection = parent.get(field) if isinstance(parent, dict) else None
    page = connection.get("pageInfo") if isinstance(connection, dict) else None
    if (
        not isinstance(connection, dict)
        or not isinstance(connection.get("nodes"), list)
        or not isinstance(page, dict)
        or not isinstance(page.get("hasNextPage"), bool)
    ):
        raise ValueError(f"Copilot監査対象の{field}接続又はpageInfoが不正である")
    return connection


def _pages(owner: str, name: str, query: str, field: str, *, number: int | None = None) -> list[dict]:
    """接続の全ページを列挙し、cursorの反復も失敗として扱う。"""
    nodes: list[dict] = []
    cursor: str | None = None
    visited: set[str] = set()
    while True:
        connection = _connection(_query_graphql(owner, name, query, number=number, cursor=cursor), field, number)
        page = connection["pageInfo"]
        if any(not isinstance(node, dict) for node in connection["nodes"]):
            raise ValueError(f"Copilot監査対象の{field}ノードが不正である")
        nodes.extend(connection["nodes"])
        if not page["hasNextPage"]:
            return nodes
        next_cursor = page.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in visited:
            raise ValueError(f"Copilot監査対象の{field}接続がpagination終端へ到達しない")
        visited.add(next_cursor)
        cursor = next_cursor


def _copilot_author(author: Any) -> bool:
    return isinstance(author, dict) and author.get("__typename") == "Bot" and "copilot" in str(author.get("login", "")).lower()


def _pending(repository: str) -> int:
    """未判定reviewと未解決のCopilot由来threadがあるPRを返す。"""
    _validate_repository(repository)
    owner, name = repository.split("/", 1)
    recorded = _repository_records(_read_records(_record_path()), repository)
    reviews: set[tuple[int, int]] = set()
    threads: set[int] = set()
    for pull_request in _pages(owner, name, _PULL_REQUESTS_QUERY, "pullRequests"):
        number = pull_request.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise ValueError("Copilot監査対象のPR番号が不正である")
        review_connection = _connection({"repository": pull_request}, "reviews", None)
        thread_connection = _connection({"repository": pull_request}, "reviewThreads", None)
        review_nodes = (
            _pages(owner, name, _REVIEWS_QUERY, "reviews", number=number)
            if review_connection["pageInfo"]["hasNextPage"]
            else review_connection["nodes"]
        )
        thread_nodes = (
            _pages(owner, name, _THREADS_QUERY, "reviewThreads", number=number)
            if thread_connection["pageInfo"]["hasNextPage"]
            else thread_connection["nodes"]
        )
        for review in review_nodes:
            if not isinstance(review, dict):
                raise ValueError("Copilot reviewのノードが不正である")
            identifier = review.get("databaseId")
            if _copilot_author(review.get("author")):
                if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
                    raise ValueError("Copilot reviewのdatabaseIdが不正である")
                if str(identifier) not in recorded:
                    reviews.add((number, identifier))
        for thread in thread_nodes:
            if not isinstance(thread, dict):
                raise ValueError("Copilot review threadのノードが不正である")
            comments = thread.get("comments")
            first = comments.get("nodes") if isinstance(comments, dict) else None
            if (
                not isinstance(thread.get("isResolved"), bool)
                or not isinstance(first, list)
                or any(not isinstance(comment, dict) for comment in first)
            ):
                raise ValueError("Copilot review threadの状態又は先頭commentが不正である")
            if not thread["isResolved"] and first and _copilot_author(first[0].get("author")):
                threads.add(number)
    result = {
        "reviews": [{"pr": number, "databaseId": identifier} for number, identifier in sorted(reviews)],
        "threads": [{"pr": number} for number in sorted(threads)],
        "counts": {"reviews": len(reviews), "threads": len(threads)},
    }
    print(json.dumps(result, ensure_ascii=False))
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
    _outcome.report_success(f"判定済みreviewを記録した: {repository}（{len(identifiers)}件）", _outcome.ResultKind.VALUE_OUTPUT)
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
    pending_parser = _atk_help.add_command(subcommands, "pending", **_atk_help.HELP["atk review-audit pending"])
    pending_parser.add_argument("--repo", required=True, help="対象リポジトリ。<owner>/<repo>形式で指定する。")
    mark_parser = _atk_help.add_command(subcommands, "mark", **_atk_help.HELP["atk review-audit mark"])
    mark_parser.add_argument("--repo", required=True, help="対象リポジトリ。<owner>/<repo>形式で指定する。")
    mark_parser.add_argument("identifiers", nargs="+", help="記録するreviewのdatabaseId。正の整数で指定する。")


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果を判定済みreviewの操作へ振り分ける。"""
    if args.review_audit_subcommand == "list":
        return _list(args.repo)
    if args.review_audit_subcommand == "pending":
        return _pending(args.repo)
    if args.review_audit_subcommand == "mark":
        return _mark(args.repo, args.identifiers)
    raise ValueError(f"未知のreview-auditサブコマンド: {args.review_audit_subcommand}")
