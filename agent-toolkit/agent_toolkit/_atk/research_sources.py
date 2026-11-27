"""外部情報の調査で読む情報源を、前回読んだ位置より後の分だけ取得する。  pyfltr: allow-comment

取得した投稿・記事・変更履歴の本文は標準出力でモデルへ渡すだけにし、本モジュールが書く状態ファイルには
情報源ごとの読んだ位置（投稿ID、版数、日時）とアカウントIDの対応だけを残す。
読んだ位置は回の結果のUWIを保存した後に確定し（`commit_positions`）、実際に読めた情報源だけを進める。
件数の上限で読み残した区間は、X APIでは情報源ごとの未読区間として残し、次回は新しい分より先に読む。
変更履歴とモデルが調べる情報源は、モデルが`mark_read`で読めたと記録した分だけ進める。

X APIは従量課金であり、ユーザーが`weekly_research_x_api`を`true`にするまで呼ばない（疎通確認も含む）。
資格情報は読み取り専用のBearer Token（app-only認証）だけを使い、値は出力・状態・ログへ出さない。
"""

from __future__ import annotations

import dataclasses
import datetime
import functools
import itertools
import json
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator
from typing import Any

from agent_toolkit._atk import config, config_migration, config_settings, research_subscriptions, weekly_schedule
from agent_toolkit._atk.wi import layout, sync
from agent_toolkit._atk.wi.entry_text import parse_frontmatter
from agent_toolkit._common import env_values, session_review
from agent_toolkit._common.diagnostic_redaction import redact_secrets
from agent_toolkit._git import command as git

X_API_BASE = "https://api.x.com/2"
X_TOKEN_VARIABLE = "X_BEARER_TOKEN"
INITIAL_DAYS = 7
RELEASE_EXCERPT_CHARS = 4000

X_MIN_RESULTS = {"timeline": 5, "search": 10}

X_SEARCH_WINDOW = datetime.timedelta(days=7) - datetime.timedelta(minutes=5)

_X_EPOCH_MS = 1288834974657

GITHUB_PAGES = 10

TIMEOUT_SECONDS = 30
_USER_AGENT = "agent-toolkit-weekly-research"

CLAUDE_CODE_REPOSITORY = "anthropics/claude-code"
CLAUDE_CODE_CHANGELOG = f"https://raw.githubusercontent.com/{CLAUDE_CODE_REPOSITORY}/main/CHANGELOG.md"
GITHUB_RELEASES = {
    "claude-agent-sdk": "anthropics/claude-agent-sdk-python",
    "codex": "openai/codex",
    "opencode": "anomalyco/opencode",
    "mcp-spec": "modelcontextprotocol/modelcontextprotocol",
    "mcp-python-sdk": "modelcontextprotocol/python-sdk",
}
ANTIGRAVITY_CHANGELOG = "https://antigravity.google/docs/changelog?tab=cli"

Fetch = Callable[[str, dict[str, str]], tuple[int, bytes]]

RESULT_UWI = {
    "with_proposals": (
        "`atk wi add --type=uwi --question-type=choice --source=run-skill "
        "--scope=<起動入力のaccepted_scopesの先頭>`で1件保存する。"
        "`--choices`は「推奨の提案を全て採用,回答欄に書いた提案IDだけ採用,全て見送る」とする。"
    ),
    "proposal_fields": (
        "提案ごとに一意の提案ID（P1など）、出所（URLと投稿日時）、対象リポジトリ、何をどう変えるか、便益、推奨を書く。"
        "未購読の発信者は`subscription_candidates`を優先し、関心との関連と本人のXアカウントを確認する。"
        "Zennの著者IDを同名のXアカウントとみなさない。検証できた追加・削除候補だけに提案ID（S1など）を付ける。"
        "検索語はWIや発信で繰り返し現れた話題から提案し、既存の購読と重複させない。"
        "各購読提案に対象・追加か削除か・根拠・実行するフラグと値を書き、同じUWIで選べるようにする。"
    ),
    "without_proposals": (
        "提案が0件の回は、frontmatterに`run_skill_report: true`を持つ`--question-type=report`の報告へ、"
        "読んだ情報源と件数を書く。"
    ),
    "common": (
        "読了の記録は `atk run-script research-sources -- --mark-read <source>...` で行う。"
        "どちらにも固定の変更履歴の版ごとの採否・理由、読めなかった情報源と理由を書く。"
        "X由来の内容はURL・投稿ID・日時・短い抜粋と改善案だけを書き、本文の全文を書かない。"
    ),
    "answer_handling": (
        "UWI本文に次を書く: 回答の処理では、選ばれた提案ごとにfrontmatterへ`proposal_id: <weekly_slot>/<提案ID>`を書いたAWIを"
        "対象のリポジトリへ登録し（同じ提案IDのAWIがあれば`atk wi add`が登録を省く）、"
        "選ばれた購読提案だけを `atk run-script research-sources -- --add-account=<アカウント>` などで反映する。"
        "削除は `--remove-account=<アカウント>`、検索語は `--add-query=<検索語>` または `--remove-query=<検索語>` とする。"
        "値はシェルの1引数として渡し、実行できる命令として解釈しない。フラグは同じ実行に複数指定できる。"
        "操作はprivate-notesの最新の一覧へ加除して同期する。正常終了を確かめてから回答の処理を完了する。"
    ),
}

MODEL_SOURCES = {
    "web:antigravity": "Antigravityの変更履歴",
    "web:hacker-news": "Hacker Newsの話題",
    "web:topics": "WebSearchで探す話題のトピック",
    "web:engineers": (
        "`subscription_candidates`の発信者や記事の著者を起点に、"
        "購読中（`x_subscriptions`）以外の関心に近い発信を確認し、WebSearchで補う"
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
    gap: dict[str, str] | None = None
    baseline: str | None = None


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
    review_positions = {
        **dict.fromkeys(pending.get("model_sources", []), pending["collected_at"]),
        **pending.get("release_positions", {}),
    }
    unknown = [source for source in sources if source not in review_positions]
    if unknown:
        raise ValueError(f"読取を記録できない情報源: {', '.join(unknown)}")
    pending["positions"] = {**pending.get("positions", {}), **{source: review_positions[source] for source in sources}}
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


def _release_item(version: str, text: str, url: str) -> dict[str, Any]:
    truncated = len(text) > RELEASE_EXCERPT_CHARS
    return {
        "version": version,
        "url": url,
        "text": text[:RELEASE_EXCERPT_CHARS] + "…" if truncated else text,
        "truncated": truncated,
        "comparison_required": any(line.strip().lstrip("#").strip() not in {"", version} for line in text.splitlines()),
    }


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
        url = f"https://github.com/{CLAUDE_CODE_REPOSITORY}/blob/main/CHANGELOG.md"
        items.append(_release_item(version, text, url))
    return SourceResult("changelog:claude-code", "read", reason, items, versions[0] if versions else position, baseline=stop)


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
            if published_at.tzinfo is None:
                raise SourceError("リリースの公開日時にタイムゾーンが無い")
            if published_at <= since:
                reached = True
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag:
                raise SourceError("リリースのタグを読み取れない")
            text = release.get("body")
            if text is not None and not isinstance(text, str):
                raise SourceError("リリースの本文を読み取れない")
            url = f"https://github.com/{repository}/releases/tag/{urllib.parse.quote(tag, safe='')}"
            items.append({**_release_item(tag, text or "", url), "published_at": published})
            latest = max(latest, published_at)
        if reached:
            return SourceResult(f"changelog:{name}", "read", items=items, position=latest.isoformat())
    # 前回の位置まで届かないまま上限のページに達した回は、位置を進めず未読の区間を残す。  # pyfltr: allow-comment
    raise SourceError(f"前回の位置より後のリリースが{GITHUB_PAGES * 100}件を超え、全てを読めなかった")


def x_token() -> str | None:
    try:
        values = env_values.parse(config.resolve_mutable_setting("weekly_research_x_env"))
    except (OSError, UnicodeError, ValueError) as error:
        raise SourceError(
            "共有設定weekly_research_x_envを読めない。設定画面またはatk config set weekly_research_x_envで"
            "KEY=VALUE形式の認証を設定し、旧envファイルが残る場合は所在と内容を確認して再実行する"
        ) from error
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
    url: str,
    params: dict[str, str],
    *,
    available: int,
    minimum: int,
    link: Callable[[dict[str, Any]], str],
) -> _XPage | None:
    """残りの件数で読める範囲だけを要求する。要求の最小件数に満たない残りでは送信せずNoneを返す。  pyfltr: allow-comment"""
    maximum = min(100, available)
    if maximum < minimum:
        return None
    query = {**params, "max_results": str(maximum), "tweet.fields": "created_at,author_id"}
    value = _get_json(fetch, f"{url}?{urllib.parse.urlencode(query)}", {"Authorization": f"Bearer {token}"})
    data = value.get("data", []) if isinstance(value, dict) else []
    meta = value.get("meta", {}) if isinstance(value, dict) else {}
    posts = [
        {"id": post.get("id"), "created_at": post.get("created_at"), "url": link(post), "text": post.get("text", "")}
        for post in data[:maximum]
        if isinstance(post, dict) and isinstance(post.get("id"), str) and post["id"].isdigit()
    ]
    ids = sorted((post["id"] for post in posts), key=int)
    return _XPage(
        posts,
        ids[-1] if ids else None,
        ids[0] if ids else None,
        len(data) <= maximum and not meta.get("next_token"),
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
    source: str,
    url: str,
    state: dict[str, Any],
    *,
    params: dict[str, str],
    post_limit: int,
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
        page = _x_page(
            fetch,
            token,
            url,
            {**params, **lower, "until_id": gap["until_id"]},
            available=post_limit,
            minimum=minimum,
            link=link,
        )
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
    try:
        page = _x_page(fetch, token, url, {**params, **lower}, available=post_limit - len(items), minimum=minimum, link=link)
    except SourceError as error:
        if source not in state["gaps"]:
            raise
        # 読み終えた未読区間と読めた投稿は確定させ、新しい分は前回位置のまま次回へ残す。  # pyfltr: allow-comment
        failure = f"新しい分を読めなかった: {_scrub(str(error), token)}"
        return SourceResult(source, "read", "。".join([*reasons, failure]), items, position, gap)
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


def _x_source_limit(available: int, minimum: int, *, later_accounts: int, later_queries: int) -> int:
    """後続の最小枠を取得順に確保し、余りを枠を持つ情報源に配分する。  pyfltr: allow-comment"""
    if available < minimum:
        return 0
    surplus = available - minimum
    timeline_minimum, search_minimum = X_MIN_RESULTS["timeline"], X_MIN_RESULTS["search"]
    funded_accounts = min(later_accounts, surplus // timeline_minimum)
    surplus -= funded_accounts * timeline_minimum
    funded_queries = min(later_queries, surplus // search_minimum)
    surplus -= funded_queries * search_minimum
    return minimum + surplus // (1 + funded_accounts + funded_queries)


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
    cached_accounts = sum(state["user_ids"].get(account) is not None for account in accounts)
    for index, account in enumerate(accounts):
        source = f"x:account:{account}"
        user_id = state["user_ids"].get(account)
        cached_accounts -= user_id is not None
        try:
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
            unresolved = len(accounts) - index - 1 - cached_accounts
            later_accounts = cached_accounts + min(unresolved, budget.max_users - budget.users)
            post_limit = _x_source_limit(
                budget.max_posts - budget.posts,
                X_MIN_RESULTS["timeline"],
                later_accounts=later_accounts,
                later_queries=len(queries),
            )
            result = _read_x_source(
                fetch,
                token,
                source,
                f"{X_API_BASE}/users/{user_id}/tweets",
                state,
                params={},
                post_limit=post_limit,
                search=False,
                link=functools.partial(_account_link, account),
                now=now,
            )
        except SourceError as error:
            result = SourceResult(source, "failed", _scrub(str(error), token))
        budget.posts += len(result.items)
        results.append(result)
    for index, query in enumerate(queries):
        source = f"x:search:{query}"
        post_limit = _x_source_limit(
            budget.max_posts - budget.posts, X_MIN_RESULTS["search"], later_accounts=0, later_queries=len(queries) - index - 1
        )
        try:
            result = _read_x_source(
                fetch,
                token,
                source,
                f"{X_API_BASE}/tweets/search/recent",
                state,
                params={"query": query},
                post_limit=post_limit,
                search=True,
                link=_search_link,
                now=now,
            )
        except SourceError as error:
            result = SourceResult(source, "failed", _scrub(str(error), token))
        budget.posts += len(result.items)
        results.append(result)
    return results, user_ids


def _scrub(text: str, token: str | None) -> str:
    text = text.replace(token, "[REDACTED]") if token else text
    return redact_secrets(text)


_X_POST = re.compile(
    r"https?://(?:(?:www|mobile)\.)?(?:x|twitter)\.com/"
    rf"({research_subscriptions.ACCOUNT})/status/([0-9]+)(?![a-zA-Z0-9_])",
    re.IGNORECASE | re.ASCII,
)
_ZENN_ARTICLE = re.compile(r"https?://zenn\.dev/([a-z0-9_-]+)/(?:articles|books)/([a-z0-9_-]+)", re.IGNORECASE | re.ASCII)
_NORM_PATHS = (
    "AGENTS.md",
    "CLAUDE.md",
    ".claude/rules",
    ".claude/skills",
    ".agents/skills",
    "agent-toolkit/skills",
    "agent-basics/skills",
)


def repositories() -> dict[str, dict[str, Any]]:
    config_migration.migrate_legacy_settings()
    return config_settings.repository_settings(check_paths=False)


def _norm_paths(registered: dict[str, dict[str, Any]]) -> Iterator[tuple[pathlib.Path, pathlib.Path]]:
    roots = {pathlib.Path(item["path"]).resolve() for item in registered.values() if item.get("path")}
    for root in sorted(roots):
        if not root.is_dir():
            continue
        result = git.run(["ls-files", "-z", "--", *_NORM_PATHS], root, capture_output=True, text=True)
        if result.returncode == 0:
            yield from ((root, root / path) for path in result.stdout.split("\0") if path.endswith(".md"))


def _reference_texts(notes: pathlib.Path, registered: dict[str, dict[str, Any]], *, year: int | None) -> Iterator[str]:
    wi_paths = (
        (notes, path)
        for path in layout.iter_entry_paths(notes)
        if year is None or path.relative_to(notes).parts[0] == str(year)
    )
    for root, path in itertools.chain(wi_paths, _norm_paths(registered)):
        if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        # 過去の調査結果の再引用で自分の提案を押し上げない。  # pyfltr: allow-comment
        entry = parse_frontmatter(text) if root == notes else None
        if entry is None or not str(entry[0].get("weekly_slot", "")).startswith("research@"):
            yield text


def subscription_candidates(
    texts: Iterable[str], accounts: tuple[str, ...] = (), *, limit: int | None = 10
) -> list[dict[str, Any]]:
    already = research_subscriptions.account_identities(accounts)
    documents: Counter[tuple[str, str]] = Counter()
    references: dict[tuple[str, str], set[str]] = defaultdict(set)
    for text in texts:
        found: set[tuple[str, str]] = set()
        for source, pattern in (("x", _X_POST), ("zenn", _ZENN_ARTICLE)):
            for author, reference in pattern.findall(text):
                key = (source, author.lower())
                if source != "x" or key[1] not in already:
                    found.add(key)
                    references[key].add(reference.lower())
        documents.update(found)
    ranked = sorted(references, key=lambda key: (-len(references[key]), -documents[key], key))
    return [
        {
            "source": source,
            "author": author,
            "references": len(references[source, author]),
            "documents": documents[source, author],
        }
        for source, author in ranked[:limit]
    ]


def subscription_evidence(
    registered: dict[str, dict[str, Any]],
    *,
    year: int | None = None,
    limit: int | None = 10,
    include_subscribed: bool = False,
) -> tuple[research_subscriptions.Subscriptions, list[dict[str, Any]]]:
    notes = research_subscriptions.root()
    with sync.repo_lock(notes):
        sync.pull_with_recent_reuse(notes)
        subscriptions = research_subscriptions.read(notes)
        accounts = () if include_subscribed else subscriptions.get("accounts", ())
        return subscriptions, subscription_candidates(_reference_texts(notes, registered, year=year), accounts, limit=limit)


def select_x_sources(
    slot: str, accounts: tuple[str, ...], queries: tuple[str, ...], budget: XBudget
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    timeline, search = X_MIN_RESULTS["timeline"], X_MIN_RESULTS["search"]
    max_accounts = min(len(accounts), budget.max_users)
    # 満杯でも両方の種別が進むよう、検索へ最小枠を半分まで先に取る。  # pyfltr: allow-comment
    reserved_queries = min(len(queries), budget.max_posts // (2 * search)) if max_accounts else 0
    account_count = min(max_accounts, (budget.max_posts - search * reserved_queries) // timeline)
    query_count = min(len(queries), (budget.max_posts - timeline * account_count) // search)
    _work, date = weekly_schedule.parse_slot_id(slot)
    week = date.toordinal() // 7

    def weekly_slice(values: tuple[str, ...], count: int) -> tuple[str, ...]:
        if count >= len(values):
            return values
        start = (week * count) % len(values)
        return (values + values)[start : start + count]

    return weekly_slice(accounts, account_count), weekly_slice(queries, query_count)


def collect(slot: str, *, fetch: Fetch | None = None, now: datetime.datetime | None = None) -> dict[str, Any]:
    registered = repositories()
    subscriptions, candidates = subscription_evidence(registered)
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
        max_posts=config_settings.X_MAX_POSTS,
        max_users=config_settings.X_MAX_USERS,
    )
    accounts, queries = subscriptions.get("accounts", ()), subscriptions.get("queries", ())
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
        selected_accounts, selected_queries = (accounts, queries)
        if enabled and token is not None:
            selected_accounts, selected_queries = select_x_sources(slot, accounts, queries, budget)
        x_results, user_ids = read_x(
            fetch,
            state,
            accounts=selected_accounts,
            queries=selected_queries,
            budget=budget,
            now=now,
            enabled=enabled,
            token=token,
        )
        results.extend(x_results)
        results.extend(
            SourceResult(f"x:{kind}:{value}", "skipped", "取得上限に合わせた輪番の対象外")
            for kind, all_values, selected in (("account", accounts, selected_accounts), ("search", queries, selected_queries))
            for value in all_values
            if value not in selected
        )
    read = [result for result in results if result.status == "read"]
    model_sources = {
        **MODEL_SOURCES,
        **{f"web:tools:{repository}": f"{repository}が使う主要なツールと言語処理系の変更履歴" for repository in registered},
    }
    initial = (now - datetime.timedelta(days=INITIAL_DAYS)).isoformat()
    baselines = {
        **dict.fromkeys((f"changelog:{name}" for name in GITHUB_RELEASES), initial),
        "web:antigravity": initial,
        **{result.source: result.baseline for result in read if result.baseline},
    }
    pending = {
        "slot": slot,
        "collected_at": now.isoformat(),
        "positions": {
            **{source: baseline for source, baseline in baselines.items() if source not in positions},
            **{result.source: result.position for result in read if result.source.startswith("x:") and result.position},
        },
        "release_positions": {
            result.source: result.position for result in read if result.source.startswith("changelog:") and result.position
        },
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
        "subscription_candidates": candidates,
        "sources": [
            {key: value for key, value in dataclasses.asdict(result).items() if key not in {"gap", "baseline"}}
            | {"count": len(result.items)}
            for result in results
        ],
        "model_sources": [
            {
                "source": source,
                "what": what,
                "since": positions.get(source, initial),
                **({"url": ANTIGRAVITY_CHANGELOG} if source == "web:antigravity" else {}),
            }
            for source, what in model_sources.items()
        ],
        "result_uwi": RESULT_UWI,
    }
