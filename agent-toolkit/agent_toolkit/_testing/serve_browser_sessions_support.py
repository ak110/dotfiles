"""serveのbrowser_sessions試験が共有する入力と補助処理。"""

import asyncio
import base64
import contextlib
import dataclasses
import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve.browser_support_test import (  # noqa: F401  # pylint: disable=unused-import
    _browser_app,
    _isolate_creation_time_index,
    _ScreenHarness,
    _serve,
    _valid_diagram_markdown,
    _write_entries,
)


@dataclasses.dataclass
class _RemoteSessionsHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    base_url: str


_NEW_REMOTE_HOST = "new-host"


_LEGACY_REMOTE_HOST = "legacy-host"


_REMOTE_RECORD_PATH = "/home/remote/.claude/projects/-home-remote-proj/33333333-4444-5555-6666-777777777777.jsonl"


async def _remote_sessions_runner(host: str, op: str, _args: list[str]) -> str:
    """リモートヘルパーの応答を模す。旧版のホストは`read`応答へサブエージェント一覧を含めない。

    `read`の対象は1件だけであり、渡されたパスを見分ける必要が無いため引数を使わない。
    """
    if op == "list":
        return json.dumps(
            {
                "ok": True,
                "host": host,
                "entries": [
                    {
                        "engine": "claude",
                        "cwd": "/home/remote/proj",
                        "first_user_message": "リモートの最初の発話",
                        "session_id": f"{host}-session",
                        "path": _REMOTE_RECORD_PATH,
                        "updated_at": 1_800_000_000,
                        "size": 120,
                    }
                ],
            },
            ensure_ascii=False,
        )
    text = (
        json.dumps(
            {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": f"{host}の発話"}},
            ensure_ascii=False,
        )
        + "\n"
    )
    payload: dict[str, Any] = {"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    if host == _NEW_REMOTE_HOST:
        payload["subagents"] = [
            {
                "agent_id": "agent-remote",
                "agent_type": "Explore",
                "description": "リモートの調査",
                "spawn_depth": 1,
                "parent_agent_id": None,
                "model": "opus",
                "path": None,
            }
        ]
    return json.dumps(payload, ensure_ascii=False)


@pytest_asyncio.fixture(loop_scope="session", name="remote_sessions_harness")
async def _remote_sessions_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_RemoteSessionsHarness]:
    """サブエージェント一覧を返すリモートホストと、返さない旧版のリモートホストを登録した画面を用意する。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    (plans_root / "plan.md").write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    # 常駐接続は実際のsshを起動するため開始させず、単発SSHの差し替えだけでリモートの応答を与える。
    monkeypatch.setattr(serve_sessions, "start_remote_clients", lambda context: None)
    app = _browser_app(
        tmp_path,
        plans_root,
        remote_hosts=[_NEW_REMOTE_HOST, _LEGACY_REMOTE_HOST],
        ssh_runner=_remote_sessions_runner,
    )
    async with _serve(app, browser) as (context, page, port):
        yield _RemoteSessionsHarness(page=page, context=context, base_url=f"http://127.0.0.1:{port}")


_SESSION_MARKDOWN = (
    "## 調査結果\n\n- 1つ目の項目\n- 2つ目の項目\n\n1. 手順\n\n| 列A | 列B |\n| --- | --- |\n| 値1 | 値2 |\n\n"
    "```python\nprint('コード')\n```\n\n`inline`のコード\n"
)


_MARKDOWN_REMOTE_HOST = "markdown-host"


_MARKDOWN_REMOTE_PATH = "/home/remote/.claude/projects/-home-remote-md/44444444-5555-6666-7777-888888888888.jsonl"


def _session_lines(*records: dict[str, Any]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _assistant_record(text: str) -> dict[str, Any]:
    return {"type": "assistant", "timestamp": "2026-09-30T00:00:01Z", "message": {"content": [{"type": "text", "text": text}]}}


def _write_local_session(tmp_path: Path, name: str, *records: dict[str, Any]) -> str:
    """ローカルのClaude Codeの記録1件を、開始日時が既存の記録より新しい形で作成する。"""
    path = tmp_path / "claude" / "projects" / "-home-aki-md" / f"{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    first = {"type": "user", "timestamp": "2026-09-30T00:00:00Z", "cwd": "/home/aki/md", "message": {"content": "整形の確認"}}
    path.write_text(_session_lines(first, *records), encoding="utf-8")
    return str(path)


async def _markdown_remote_runner(host: str, op: str, _args: list[str]) -> str:
    """整形の対象となるアシスタントの発言を含む記録を返す、リモートヘルパーの偽の応答。"""
    if op == "list":
        entry = {
            "engine": "claude",
            "cwd": "/home/remote/md",
            "first_user_message": "リモートの整形",
            "session_id": "remote-md",
            "path": _MARKDOWN_REMOTE_PATH,
            "updated_at": 1_800_000_000,
            "size": 200,
        }
        return json.dumps({"ok": True, "host": host, "entries": [entry]}, ensure_ascii=False)
    text = _session_lines(
        {"type": "user", "timestamp": "2026-09-01T00:00:00Z", "message": {"content": "リモートの整形"}},
        _assistant_record(_SESSION_MARKDOWN),
    )
    return json.dumps({"ok": True, "data": base64.b64encode(text.encode("utf-8")).decode("ascii"), "subagents": []})


@contextlib.asynccontextmanager
async def _markdown_sessions_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, browser: playwright.async_api.Browser
) -> AsyncGenerator[tuple[playwright.async_api.Page, str]]:
    """ローカルの記録と、偽の応答を返すリモートホストを登録したセッション画面を開く。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    monkeypatch.setattr(serve_sessions, "start_remote_clients", lambda context: None)
    app = _browser_app(tmp_path, plans_root, remote_hosts=[_MARKDOWN_REMOTE_HOST], ssh_runner=_markdown_remote_runner)
    async with _serve(app, browser) as (_context, page, port):
        yield page, f"http://127.0.0.1:{port}"


async def _open_session(page: playwright.async_api.Page, base_url: str, host: str, path: str) -> None:
    await page.goto(base_url + "/sessions")
    await page.locator(f'#sessions .session-item[data-host="{host}"][data-path="{path}"]').click()
    await page.locator("#detail .event.kind-assistant").first.wait_for(state="visible")


def _append_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    """JSON Linesの記録へ行を追記する。記録が無ければ作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.writelines(json.dumps(record, ensure_ascii=False) + "\n" for record in records)


def _session_list_requests(harness: _ScreenHarness) -> int:
    return sum(1 for url in harness.requests if "/api/sessions/list" in url)


_SCROLL_DETAIL_TO_END_JS = (
    "() => { const main = document.querySelector('#screen-sessions main'); main.scrollTop = main.scrollHeight; }"
)


_DETAIL_DISTANCE_FROM_END_JS = (
    "() => { const main = document.querySelector('#screen-sessions main');"
    " return main.scrollHeight - main.scrollTop - main.clientHeight; }"
)


async def _wait_record_notification(harness: _ScreenHarness, queue: asyncio.Queue[str], path: Path) -> None:
    try:
        async with asyncio.timeout(5):
            while True:
                event = json.loads(await queue.get())
                if event.get("type") == "record" and event.get("path") == str(path):
                    return
    finally:
        await serve_sessions.unsubscribe(harness.sessions_context.state, queue)
