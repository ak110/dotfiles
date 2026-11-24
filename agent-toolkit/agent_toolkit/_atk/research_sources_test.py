import argparse
import datetime
import json
import typing
import urllib.parse
from typing import Any

import pytest

from agent_toolkit._atk import research_sources, run_script, run_skill
from agent_toolkit._common import session_review

_SLOT = "research@2026-11-22"
_NOW = datetime.datetime(2026, 11, 22, 6, tzinfo=datetime.UTC)
_TOKEN = "AAAAAAAAAAAAAAAAAAAAAsecret-bearer-token-value"
_FULL_TEXT = "合成の投稿本文。" + "この文は記録へ残してはならない長い本文の一部である。" * 20
_CHANGELOG = b"# Changelog\n\n## 2.3.0\n\n- New hooks event\n\n## 2.2.0\n\n- Faster startup\n\n## 2.1.0\n\n- Old entry\n"


def _releases(*items):
    return json.dumps(
        [
            {"tag_name": tag, "published_at": published, "html_url": f"https://github.com/r/{tag}", "body": f"notes {tag}"}
            for tag, published in items
        ]
    ).encode()


class _Recorded:
    def __init__(self, responses: dict[str, Any]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        for prefix, response in self.responses.items():
            if url.startswith(prefix):
                return typing.cast(tuple[int, bytes], response(url) if callable(response) else response)
        raise OSError(f"未記録のURL: {url}")

    def x_calls(self):
        return [url for url, _headers in self.calls if url.startswith(research_sources.X_API_BASE)]


def _timeline(count, *, newest=200):
    """新しい側から返し、範囲内に残りがあれば`next_token`を付ける記録応答。  pyfltr: allow-comment"""

    def respond(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        since = int(query.get("since_id", ["0"])[0])
        until = int(query.get("until_id", [str(newest + 1)])[0])
        matched = [
            {"id": str(newest - index), "created_at": "2026-11-21T00:00:00.000Z", "text": f"{_FULL_TEXT}{index}"}
            for index in range(count)
            if since < newest - index < until
        ]
        posts = matched[: int(query["max_results"][0])]
        meta: dict[str, object] = {"result_count": len(posts)}
        if posts:
            meta["newest_id"] = posts[0]["id"]
            meta["oldest_id"] = posts[-1]["id"]
        if len(matched) > len(posts):
            meta["next_token"] = "next"
        return 200, json.dumps({"data": posts, "meta": meta}).encode()

    return respond


_BASELINE_SHA = "a" * 40

_COMMITS = f"https://api.github.com/repos/{research_sources.CLAUDE_CODE_REPOSITORY}/commits"


def _base_responses():
    return {
        research_sources.CLAUDE_CODE_CHANGELOG: (200, _CHANGELOG),
        _COMMITS: (200, json.dumps([{"sha": _BASELINE_SHA}]).encode()),
        f"https://raw.githubusercontent.com/{research_sources.CLAUDE_CODE_REPOSITORY}/{_BASELINE_SHA}/CHANGELOG.md": (
            200,
            b"## 2.2.0\n\n- Faster startup\n\n## 2.1.0\n\n- Old entry\n",
        ),
        "https://api.github.com/repos/openai/codex/releases": (
            200,
            _releases(("rust-v0.9", "2026-11-20T00:00:00Z"), ("rust-v0.8", "2026-11-01T00:00:00Z")),
        ),
        "https://api.github.com/repos/anomalyco/opencode/releases": (500, b'{"message": "Server Error"}'),
    }


@pytest.fixture(name="x_settings")
def _x_settings(monkeypatch, tmp_path):
    env_file = tmp_path / "x-api.env"
    env_file.write_text(f"{research_sources.X_TOKEN_VARIABLE}={_TOKEN}\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ENV_FILE", str(env_file))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", json.dumps(["poteto", "mattpocockuk"]))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_QUERIES", json.dumps(["claude code hooks"]))
    return env_file


def _by_source(result):
    return {source["source"]: source for source in result["sources"]}


def test_without_usage_start_x_api_is_not_called(x_settings):
    del x_settings
    fetch = _Recorded(_base_responses())
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    sources = _by_source(result)
    assert fetch.x_calls() == []
    assert result["x_api"] == {"enabled": False, "posts_read": 0, "users_looked_up": 0}
    assert sources["x:account:poteto"]["status"] == "skipped"
    assert "weekly_research_x_api" in sources["x:account:poteto"]["reason"]
    assert sources["changelog:claude-code"]["status"] == "read"
    assert sources["changelog:codex"]["count"] == 1


def test_missing_key_does_not_call_x_api(monkeypatch):
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", json.dumps(["poteto"]))
    fetch = _Recorded(_base_responses())
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    assert fetch.x_calls() == []
    assert "X_BEARER_TOKEN" in _by_source(result)["x:account:poteto"]["reason"]


def test_reads_only_after_previous_position_and_commits_after_save(x_settings, monkeypatch):
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    responses = {
        **_base_responses(),
        f"{research_sources.X_API_BASE}/users/by/username/poteto": (200, b'{"data": {"id": "11"}}'),
        f"{research_sources.X_API_BASE}/users/by/username/mattpocockuk": (200, b'{"data": {"id": "22"}}'),
        f"{research_sources.X_API_BASE}/users/11/tweets": _timeline(3, newest=105),
        f"{research_sources.X_API_BASE}/users/22/tweets": _timeline(2, newest=205),
        f"{research_sources.X_API_BASE}/tweets/search/recent": _timeline(1, newest=305),
    }
    fetch = _Recorded(responses)
    first = _by_source(research_sources.collect(_SLOT, fetch=fetch, now=_NOW))
    assert first["x:account:poteto"]["count"] == 3
    assert "start_time" in fetch.x_calls()[1]
    assert not research_sources.load_state()["positions"]
    research_sources.mark_read(_SLOT, ("changelog:claude-code",))
    assert research_sources.commit_positions(_SLOT)
    state = research_sources.load_state()
    assert state["positions"]["x:account:poteto"] == "105"
    assert state["positions"]["changelog:claude-code"] == "2.3.0"
    assert state["user_ids"] == {"poteto": "11", "mattpocockuk": "22"}

    responses[f"{research_sources.X_API_BASE}/users/11/tweets"] = _timeline(5, newest=107)
    responses[research_sources.CLAUDE_CODE_CHANGELOG] = (200, b"## 2.4.0\n\n- Newer\n\n" + _CHANGELOG)
    fetch = _Recorded(responses)
    second = _by_source(research_sources.collect("research@2026-11-29", fetch=fetch, now=_NOW))
    assert [post["id"] for post in second["x:account:poteto"]["items"]] == ["107", "106"]
    assert [item["version"] for item in second["changelog:claude-code"]["items"]] == ["2.4.0"]
    assert not any("/users/by/username/" in url for url in fetch.x_calls())
    assert any("since_id=105" in url for url in fetch.x_calls())


def _max_results(fetch, path):
    return [
        int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["max_results"][0]) for url in fetch.x_calls() if path in url
    ]


@pytest.mark.parametrize(
    ("max_posts", "timeline_requests", "posts_read"),
    [(4, [], 0), (5, [5], 5), (12, [12], 10)],
)
def test_read_limit_stops_by_kind_without_overrequest(x_settings, monkeypatch, max_posts, timeline_requests, posts_read):
    """投稿一覧は5件、検索は10件が要求の最小であり、残りがそれに満たなければ送信しない。  pyfltr: allow-comment"""
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setattr(research_sources.config_settings, "X_MAX_POSTS", max_posts)
    monkeypatch.setattr(research_sources.config_settings, "X_MAX_USERS", 1)
    responses = {
        **_base_responses(),
        f"{research_sources.X_API_BASE}/users/by/username/poteto": (200, b'{"data": {"id": "11"}}'),
        f"{research_sources.X_API_BASE}/users/11/tweets": _timeline(10, newest=110),
        f"{research_sources.X_API_BASE}/tweets/search/recent": _timeline(3, newest=310),
    }
    fetch = _Recorded(responses)
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    sources = _by_source(result)
    assert _max_results(fetch, "/users/11/tweets") == timeline_requests
    assert _max_results(fetch, "/tweets/search/recent") == []
    assert result["x_api"]["posts_read"] == posts_read
    assert result["x_api"]["users_looked_up"] == 1
    assert sources["x:account:poteto"]["count"] == posts_read
    assert "件数上限" in sources["x:account:poteto"]["reason"] or posts_read == 10
    assert sources["x:account:mattpocockuk"]["status"] == "failed"
    assert "照会の件数上限" in sources["x:account:mattpocockuk"]["reason"]
    assert sources["x:search:claude code hooks"]["status"] == "skipped"
    assert "件数上限" in sources["x:search:claude code hooks"]["reason"]
    assert not any("mattpocockuk" in url for url in fetch.x_calls())


def test_search_requests_at_least_ten_within_budget(x_settings, monkeypatch):
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", "[]")
    monkeypatch.setattr(research_sources.config_settings, "X_MAX_POSTS", 10)
    responses = {**_base_responses(), f"{research_sources.X_API_BASE}/tweets/search/recent": _timeline(3, newest=310)}
    fetch = _Recorded(responses)
    sources = _by_source(research_sources.collect(_SLOT, fetch=fetch, now=_NOW))
    assert _max_results(fetch, "/tweets/search/recent") == [10]
    assert sources["x:search:claude code hooks"]["count"] == 3


def test_partial_read_keeps_unread_range_for_next_run(x_settings, monkeypatch):
    """上限で古い側を読み残した区間は次回に先に読み、どの投稿も欠かさず二重にも読まない。  pyfltr: allow-comment"""
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", json.dumps(["poteto"]))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_QUERIES", "[]")
    monkeypatch.setattr(research_sources.config_settings, "X_MAX_POSTS", 5)
    session_review.directory().joinpath("weekly").mkdir(parents=True, exist_ok=True)
    session_review.write(
        research_sources.positions_path(), {"positions": {"x:account:poteto": "100"}, "user_ids": {"poteto": "11"}}
    )
    timeline = f"{research_sources.X_API_BASE}/users/11/tweets"
    responses = {**_base_responses(), timeline: _timeline(12, newest=110)}
    read: list[str] = []
    for run, newest in enumerate((110, 112, 112)):
        responses[timeline] = _timeline(newest - 100, newest=newest)
        fetch = _Recorded(responses)
        slot = f"research@2026-11-{22 + run * 7:02d}" if run < 2 else "research@2026-12-06"
        sources = _by_source(research_sources.collect(slot, fetch=fetch, now=_NOW))
        read += [post["id"] for post in sources["x:account:poteto"]["items"]]
        assert all(size <= 5 for size in _max_results(fetch, "/tweets"))
        assert research_sources.commit_positions(slot)
    assert sorted(read, key=int) == [str(post_id) for post_id in range(101, 113)]
    state = research_sources.load_state()
    assert state["positions"]["x:account:poteto"] == "112"
    assert not state["gaps"]


def test_search_lower_bound_is_kept_within_seven_days(x_settings, monkeypatch):
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", "[]")
    old = _NOW - datetime.timedelta(days=7, minutes=1)
    old_id = str((int(old.timestamp() * 1000) - research_sources._X_EPOCH_MS) << 22)  # noqa: SLF001  # pylint: disable=protected-access
    session_review.directory().joinpath("weekly").mkdir(parents=True, exist_ok=True)
    session_review.write(research_sources.positions_path(), {"positions": {"x:search:claude code hooks": old_id}})
    fetch = _Recorded({**_base_responses(), f"{research_sources.X_API_BASE}/tweets/search/recent": _timeline(0)})
    sources = _by_source(research_sources.collect(_SLOT, fetch=fetch, now=_NOW))
    (url,) = fetch.x_calls()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert "since_id" not in query
    assert datetime.datetime.fromisoformat(query["start_time"][0].replace("Z", "+00:00")) > _NOW - datetime.timedelta(days=7)
    assert "直近7日" in sources["x:search:claude code hooks"]["reason"]


def test_unreadable_sources_keep_position_and_hide_key(x_settings, monkeypatch, caplog):
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    session_review.directory().joinpath("weekly").mkdir(parents=True, exist_ok=True)
    session_review.write(
        research_sources.positions_path(),
        {
            "positions": {"x:account:poteto": "90", "changelog:opencode": "2026-11-01T00:00:00+00:00"},
            "user_ids": {"poteto": "11"},
        },
    )
    responses = {
        **_base_responses(),
        f"{research_sources.X_API_BASE}/users/11/tweets": (
            402,
            json.dumps({"title": "CreditsDepleted", "detail": f"token {_TOKEN} has no credits"}).encode(),
        ),
        f"{research_sources.X_API_BASE}/users/by/username/mattpocockuk": (401, b'{"title": "Unauthorized"}'),
        f"{research_sources.X_API_BASE}/tweets/search/recent": (429, b'{"title": "Too Many Requests"}'),
    }
    fetch = _Recorded(responses)
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    sources = _by_source(result)
    assert sources["x:account:poteto"]["status"] == "failed"
    assert "HTTP 402" in sources["x:account:poteto"]["reason"]
    assert sources["changelog:opencode"]["status"] == "failed"
    assert all(headers.get("Authorization") == f"Bearer {_TOKEN}" for url, headers in fetch.calls if "api.x.com" in url)
    research_sources.commit_positions(_SLOT)
    state = research_sources.load_state()
    assert state["positions"]["x:account:poteto"] == "90"
    assert state["positions"]["changelog:opencode"] == "2026-11-01T00:00:00+00:00"
    written = json.dumps(result, ensure_ascii=False) + caplog.text
    written += "".join(path.read_text(encoding="utf-8") for path in session_review.directory().rglob("*.json"))
    assert _TOKEN not in written


def test_records_keep_no_full_post_text(x_settings, monkeypatch):
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    responses = {
        **_base_responses(),
        f"{research_sources.X_API_BASE}/users/by/username/": (200, b'{"data": {"id": "11"}}'),
        f"{research_sources.X_API_BASE}/users/11/tweets": _timeline(2, newest=105),
        f"{research_sources.X_API_BASE}/tweets/search/recent": _timeline(1, newest=305),
    }
    result = research_sources.collect(_SLOT, fetch=_Recorded(responses), now=_NOW)
    output = json.dumps(result, ensure_ascii=False)
    assert _FULL_TEXT in output
    research_sources.commit_positions(_SLOT)
    stored = "".join(path.read_text(encoding="utf-8") for path in session_review.directory().rglob("*") if path.is_file())
    assert _FULL_TEXT[:40] not in stored
    event = {"type": "user", "message": {"content": [{"type": "tool_result", "content": output}]}}
    summarized = run_skill.summarize_event_line(json.dumps(event, ensure_ascii=False))
    assert _FULL_TEXT[:40] not in summarized
    assert json.loads(summarized)["type"] == "user"
    assert run_skill.summarize_event_line(_FULL_TEXT) == json.dumps({"omitted_chars": len(_FULL_TEXT)})


def test_script_prints_result_for_launched_slot(monkeypatch, capsys):
    monkeypatch.setenv(session_review.WEEKLY_SLOT_ENV, _SLOT)
    seen = []

    def collect(slot, **_kwargs):
        seen.append(slot)
        return {"slot": slot, "sources": []}

    monkeypatch.setattr(research_sources, "collect", collect)
    assert run_script.dispatch(argparse.Namespace(script_name="research-sources", script_args=[])) == 0
    assert json.loads(capsys.readouterr().out)["slot"] == _SLOT
    assert seen == [_SLOT]


def test_claude_code_first_run_reads_versions_since_seven_days_ago():
    fetch = _Recorded(_base_responses())
    sources = _by_source(research_sources.collect(_SLOT, fetch=fetch, now=_NOW))
    assert [item["version"] for item in sources["changelog:claude-code"]["items"]] == ["2.3.0"]
    (commits,) = [url for url, _headers in fetch.calls if url.startswith(_COMMITS)]
    assert "until=2026-11-15T06%3A00%3A00Z" in commits


@pytest.mark.parametrize(
    ("position", "baseline", "status", "versions"),
    [
        ("2.2.0", None, "read", ["2.3.0"]),
        ("1.0.0", b"## 2.1.0\n", "read", ["2.3.0", "2.2.0"]),
        ("1.0.0", b"## 0.9.0\n", "failed", []),
    ],
)
def test_claude_code_position_is_not_skipped(position, baseline, status, versions):
    """前回の版が変更履歴に無い時は7日前の版まで読み、どちらも無ければ位置を進めない。  pyfltr: allow-comment"""
    session_review.directory().joinpath("weekly").mkdir(parents=True, exist_ok=True)
    session_review.write(research_sources.positions_path(), {"positions": {"changelog:claude-code": position}})
    responses = _base_responses()
    if baseline is not None:
        responses[
            f"https://raw.githubusercontent.com/{research_sources.CLAUDE_CODE_REPOSITORY}/{_BASELINE_SHA}/CHANGELOG.md"
        ] = (
            200,
            baseline,
        )
    sources = _by_source(research_sources.collect(_SLOT, fetch=_Recorded(responses), now=_NOW))
    assert sources["changelog:claude-code"]["status"] == status
    assert [item["version"] for item in sources["changelog:claude-code"]["items"]] == versions
    if status == "read":
        research_sources.mark_read(_SLOT, ("changelog:claude-code",))
    else:
        with pytest.raises(ValueError, match="changelog:claude-code"):
            research_sources.mark_read(_SLOT, ("changelog:claude-code",))
    research_sources.commit_positions(_SLOT)
    expected = "2.3.0" if status == "read" else position
    assert research_sources.load_state()["positions"]["changelog:claude-code"] == expected


def _release_pages(pages):
    def respond(url):
        page = int(urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["page"][0])
        return 200, _releases(*pages[page - 1]) if page <= len(pages) else b"[]"

    return respond


@pytest.mark.parametrize("reach_previous", [True, False])
def test_github_releases_follow_pages_until_previous_position(monkeypatch, reach_previous):
    monkeypatch.setattr(research_sources, "GITHUB_PAGES", 2)
    newer = [(f"v{index}", f"2026-11-21T{index // 60:02d}:{index % 60:02d}:00Z") for index in range(100)]
    older = (
        [("v-old", "2026-11-01T00:00:00Z")]
        if reach_previous
        else [(f"w{index}", "2026-11-20T00:00:00Z") for index in range(100)]
    )
    responses = {**_base_responses(), "https://api.github.com/repos/openai/codex/releases": _release_pages([newer, older])}
    sources = _by_source(research_sources.collect(_SLOT, fetch=_Recorded(responses), now=_NOW))
    codex = sources["changelog:codex"]
    if reach_previous:
        research_sources.mark_read(_SLOT, ("changelog:codex",))
    research_sources.commit_positions(_SLOT)
    position = research_sources.load_state()["positions"].get("changelog:codex")
    if reach_previous:
        assert codex["count"] == 100
        assert position == "2026-11-21T01:39:00+00:00"
    else:
        assert codex["status"] == "failed"
        assert position == (_NOW - datetime.timedelta(days=7)).isoformat()


def test_model_sources_advance_only_when_marked_read(monkeypatch):
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_REPOS", json.dumps(["github.com/test/package"]))
    first = research_sources.collect(_SLOT, fetch=_Recorded(_base_responses()), now=_NOW)
    since = {source["source"]: source["since"] for source in first["model_sources"]}
    assert set(since) == {*research_sources.MODEL_SOURCES, "web:tools:github.com/test/package"}
    assert set(since.values()) == {(_NOW - datetime.timedelta(days=7)).isoformat()}
    with pytest.raises(ValueError, match="x:account:poteto"):
        research_sources.mark_read(_SLOT, ("x:account:poteto",))
    assert research_sources.mark_read(_SLOT, ("web:antigravity",)) == ["web:antigravity"]
    assert research_sources.commit_positions(_SLOT)
    later = _NOW + datetime.timedelta(days=8)
    second = research_sources.collect("research@2026-11-29", fetch=_Recorded(_base_responses()), now=later)
    since = {source["source"]: source["since"] for source in second["model_sources"]}
    assert since["web:antigravity"] == _NOW.isoformat()
    assert since["web:hacker-news"] == (later - datetime.timedelta(days=7)).isoformat()


def test_script_marks_model_sources_read(monkeypatch, capsys):
    monkeypatch.setenv(session_review.WEEKLY_SLOT_ENV, _SLOT)
    research_sources.collect(_SLOT, fetch=_Recorded(_base_responses()), now=_NOW)
    args = argparse.Namespace(script_name="research-sources", script_args=["--mark-read", "web:hacker-news"])
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out) == {"slot": _SLOT, "marked": ["web:hacker-news"]}
    research_sources.commit_positions(_SLOT)
    state = research_sources.load_state()["positions"]
    assert state["web:hacker-news"] == _NOW.isoformat()
    assert state["changelog:claude-code"] == "2.2.0"
    assert state["changelog:codex"] == (_NOW - datetime.timedelta(days=7)).isoformat()


@pytest.mark.parametrize(
    ("gap_status", "newer_status", "status", "ids", "position", "gap"),
    [
        (200, 200, "read", ["105", "104", "112", "111"], "112", None),
        (503, 200, "failed", [], "110", {"since_id": "103", "until_id": "106"}),
        (200, 503, "read", ["105", "104"], "110", None),
    ],
)
def test_unread_range_result_is_kept_when_newer_range_fails(
    x_settings, monkeypatch, gap_status, newer_status, status, ids, position, gap
):
    """未読区間の読取と新しい分の読取を分けて確定し、後半の失敗で前半の結果を失わない。  pyfltr: allow-comment"""
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ACCOUNTS", json.dumps(["poteto"]))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_QUERIES", "[]")
    session_review.directory().joinpath("weekly").mkdir(parents=True, exist_ok=True)
    session_review.write(
        research_sources.positions_path(),
        {
            "positions": {"x:account:poteto": "110"},
            "user_ids": {"poteto": "11"},
            "gaps": {"x:account:poteto": {"since_id": "103", "until_id": "106"}},
        },
    )
    timeline = _timeline(12, newest=112)

    def respond(url):
        gap_request = "until_id=" in url
        code = gap_status if gap_request else newer_status
        return timeline(url) if code == 200 else (code, json.dumps({"title": f"Unavailable {_TOKEN}"}).encode())

    fetch = _Recorded({**_base_responses(), f"{research_sources.X_API_BASE}/users/11/tweets": respond})
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    source = _by_source(result)["x:account:poteto"]
    assert source["status"] == status
    assert [item["id"] for item in source["items"]] == ids
    assert result["x_api"]["posts_read"] == len(ids)
    assert _TOKEN not in json.dumps(result, ensure_ascii=False)
    research_sources.commit_positions(_SLOT)
    state = research_sources.load_state()
    assert state["positions"]["x:account:poteto"] == position
    assert state["gaps"].get("x:account:poteto") == gap


def test_weekly_releases_require_review_of_truncated_versions_before_advancing():
    release_url = "https://github.com/anomalyco/opencode/releases/tag/v2.4.0"
    releases = [
        {
            "tag_name": "v2.4.0",
            "published_at": "2026-11-20T00:00:00Z",
            "html_url": release_url,
            "body": "x" * 4001 + "\nMCP Events",
        },
        {"tag_name": "v2.3.2", "published_at": "2026-11-01T00:00:00Z"},
    ]
    responses = _base_responses()
    responses.update(
        {
            "https://api.github.com/repos/anomalyco/opencode/releases": (200, json.dumps(releases).encode()),
            "https://api.github.com/repos/modelcontextprotocol/modelcontextprotocol/releases": (200, b"[]"),
            "https://api.github.com/repos/modelcontextprotocol/python-sdk/releases": (200, b"[]"),
        }
    )
    fetch = _Recorded(responses)
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    sources = _by_source(result)
    (item,) = sources["changelog:opencode"]["items"]
    assert item["version"] == "v2.4.0"
    assert item["url"] == release_url
    assert item["truncated"] is True
    assert sources["changelog:mcp-spec"]["status"] == "read"
    assert sources["changelog:mcp-python-sdk"]["status"] == "read"
    assert next(source for source in result["model_sources"] if source["source"] == "web:antigravity")["url"]

    assert research_sources.commit_positions(_SLOT)
    assert research_sources.load_state()["positions"]["changelog:opencode"] == (_NOW - datetime.timedelta(days=7)).isoformat()
    next_slot = "research@2026-11-29"
    later = _by_source(research_sources.collect(next_slot, fetch=fetch, now=_NOW + datetime.timedelta(days=7)))
    assert [item["version"] for item in later["changelog:opencode"]["items"]] == ["v2.4.0"]
    assert research_sources.mark_read(next_slot, ("changelog:opencode",)) == ["changelog:opencode"]
    assert research_sources.commit_positions(next_slot)
    assert research_sources.load_state()["positions"]["changelog:opencode"] == "2026-11-20T00:00:00+00:00"


@pytest.mark.parametrize("first_posts", [0, 100])
def test_shared_x_budget_reads_all_subscriptions(first_posts):
    accounts = tuple(f"account{index}" for index in range(8))
    queries = tuple(f"query {index}" for index in range(5))
    ids = {account: str(index) for index, account in enumerate(accounts, 1)}
    responses = {
        f"{research_sources.X_API_BASE}/users/{user_id}/tweets": _timeline(first_posts if user_id == "1" else 100)
        for user_id in ids.values()
    }
    responses[f"{research_sources.X_API_BASE}/tweets/search/recent"] = _timeline(100)
    fetch = _Recorded(responses)
    budget = research_sources.XBudget(max_posts=100, max_users=20)

    results, _ = research_sources.read_x(
        fetch, {"user_ids": ids}, accounts=accounts, queries=queries, budget=budget, now=_NOW, enabled=True, token=_TOKEN
    )

    timelines = _max_results(fetch, "/users/")
    searches = _max_results(fetch, "/tweets/search/recent")
    assert results[0].status == "read" and bool(results[0].items) == bool(first_posts)
    assert len(timelines) == 8 and all(size >= 5 for size in timelines)
    assert len(searches) == 5 and all(size >= 10 for size in searches)
    assert all(result.status == "read" and result.items for result in results[1:])
    assert budget.posts == 100
    assert sum(timelines[1:] + searches) == (100 if first_posts == 0 else 100 - timelines[0])
    if first_posts == 0:
        assert sum(timelines[1:]) > 7 * 5
