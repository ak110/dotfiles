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
    def respond(url):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        since = int(query.get("since_id", ["0"])[0])
        posts = [
            {"id": str(newest - index), "created_at": "2026-11-21T00:00:00.000Z", "text": f"{_FULL_TEXT}{index}"}
            for index in range(count)
            if newest - index > since
        ][: int(query["max_results"][0])]
        meta: dict[str, object] = {"result_count": len(posts)}
        if posts:
            meta["newest_id"] = posts[0]["id"]
        return 200, json.dumps({"data": posts, "meta": meta}).encode()

    return respond


def _base_responses():
    return {
        research_sources.CLAUDE_CODE_CHANGELOG: (200, _CHANGELOG),
        "https://api.github.com/repos/openai/codex/releases": (
            200,
            _releases(("rust-v0.9", "2026-11-20T00:00:00Z"), ("rust-v0.8", "2026-11-01T00:00:00Z")),
        ),
        "https://api.github.com/repos/sst/opencode/releases": (500, b'{"message": "Server Error"}'),
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


def test_missing_key_does_not_call_x_api(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_ENV_FILE", str(tmp_path / "missing.env"))
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


def test_read_limit_stops_by_kind(x_settings, monkeypatch):
    del x_settings
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_API", "true")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_MAX_POSTS", "4")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WEEKLY_RESEARCH_X_MAX_USERS", "1")
    responses = {
        **_base_responses(),
        f"{research_sources.X_API_BASE}/users/by/username/poteto": (200, b'{"data": {"id": "11"}}'),
        f"{research_sources.X_API_BASE}/users/11/tweets": _timeline(10, newest=110),
        f"{research_sources.X_API_BASE}/tweets/search/recent": _timeline(3, newest=310),
    }
    fetch = _Recorded(responses)
    result = research_sources.collect(_SLOT, fetch=fetch, now=_NOW)
    sources = _by_source(result)
    assert result["x_api"]["posts_read"] == 4
    assert result["x_api"]["users_looked_up"] == 1
    assert sources["x:account:poteto"]["count"] == 4
    assert "件数上限" in sources["x:account:poteto"]["reason"]
    assert sources["x:account:mattpocockuk"]["status"] == "failed"
    assert "照会の件数上限" in sources["x:account:mattpocockuk"]["reason"]
    assert sources["x:search:claude code hooks"]["count"] == 0
    assert not any("mattpocockuk" in url for url in fetch.x_calls())


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
