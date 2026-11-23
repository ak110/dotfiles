"""外部情報の調査で読む情報源を、前回読んだ位置より後の分だけ取得する。  pyfltr: allow-comment

取得した投稿・記事・変更履歴の本文は標準出力でモデルへ渡すだけにし、本モジュールが書く状態ファイルには
情報源ごとの読んだ位置（投稿ID、版数、日時）とアカウントIDの対応だけを残す。
読んだ位置は回の結果のUWIを保存した後に確定し（`commit_positions`）、実際に読めた情報源だけを進める。

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

INITIAL_UNDATED_ENTRIES = 10

TIMEOUT_SECONDS = 30
_USER_AGENT = "agent-toolkit-weekly-research"

CLAUDE_CODE_CHANGELOG = "https://raw.githubusercontent.com/anthropics/claude-code/main/CHANGELOG.md"
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
        "購読する発信元の追加・削除の候補は提案ID（S1など）を付けて同じUWIで選べるようにする。"
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
        "選ばれた購読の変更を`atk config set weekly_research_x_accounts`へ反映する。"
    ),
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
    return {"positions": dict(state.get("positions", {})), "user_ids": dict(state.get("user_ids", {}))}


def commit_positions(slot: str) -> bool:
    pending = _read(pending_path())
    if pending.get("slot") != slot:
        return False
    state = load_state()
    state["positions"].update(pending.get("positions", {}))
    state["user_ids"].update(pending.get("user_ids", {}))
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


def read_claude_code(fetch: Fetch, position: str | None) -> SourceResult:
    status, body = fetch(CLAUDE_CODE_CHANGELOG, {})
    if status != 200:
        raise SourceError(f"HTTP {status}")
    sections = re.split(r"(?m)^## +", body.decode("utf-8", errors="replace"))[1:]
    items = []
    for section in sections:
        version, _newline, text = section.partition("\n")
        version = version.strip()
        if version == position:
            break
        items.append({"version": version, "text": _excerpt(text.strip())})
        if len(items) >= INITIAL_UNDATED_ENTRIES * (1 if position is None else 5):
            break
    latest = sections[0].partition("\n")[0].strip() if sections else position
    return SourceResult("changelog:claude-code", "read", items=items, position=latest)


def read_github_releases(
    fetch: Fetch, name: str, repository: str, position: str | None, *, now: datetime.datetime
) -> SourceResult:
    releases = _get_json(
        fetch,
        f"https://api.github.com/repos/{repository}/releases?per_page=30",
        {"Accept": "application/vnd.github+json"},
    )
    if not isinstance(releases, list):
        raise SourceError("リリース一覧の形式が想定と異なる")
    since = position or (now - datetime.timedelta(days=INITIAL_DAYS)).isoformat()
    items = []
    latest = position
    for release in releases:
        published = release.get("published_at") if isinstance(release, dict) else None
        if not isinstance(published, str) or release.get("draft"):
            continue
        published_at = datetime.datetime.fromisoformat(published.replace("Z", "+00:00"))
        if published_at <= datetime.datetime.fromisoformat(since):
            continue
        items.append(
            {
                "version": release.get("tag_name"),
                "published_at": published,
                "url": release.get("html_url"),
                "text": _excerpt(release.get("body") or ""),
            }
        )
        if latest is None or published_at > datetime.datetime.fromisoformat(latest):
            latest = published_at.isoformat()
    return SourceResult(f"changelog:{name}", "read", items=items, position=latest or since)


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


def _x_posts(
    fetch: Fetch,
    token: str,
    budget: XBudget,
    url: str,
    params: dict[str, str],
    *,
    link: Callable[[dict[str, Any]], str],
) -> tuple[list[dict[str, Any]], str | None, bool]:
    remaining = budget.max_posts - budget.posts
    if remaining <= 0:
        return [], None, True
    query = {**params, "max_results": str(min(100, max(5, remaining))), "tweet.fields": "created_at,author_id"}
    value = _get_json(fetch, f"{url}?{urllib.parse.urlencode(query)}", {"Authorization": f"Bearer {token}"})
    data = value.get("data", []) if isinstance(value, dict) else []
    meta = value.get("meta", {}) if isinstance(value, dict) else {}
    posts = []
    limited = False
    for post in data:
        if budget.posts >= budget.max_posts:
            limited = True
            break
        budget.posts += 1
        posts.append(
            {
                "id": post.get("id"),
                "created_at": post.get("created_at"),
                "url": link(post),
                "text": post.get("text", ""),
            }
        )
    newest = meta.get("newest_id") if posts and isinstance(meta.get("newest_id"), str) else None
    return posts, newest, limited or bool(meta.get("next_token"))


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
    start_time = (now - datetime.timedelta(days=INITIAL_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    positions = state["positions"]
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
            position = positions.get(source)
            params = {"since_id": position} if position else {"start_time": start_time}
            posts, newest, limited = _x_posts(
                fetch,
                token,
                budget,
                f"{X_API_BASE}/users/{user_id}/tweets",
                params,
                link=functools.partial(_account_link, account),
            )
            reason = "投稿の読取の件数上限か1回の取得の範囲に達し、古い側の投稿を読み残した" if limited else ""
            results.append(SourceResult(source, "read", reason, posts, newest or position))
        except SourceError as error:
            results.append(SourceResult(source, "failed", _scrub(str(error), token)))
    for query in queries:
        source = f"x:search:{query}"
        position = positions.get(source)
        params = {"query": query, **({"since_id": position} if position else {"start_time": start_time})}
        try:
            posts, newest, limited = _x_posts(
                fetch,
                token,
                budget,
                f"{X_API_BASE}/tweets/search/recent",
                params,
                link=_search_link,
            )
            reason = "投稿の読取の件数上限か1回の取得の範囲に達し、古い側の投稿を読み残した" if limited else ""
            results.append(SourceResult(source, "read", reason, posts, newest or position))
        except SourceError as error:
            results.append(SourceResult(source, "failed", _scrub(str(error), token)))
    return results, user_ids


def _scrub(text: str, token: str | None) -> str:
    text = text.replace(token, "[REDACTED]") if token else text
    return redact_secrets(text)


def collect(slot: str, *, fetch: Fetch = default_fetch, now: datetime.datetime | None = None) -> dict[str, Any]:
    now = now or datetime.datetime.now(datetime.UTC)
    state = load_state()
    positions = state["positions"]
    results: list[SourceResult] = []
    try:
        results.append(read_claude_code(fetch, positions.get("changelog:claude-code")))
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
    pending = {
        "slot": slot,
        "positions": {result.source: result.position for result in results if result.status == "read" and result.position},
        "user_ids": user_ids,
    }
    _directory().mkdir(parents=True, exist_ok=True)
    session_review.write(pending_path(), pending)
    return {
        "slot": slot,
        "x_api": {"enabled": enabled, "posts_read": budget.posts, "users_looked_up": budget.users},
        "sources": [dataclasses.asdict(result) | {"count": len(result.items)} for result in results],
        "result_uwi": RESULT_UWI,
    }
