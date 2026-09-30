"""自動コードレビュー監査の未判定対象を取得し、判定済みの識別子を記録する。

対象はGitHub Copilot由来のreviewとthread、およびDependabotアラートである。
Dependabotアラートは処理回ごとの監査で拾うため、`atk wi process-loop`の待機中確認も同じ取得と判定を使う。
"""

from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import re
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from agent_toolkit._atk import config as _config
from agent_toolkit._atk import help_text as _atk_help
from agent_toolkit._atk import outcome as _outcome
from agent_toolkit._atk import output_file as _output_file
from agent_toolkit._common import file_lock as _file_lock
from agent_toolkit._common import json_command as _json_command
from agent_toolkit._common import next_action as _next_action
from agent_toolkit._common.atomic_file import atomic_write

_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_IDENTIFIER_RE = re.compile(r"^(?:dependabot:)?[1-9][0-9]*$")
DEPENDABOT_PREFIX = "dependabot:"
"""判定済み記録でDependabotアラート番号をreviewの`databaseId`と区別する接頭辞。"""
_DEPENDABOT_DISABLED_MESSAGE = "Dependabot alerts are disabled for this repository."
"""Dependabotアラート機能が無効なリポジトリへGitHub APIがHTTP 403で返す本文。実測で確認した文言をそのまま用いる。"""
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


_GRAPHQL_NEXT_ACTION = "`gh auth status`で認証を確認し、時間をおいて再実行する。繰り返す場合はユーザーへ報告する"
"""GitHub GraphQLの取得失敗と応答不正に添える次の操作。"""


def _graphql_error(reason: str) -> _next_action.ActionableError:
    """GitHub GraphQLの取得失敗と応答不正を次の操作付きの例外へ変換する。"""
    return _next_action.ActionableError(reason, next_action=_GRAPHQL_NEXT_ACTION)


def _record_path() -> Path:
    """判定済みreviewの記録ファイルを返す。"""
    return _config.state_dir() / "review-audit.json"


def _validate_repository(repository: str) -> None:
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise _next_action.ActionableError(
            f"リポジトリの形式が不正: {repository}", next_action="--repoへ<owner>/<repo>形式で指定する"
        )


def _validate_identifiers(identifiers: list[str]) -> None:
    if any(_IDENTIFIER_RE.fullmatch(identifier) is None for identifier in identifiers):
        raise _next_action.ActionableError(
            "10進数の正の整数でも`dependabot:<番号>`でもない識別子がある",
            next_action="reviewは10進数の正の整数、Dependabotアラートは`dependabot:<番号>`で指定する",
        )


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


def _identifier_sort_key(identifier: str) -> tuple[bool, int]:
    """reviewの`databaseId`を先に、各種別の中では番号の昇順に並べる。"""
    is_dependabot = identifier.startswith(DEPENDABOT_PREFIX)
    return is_dependabot, int(identifier.removeprefix(DEPENDABOT_PREFIX))


def _print_identifiers(records: dict[str, str]) -> None:
    for identifier in sorted(records, key=_identifier_sort_key):
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
        return _graphql_error(f"Copilot監査対象の取得に失敗した: {error.kind}: {error.detail or error.stderr.strip()}")

    response = _json_command.run(command, _GH_TIMEOUT, error_factory=failure, strict_stderr=False)
    if not isinstance(response, dict) or response.get("errors") or not isinstance(response.get("data"), dict):
        raise _graphql_error("Copilot監査対象のGraphQL応答が不正または部分失敗である")
    return response["data"]


def _connection(data: dict, field: str, number: int | None) -> dict:
    repository = data.get("repository")
    if not isinstance(repository, dict):
        raise _graphql_error("Copilot監査対象のrepositoryを取得できない")
    parent = repository if number is None else repository.get("pullRequest")
    connection = parent.get(field) if isinstance(parent, dict) else None
    page = connection.get("pageInfo") if isinstance(connection, dict) else None
    if (
        not isinstance(connection, dict)
        or not isinstance(connection.get("nodes"), list)
        or not isinstance(page, dict)
        or not isinstance(page.get("hasNextPage"), bool)
    ):
        raise _graphql_error(f"Copilot監査対象の{field}接続またはpageInfoが不正である")
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
            raise _graphql_error(f"Copilot監査対象の{field}ノードが不正である")
        nodes.extend(connection["nodes"])
        if not page["hasNextPage"]:
            return nodes
        next_cursor = page.get("endCursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor in visited:
            raise _graphql_error(f"Copilot監査対象の{field}接続がpagination終端へ到達しない")
        visited.add(next_cursor)
        cursor = next_cursor


def _copilot_author(author: Any) -> bool:
    return isinstance(author, dict) and author.get("__typename") == "Bot" and "copilot" in str(author.get("login", "")).lower()


def _error_body(stdout: str) -> dict:
    """`gh api`が失敗時に標準出力へ書いた応答本文を返す。解釈できない場合は空の辞書を返す。

    `--slurp`付きの呼び出しは本文を配列で包むため、要素1件の配列も本文として扱う。
    """
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        return {}
    if isinstance(payload, list) and len(payload) == 1:
        payload = payload[0]
    return payload if isinstance(payload, dict) else {}


def _dependabot_error(reason: str) -> _next_action.ActionableError:
    return _next_action.ActionableError(reason, next_action=_GRAPHQL_NEXT_ACTION)


class _NotFound(Exception):
    """REST APIがHTTP 404を返したことを呼び出し元の分岐へ伝える。"""


class _Forbidden(Exception):
    """REST APIがHTTP 403を返したことと応答本文のmessageを呼び出し元の分岐へ伝える。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _gh_rest(path: str, *, operation: str, paginate: bool = False, not_found_ok: bool = False) -> Any:
    """REST APIの応答を返す。`not_found_ok`ではHTTP 404を`None`で返し、それ以外の失敗は非0終了の例外にする。"""
    command = ["gh", "api", *(("--paginate", "--slurp") if paginate else ()), path]

    def failure(error: _json_command.Failure) -> Exception:
        if error.kind == "exit" and not_found_ok and str(_error_body(error.stdout).get("status")) == "404":
            return _NotFound()
        if error.kind == "exit" and str(_error_body(error.stdout).get("status")) == "403":
            return _Forbidden(str(_error_body(error.stdout).get("message", "")))
        return _dependabot_error(f"{operation}に失敗した: {error.kind}: {error.detail or error.stderr.strip()}")

    try:
        return _json_command.run(command, _GH_TIMEOUT, error_factory=failure, strict_stderr=False)
    except _NotFound:
        return None


def _mapping(value: Any) -> dict[str, Any]:
    """辞書でない値（GitHub APIがnullを返す項目など）を空の辞書として扱う。"""
    return value if isinstance(value, dict) else {}


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def dependabot_pending(repository: str) -> dict[str, Any]:
    """未判定のopenなDependabotアラートを判定区分付きで返す。

    戻り値の`status`は`available`・`disabled`（機能が無効）・`unauthorized`（権限不足。`message`に応答本文）のいずれかとする。
    `category`はマニフェストが既定ブランチに実在しなければ`inaccurate`、実在すれば`manifest_present`とする。
    機能無効と権限不足以外の取得失敗は`ActionableError`を送出する。
    """
    _validate_repository(repository)
    try:
        pages = _gh_rest(
            f"repos/{repository}/dependabot/alerts?state=open&per_page=100",
            operation="Dependabotアラートの取得",
            paginate=True,
        )
    except _Forbidden as forbidden:
        if forbidden.message == _DEPENDABOT_DISABLED_MESSAGE:
            return {"status": "disabled", "alerts": []}
        return {"status": "unauthorized", "alerts": [], "message": forbidden.message}
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise _dependabot_error("Dependabotアラートの応答形状が不正である")
    recorded = _repository_records(_read_records(_record_path()), repository)
    items = [item for page in pages for item in page]
    unjudged: list[dict[str, Any]] = []
    for item in items:
        number = item.get("number") if isinstance(item, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise _dependabot_error("Dependabotアラートの番号が不正である")
        if f"{DEPENDABOT_PREFIX}{number}" not in recorded:
            unjudged.append(item)
    alerts: list[dict[str, Any]] = []
    if unjudged:
        default_branch = _default_branch(repository)
        presence: dict[str, bool] = {}
        for item in sorted(unjudged, key=lambda alert: alert["number"]):
            dependency = _mapping(item.get("dependency"))
            manifest_path = dependency.get("manifest_path")
            if not isinstance(manifest_path, str) or not manifest_path:
                raise _dependabot_error(f"Dependabotアラート{item['number']}のmanifest_pathが不正である")
            if manifest_path not in presence:
                presence[manifest_path] = _manifest_exists(repository, manifest_path, default_branch)
            package = _mapping(dependency.get("package"))
            patched = _mapping(_mapping(item.get("security_vulnerability")).get("first_patched_version"))
            alerts.append(
                {
                    "number": item["number"],
                    "manifest_path": manifest_path,
                    "package": _optional_str(package.get("name")),
                    "ecosystem": _optional_str(package.get("ecosystem")),
                    "first_patched_version": _optional_str(patched.get("identifier")),
                    "category": "manifest_present" if presence[manifest_path] else "inaccurate",
                }
            )
    return {"status": "available", "alerts": alerts}


def _default_branch(repository: str) -> str:
    try:
        response = _gh_rest(f"repos/{repository}", operation="既定ブランチの取得")
    except _Forbidden as forbidden:
        raise _dependabot_error(f"既定ブランチの取得が拒否された: {forbidden.message}") from forbidden
    branch = response.get("default_branch") if isinstance(response, dict) else None
    if not isinstance(branch, str) or not branch:
        raise _dependabot_error("既定ブランチを応答から取得できない")
    return branch


def _manifest_exists(repository: str, manifest_path: str, branch: str) -> bool:
    """既定ブランチにマニフェストが実在するかを返す。HTTP 404だけを不在とし、他の失敗は例外にする。"""
    path = urllib.parse.quote(manifest_path, safe="/")
    ref = urllib.parse.quote(branch, safe="")
    try:
        response = _gh_rest(
            f"repos/{repository}/contents/{path}?ref={ref}", operation=f"{manifest_path}の実在確認", not_found_ok=True
        )
    except _Forbidden as forbidden:
        raise _dependabot_error(f"{manifest_path}の実在確認が拒否された: {forbidden.message}") from forbidden
    return response is not None


def _pending(repository: str) -> int:
    """未判定reviewと未解決のCopilot由来threadがあるPR、および未判定のDependabotアラートを返す。"""
    _validate_repository(repository)
    owner, name = repository.split("/", 1)
    recorded = _repository_records(_read_records(_record_path()), repository)
    reviews: set[tuple[int, int]] = set()
    threads: set[int] = set()
    for pull_request in _pages(owner, name, _PULL_REQUESTS_QUERY, "pullRequests"):
        number = pull_request.get("number")
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise _graphql_error("Copilot監査対象のPR番号が不正である")
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
                raise _graphql_error("Copilot reviewのノードが不正である")
            identifier = review.get("databaseId")
            if _copilot_author(review.get("author")):
                if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier <= 0:
                    raise _graphql_error("Copilot reviewのdatabaseIdが不正である")
                if str(identifier) not in recorded:
                    reviews.add((number, identifier))
        for thread in thread_nodes:
            if not isinstance(thread, dict):
                raise _graphql_error("Copilot review threadのノードが不正である")
            comments = thread.get("comments")
            first = comments.get("nodes") if isinstance(comments, dict) else None
            if (
                not isinstance(thread.get("isResolved"), bool)
                or not isinstance(first, list)
                or any(not isinstance(comment, dict) for comment in first)
            ):
                raise _graphql_error("Copilot review threadの状態または先頭commentが不正である")
            if not thread["isResolved"] and first and _copilot_author(first[0].get("author")):
                threads.add(number)
    dependabot = dependabot_pending(repository)
    result = {
        "reviews": [{"pr": number, "databaseId": identifier} for number, identifier in sorted(reviews)],
        "threads": [{"pr": number} for number in sorted(threads)],
        "dependabot": dependabot,
        "counts": {"reviews": len(reviews), "threads": len(threads), "dependabot": len(dependabot["alerts"])},
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
    _outcome.report_success(
        f"判定済みの識別子を記録した: {repository}（{len(identifiers)}件）", _outcome.ResultKind.VALUE_OUTPUT
    )
    _print_identifiers(dict.fromkeys(identifiers, recorded_at))
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
    mark_parser.add_argument(
        "identifiers",
        nargs="+",
        help="記録する識別子。reviewはdatabaseIdの正の整数、Dependabotアラートは`dependabot:<番号>`で指定する。",
    )


def dispatch(args: argparse.Namespace) -> int:
    """argparse結果を判定済み記録と未判定対象の操作へ振り分ける。"""
    if args.review_audit_subcommand == "list":
        return _list(args.repo)
    if args.review_audit_subcommand == "pending":
        return _pending(args.repo)
    if args.review_audit_subcommand == "mark":
        return _mark(args.repo, args.identifiers)
    raise ValueError(f"未知のreview-auditサブコマンド: {args.review_audit_subcommand}")
