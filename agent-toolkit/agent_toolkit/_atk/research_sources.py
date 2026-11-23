"""外部情報の調査で読む情報源を、前回読んだ位置より後の分だけ取得する。  pyfltr: allow-comment

取得した投稿・記事・変更履歴の本文は標準出力でモデルへ渡すだけにし、本モジュールが書く状態ファイルには
情報源ごとの読んだ位置（投稿ID、版数、日時）とアカウントIDの対応だけを残す。
読んだ位置は回の結果のUWIを保存した後に確定し（`commit_positions`）、実際に読めた情報源だけを進める。
件数の上限で読み残した区間は、X APIでは情報源ごとの未読区間として残し、次回は新しい分より先に読む。
モデルが無料の手段で調べる情報源（`model_sources`）は、モデルが`mark_read`で読めたと記録した分だけ進める。

X APIは従量課金であり、ユーザーが`weekly_research_x_api`を`true`にするまで呼ばない（疎通確認も含む）。
資格情報は読み取り専用のBearer Token（app-only認証）だけを使い、値は出力・状態・ログへ出さない。
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import json
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any

from agent_toolkit._atk import config, config_settings
from agent_toolkit._common import claude_connection, session_review
from agent_toolkit._common.diagnostic_redaction import redact_secrets

X_API_BASE = "https://api.x.com/2"
X_TOKEN_VARIABLE = "X_BEARER_TOKEN"
X_ENV_FILENAME = "x-api.env"
INITIAL_DAYS = 7

X_MIN_RESULTS = {"timeline": 5, "search": 10}

X_SEARCH_WINDOW = datetime.timedelta(days=7) - datetime.timedelta(minutes=5)

_X_EPOCH_MS = 1288834974657

GITHUB_PAGES = 10

TIMEOUT_SECONDS = 30
_USER_AGENT = "agent-toolkit-weekly-research"

CLAUDE_CODE_REPOSITORY = "anthropics/claude-code"
CLAUDE_CODE_CHANGELOG = f"https://raw.githubusercontent.com/{CLAUDE_CODE_REPOSITORY}/main/CHANGELOG.md"
GITHUB_RELEASES = {"codex": "openai/codex", "opencode": "sst/opencode"}

Fetch = Callable[[str, dict[str, str]], tuple[int, bytes]]

RESULT_UWI = {
    "with_proposals": (
        "`atk wi add --type=uwi --question-type=choice --source=run-skill "
        "--scope=<起動入力のaccepted_scopesの先頭>`で1件保存する。"
        "`--choices`は「推奨の提案を全て採用,回答欄に書いた提案IDだけ採用,全て見送る」とする。"
    ),
    "proposal_fields": (
        "提案ごとに一意の提案ID（P1など）、出所（URLと投稿日時）、対象リポジトリ、何をどう変えるか、便益、推奨を書く。"
        "購読するアカウントと検索語の追加・削除の候補は、提案ID（S1など）を付けて同じUWIで選べるようにする。"
    ),
    "without_proposals": (
        "提案が0件の回は、frontmatterに`run_skill_report: true`を持つ`--question-type=post-approval`の報告用UWIへ、"
        "読んだ情報源と件数を書く。"
    ),
    "common": (
        "どちらにも読めなかった情報源と理由を書く。"
        "X由来の内容はURL・投稿ID・日時・短い抜粋と改善案だけを書き、本文の全文を書かない。"
    ),
    "answer_handling": (
        "UWI本文に次を書く: 回答の処理では、選ばれた提案ごとにfrontmatterへ`proposal_id: <weekly_slot>/<提案ID>`を書いたAWIを"
        "対象のリポジトリへ登録し（同じ提案IDのAWIがあれば`atk wi add`が登録を省く）、"
        "選ばれた購読の変更を、アカウントは`weekly_research_x_accounts`、検索語は`weekly_research_x_queries`へ"
        "`atk config set <キー> <JSON配列>`で反映する（`atk config show`の現在値に加除した配列を渡す）。"
    ),
}

MODEL_SOURCES = {
    "web:antigravity": "Antigravityの変更履歴",
    "web:hacker-news": "Hacker Newsの話題",
    "web:topics": "WebSearchで探す話題のトピック",
    "web:engineers": "購読中（`x_subscriptions`）以外の著名なエンジニアの発信",
}


class SourceError(Exception):
    pass


@dataclasses.dataclass
class SourceResult:
    source: str
    status: str
    reason: str = ""
    items: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    position: str | None = None
    gap: dict[str, str] | None = None


@dataclasses.dataclass
class XBudget:
    max_posts: int
    max_users: int
    posts: int = 0
    users: int = 0


def _directory() -> pathlib.Path:
    return session_review.directory() / "weekly"


def positions_path() -> pathlib.Path:
    return _directory() / "research-positions.json"


def pending_path() -> pathlib.Path:
    return _directory() / "research-positions-pending.json"


def _read(path: pathlib.Path) -> dict[str, Any]:
    try:
        return session_review.read(path)
    except FileNotFoundError:
        return {}


def load_state() -> dict[str, Any]:
    state = _read(positions_path())
    return {
        "positions": dict(state.get("positions", {})),
        "user_ids": dict(state.get("user_ids", {})),
        "gaps": dict(state.get("gaps", {})),
    }


def mark_read(slot: str, sources: tuple[str, ...]) -> list[str]:
    pending = _read(pending_path())
    if pending.get("slot") != slot:
        raise ValueError(f"回 {slot} の取得結果が無い。先に `atk run-script research-sources` を実行する")
    unknown = [source for source in sources if source not in pending.get("model_sources", [])]
    if unknown:
        raise ValueError(f"出力のmodel_sourcesに無い情報源: {', '.join(unknown)}")
    pending["positions"] = {**pending.get("positions", {}), **dict.fromkeys(sources, pending["collected_at"])}
    session_review.write(pending_path(), pending)
    return list(sources)


def commit_positions(slot: str) -> bool:
    pending = _read(pending_path())
    if pending.get("slot") != slot:
        return False
    state = load_state()
    state["positions"].update(pending.get("positions", {}))
    state["user_ids"].update(pending.get("user_ids", {}))
    for source, gap in pending.get("gaps", {}).items():
        if gap:
            state["gaps"][source] = gap
        else:
            state["gaps"].pop(source, None)
    _directory().mkdir(parents=True, exist_ok=True)
    session_review.write(positions_path(), state)
    pending_path().unlink(missing_ok=True)
    return True


def default_fetch(url: str, headers: dict[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # noqa: S310  # 取得先は固定のhttpsのURLに限る。
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def _get_json(fetch: Fetch, url: str, headers: dict[str, str] | None = None) -> Any:
    try:
        status, body = fetch(url, headers or {})
    except (OSError, ValueError) as error:
        raise SourceError(f"接続できない: {type(error).__name__}") from error
    if status != 200:
        raise SourceError(f"HTTP {status}{_error_title(body)}")
    try:
        return json.loads(body)
    except ValueError as error:
        raise SourceError("応答がJSONでない") from error


def _error_title(body: bytes) -> str:
    try:
        value = json.loads(body)
    except ValueError:
        return ""
    if not isinstance(value, dict):
        return ""
    title = value.get("title") or value.get("message") or value.get("detail")
    return f"（{str(title)[:120]}）" if isinstance(title, str) and title else ""


def _excerpt(text: str, limit: int = 4000) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _changelog_sections(body: bytes) -> list[tuple[str, str]]:
    sections = re.split(r"(?m)^## +", body.decode("utf-8", errors="replace"))[1:]
    return [(version.strip(), text.strip()) for version, _newline, text in (section.partition("\n") for section in sections)]


def _claude_code_baseline(fetch: Fetch, now: datetime.datetime) -> str | None:
    """日付を持たない変更履歴の、収集の7日前の時点で先頭だった版を返す。  pyfltr: allow-comment"""
    until = (now - datetime.timedelta(days=INITIAL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    query = urllib.parse.urlencode({"path": "CHANGELOG.md", "until": until, "per_page": "1"})
    commits = _get_json(
        fetch,
        f"https://api.github.com/repos/{CLAUDE_CODE_REPOSITORY}/commits?{query}",
        {"Accept": "application/vnd.github+json"},
    )
    if not isinstance(commits, list):
        raise SourceError("変更履歴のcommit一覧の形式が想定と異なる")
    if not commits:
        return None
    sha = commits[0].get("sha") if isinstance(commits[0], dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SourceError("変更履歴のcommitの識別子が想定と異なる")
    status, body = fetch(f"https://raw.githubusercontent.com/{CLAUDE_CODE_REPOSITORY}/{sha}/CHANGELOG.md", {})
    if status != 200:
        raise SourceError(f"7日前の変更履歴を読めない: HTTP {status}")
    sections = _changelog_sections(body)
    return sections[0][0] if sections else None


def read_claude_code(fetch: Fetch, position: str | None, *, now: datetime.datetime) -> SourceResult:
    status, body = fetch(CLAUDE_CODE_CHANGELOG, {})
    if status != 200:
        raise SourceError(f"HTTP {status}")
    sections = _changelog_sections(body)
    versions = [version for version, _text in sections]
    reason = ""
    stop = position
    if position is None or position not in versions:
        # 前回の版が無い初回と、前回の版が変更履歴から消えた回は、7日前の時点の先頭の版より後を読む。  # pyfltr: allow-comment
        stop = _claude_code_baseline(fetch, now)
        if position is not None:
            reason = f"前回の版 {position} が変更履歴に無いため、7日前の時点の版より後を読んだ"
        if stop is not None and stop not in versions:
            raise SourceError(f"前回の版も7日前の時点の版 {stop} も変更履歴に見つからない")
    items = []
    for version, text in sections:
        if version == stop:
            break
        items.append({"version": version, "text": _excerpt(text)})
    return SourceResult("changelog:claude-code", "read", reason, items, versions[0] if versions else position)


def read_github_releases(
    fetch: Fetch, name: str, repository: str, position: str | None, *, now: datetime.datetime
) -> SourceResult:
    since = datetime.datetime.fromisoformat(position or (now - datetime.timedelta(days=INITIAL_DAYS)).isoformat())
    items = []
    latest = since
    for page in range(1, GITHUB_PAGES + 1):
        releases = _get_json(
            fetch,
            f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}",
            {"Accept": "application/vnd.github+json"},
        )
        if not isinstance(releases, list):
            raise SourceError("リリース一覧の形式が想定と異なる")
        reached = not releases
        for release in releases:
            published = release.get("published_at") if isinstance(release, dict) else None
            if not isinstance(published, str) or release.get("draft"):
                continue
            published_at = datetime.datetime.fromisoformat(published.replace("Z", "+00:00"))
            if published_at <= since:
                reached = True
                continue
            items.append(
                {
                    "version": release.get("tag_name"),
                    "published_at": published,
                    "url": release.get("html_url"),
                    "text": _excerpt(release.get("body") or ""),
                }
            )
            latest = max(latest, published_at)
        if reached:
            return SourceResult(f"changelog:{name}", "read", items=items, position=latest.isoformat())
    # 前回の位置まで届かないまま上限のページに達した回は、位置を進めず未読の区間を残す。  # pyfltr: allow-comment
    raise SourceError(f"前回の位置より後のリリースが{GITHUB_PAGES * 100}件を超え、全てを読めなかった")


def x_token() -> str | None:
    configured = config.resolve_mutable_setting("weekly_research_x_env_file")
    path = pathlib.Path(configured) if configured else config_settings.config_dir() / X_ENV_FILENAME
    try:
        values = claude_connection.read_api_env_file(str(path))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, ValueError) as error:
        raise SourceError(f"キーのファイルを読めない: {path}（{type(error).__name__}）") from error
    return values.get(X_TOKEN_VARIABLE) or None


def _x_time(post_id: str) -> datetime.datetime:
    """投稿IDに含まれる生成時刻を返す（X APIの投稿IDは上位ビットにミリ秒の時刻を持つ）。  pyfltr: allow-comment"""
    return datetime.datetime.fromtimestamp(((int(post_id) >> 22) + _X_EPOCH_MS) / 1000, datetime.UTC)


def _x_time_text(value: datetime.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclasses.dataclass
class _XPage:
    posts: list[dict[str, Any]]
    newest: str | None
    oldest: str | None
    complete: bool


def _x_page(
    fetch: Fetch,
    token: str,
    budget: XBudget,
    url: str,
    params: dict[str, str],
    *,
    minimum: int,
    link: Callable[[dict[str, Any]], str],
) -> _XPage | None:
    """残りの件数で読める範囲だけを要求する。要求の最小件数に満たない残りでは送信せずNoneを返す。  pyfltr: allow-comment"""
    remaining = budget.max_posts - budget.posts
    if remaining < minimum:
        return None
    query = {**params, "max_results": str(min(100, remaining)), "tweet.fields": "created_at,author_id"}
    value = _get_json(fetch, f"{url}?{urllib.parse.urlencode(query)}", {"Authorization": f"Bearer {token}"})
    data = value.get("data", []) if isinstance(value, dict) else []
    meta = value.get("meta", {}) if isinstance(value, dict) else {}
    posts = [
        {"id": post.get("id"), "created_at": post.get("created_at"), "url": link(post), "text": post.get("text", "")}
        for post in data[:remaining]
        if isinstance(post, dict) and isinstance(post.get("id"), str) and post["id"].isdigit()
    ]
    budget.posts += len(posts)
    ids = sorted((post["id"] for post in posts), key=int)
    return _XPage(
        posts,
        ids[-1] if ids else None,
        ids[0] if ids else None,
        len(data) <= remaining and not meta.get("next_token"),
    )


_X_LIMIT_REASON = "投稿の読取の件数上限に達したため読まなかった"
_X_PARTIAL_REASON = "投稿の読取の件数上限か1回の取得の範囲に達し、古い側の投稿を次回へ読み残した"


def _x_lower(lower: dict[str, str], *, search: bool, now: datetime.datetime) -> tuple[dict[str, str], str]:
    """検索は直近7日だけを読めるため、範囲の下端が7日より前なら7日前へ移し、その旨を返す。  pyfltr: allow-comment"""
    if not search:
        return lower, ""
    floor = now - X_SEARCH_WINDOW
    start = (
        _x_time(lower["since_id"])
        if "since_id" in lower
        else datetime.datetime.fromisoformat(lower["start_time"].replace("Z", "+00:00"))
    )
    if start >= floor:
        return lower, ""
    return {"start_time": _x_time_text(floor)}, "検索APIは直近7日だけを読めるため、それより前の分は読めなかった"


def _read_x_source(
    fetch: Fetch,
    token: str,
    budget: XBudget,
    source: str,
    url: str,
    state: dict[str, Any],
    *,
    params: dict[str, str],
    search: bool,
    link: Callable[[dict[str, Any]], str],
    now: datetime.datetime,
) -> SourceResult:
    """読み残しの区間（gap）を先に読み、全て読めた時だけ前回位置より新しい分へ進む。  pyfltr: allow-comment

    1回の要求は新しい側から返るため、件数の上限で止まった時は古い側の未読区間を`gap`として残す。
    未読区間を全て読むまで新しい分を読まないため、情報源ごとの未読区間は常に1つ以下になる。
    """
    minimum = X_MIN_RESULTS["search" if search else "timeline"]
    position = state["positions"].get(source)
    gap = state["gaps"].get(source)
    items: list[dict[str, Any]] = []
    reasons: list[str] = []
    if gap:
        lower, note = _x_lower({key: gap[key] for key in ("since_id", "start_time") if key in gap}, search=search, now=now)
        reasons += [note] if note else []
        page = _x_page(fetch, token, budget, url, {**params, **lower, "until_id": gap["until_id"]}, minimum=minimum, link=link)
        if page is None:
            return SourceResult(source, "skipped", _X_LIMIT_REASON, position=position, gap=gap)
        items += page.posts
        if not page.complete and page.oldest is not None:
            reasons.append(_X_PARTIAL_REASON)
            return SourceResult(source, "read", "。".join(reasons), items, position, {**lower, "until_id": page.oldest})
        gap = None
    lower_value = (
        {"since_id": position} if position else {"start_time": _x_time_text(now - datetime.timedelta(days=INITIAL_DAYS))}
    )
    lower, note = _x_lower(lower_value, search=search, now=now)
    reasons += [note] if note else []
    page = _x_page(fetch, token, budget, url, {**params, **lower}, minimum=minimum, link=link)
    if page is None:
        # 全て読んだ未読区間は確定させ、新しい分は前回位置のまま次回へ残す。  # pyfltr: allow-comment
        status = "read" if source in state["gaps"] else "skipped"
        return SourceResult(source, status, "。".join([*reasons, _X_LIMIT_REASON]), items, position, gap)
    items += page.posts
    if not page.complete and page.oldest is not None:
        reasons.append(_X_PARTIAL_REASON)
        gap = {**lower, "until_id": page.oldest}
    return SourceResult(source, "read", "。".join(reasons), items, page.newest or position, gap)


def _account_link(account: str, post: dict[str, Any]) -> str:
    return f"https://x.com/{account}/status/{post.get('id')}"


def _search_link(post: dict[str, Any]) -> str:
    return f"https://x.com/i/web/status/{post.get('id')}"


def read_x(
    fetch: Fetch,
    state: dict[str, Any],
    *,
    accounts: tuple[str, ...],
    queries: tuple[str, ...],
    budget: XBudget,
    now: datetime.datetime,
    enabled: bool,
    token: str | None,
) -> tuple[list[SourceResult], dict[str, str]]:
    sources = [f"x:account:{account}" for account in accounts] + [f"x:search:{query}" for query in queries]
    if not enabled:
        reason = "X APIの利用開始の設定（weekly_research_x_api）が無いため読まなかった"
        return [SourceResult(source, "skipped", reason) for source in sources], {}
    if token is None:
        reason = f"X APIのキー（{X_TOKEN_VARIABLE}）が未設定のため読まなかった"
        return [SourceResult(source, "skipped", reason) for source in sources], {}
    state = {"positions": state.get("positions", {}), "user_ids": state.get("user_ids", {}), "gaps": state.get("gaps", {})}
    user_ids: dict[str, str] = {}
    results = []
    for account in accounts:
        source = f"x:account:{account}"
        try:
            user_id = state["user_ids"].get(account)
            if user_id is None:
                if budget.users >= budget.max_users:
                    results.append(SourceResult(source, "failed", "アカウントの照会の件数上限に達したため読まなかった"))
                    continue
                budget.users += 1
                found = _get_json(
                    fetch,
                    f"{X_API_BASE}/users/by/username/{urllib.parse.quote(account)}",
                    {"Authorization": f"Bearer {token}"},
                )
                user_id = found.get("data", {}).get("id") if isinstance(found, dict) else None
                if not isinstance(user_id, str):
                    raise SourceError("アカウントが見つからない")
                user_ids[account] = user_id
            results.append(
                _read_x_source(
                    fetch,
                    token,
                    budget,
                    source,
                    f"{X_API_BASE}/users/{user_id}/tweets",
                    state,
                    params={},
                    search=False,
                    link=functools.partial(_account_link, account),
                    now=now,
                )
            )
        except SourceError as error:
            results.append(SourceResult(source, "failed", _scrub(str(error), token)))
    for query in queries:
        source = f"x:search:{query}"
        try:
            results.append(
                _read_x_source(
                    fetch,
                    token,
                    budget,
                    source,
                    f"{X_API_BASE}/tweets/search/recent",
                    state,
                    params={"query": query},
                    search=True,
                    link=_search_link,
                    now=now,
                )
            )
        except SourceError as error:
            results.append(SourceResult(source, "failed", _scrub(str(error), token)))
    return results, user_ids


def _scrub(text: str, token: str | None) -> str:
    text = text.replace(token, "[REDACTED]") if token else text
    return redact_secrets(text)


def collect(slot: str, *, fetch: Fetch | None = None, now: datetime.datetime | None = None) -> dict[str, Any]:
    fetch = fetch or default_fetch
    now = now or datetime.datetime.now(datetime.UTC)
    state = load_state()
    positions = state["positions"]
    results: list[SourceResult] = []
    try:
        results.append(read_claude_code(fetch, positions.get("changelog:claude-code"), now=now))
    except (OSError, ValueError, SourceError) as error:
        results.append(SourceResult("changelog:claude-code", "failed", redact_secrets(str(error))))
    for name, repository in GITHUB_RELEASES.items():
        try:
            results.append(read_github_releases(fetch, name, repository, positions.get(f"changelog:{name}"), now=now))
        except (OSError, ValueError, SourceError) as error:
            results.append(SourceResult(f"changelog:{name}", "failed", redact_secrets(str(error))))
    enabled = config.resolve_mutable_setting("weekly_research_x_api") == "true"
    budget = XBudget(
        max_posts=int(config.resolve_mutable_setting("weekly_research_x_max_posts")),
        max_users=int(config.resolve_mutable_setting("weekly_research_x_max_users")),
    )
    accounts = config_settings.parse_string_list(config.resolve_mutable_setting("weekly_research_x_accounts"))
    queries = config_settings.parse_string_list(config.resolve_mutable_setting("weekly_research_x_queries"))
    token = None
    x_failure = None
    if enabled:
        try:
            token = x_token()
        except SourceError as error:
            x_failure = str(error)
    if x_failure is not None:
        sources = [f"x:account:{account}" for account in accounts] + [f"x:search:{query}" for query in queries]
        results.extend(SourceResult(source, "failed", x_failure) for source in sources)
        user_ids: dict[str, str] = {}
    else:
        x_results, user_ids = read_x(
            fetch, state, accounts=accounts, queries=queries, budget=budget, now=now, enabled=enabled, token=token
        )
        results.extend(x_results)
    read = [result for result in results if result.status == "read"]
    repositories = config_settings.parse_string_list(config.resolve_mutable_setting("weekly_research_repos"))
    model_sources = {
        **MODEL_SOURCES,
        **{f"web:tools:{repository}": f"{repository}が使う主要なツールと言語処理系の変更履歴" for repository in repositories},
    }
    initial = (now - datetime.timedelta(days=INITIAL_DAYS)).isoformat()
    pending = {
        "slot": slot,
        "collected_at": now.isoformat(),
        "positions": {result.source: result.position for result in read if result.position},
        "gaps": {result.source: result.gap for result in read if result.source.startswith("x:")},
        "user_ids": user_ids,
        "model_sources": list(model_sources),
    }
    _directory().mkdir(parents=True, exist_ok=True)
    session_review.write(pending_path(), pending)
    return {
        "slot": slot,
        "x_api": {"enabled": enabled, "posts_read": budget.posts, "users_looked_up": budget.users},
        "x_subscriptions": {"accounts": list(accounts), "queries": list(queries)},
        "sources": [
            {key: value for key, value in dataclasses.asdict(result).items() if key != "gap"} | {"count": len(result.items)}
            for result in results
        ],
        "model_sources": [
            {"source": source, "what": what, "since": positions.get(source, initial)} for source, what in model_sources.items()
        ],
        "result_uwi": RESULT_UWI,
    }
