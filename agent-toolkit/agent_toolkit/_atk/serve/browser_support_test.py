"""`atk serve`の実ブラウザー統合テストが共有する擬似操作、サーバーの起動、fixtureと補助。"""

import asyncio
import contextlib
import dataclasses
import json
import os
import socket
import threading
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.wi import user_comment as user_comment_mutations
from agent_toolkit._atk.wi import uwi as uwi_mutations

_BROWSER_TEST_ENV = "AGENT_TOOLKIT_SERVE_BROWSER_TESTS"
_SERVER_START_TIMEOUT_SEC = 10.0
_LONG_UNKNOWN_FRONTMATTER_KEY = "unknown_" + "x" * (500 - len("unknown_"))


async def _hold_route(
    route: playwright.async_api.Route,
    *,
    started: asyncio.Event,
    release: asyncio.Event,
) -> None:
    started.set()
    await release.wait()
    await route.continue_()


def _browser_tests_enabled() -> bool:
    value = os.environ.get(_BROWSER_TEST_ENV, "")
    return value.lower() in {"1", "true", "yes", "on"}


class _BrowserOperations(serve_app.Operations):
    """Gitを使わず、更新処理の待機を同期イベントで制御する。"""

    def __init__(self, private_notes: Path) -> None:
        super().__init__(private_notes)
        self.delay_edit = False
        self.edit_started = threading.Event()
        self.edit_release = threading.Event()
        self.edit_release.set()
        self.edit_calls = 0
        self.answer_calls = 0
        self.delay_remove = False
        self.remove_started = threading.Event()
        self.remove_release = threading.Event()
        self.remove_release.set()
        self.remove_calls = 0
        self.last_transition: tuple[str, list[str], str | None] | None = None
        self.persist_mutations = False
        self.add_calls: list[dict[str, Any]] = []
        self.batch_calls: list[str] = []
        self.delay_user_comment = False
        self.user_comment_started = threading.Event()
        self.user_comment_release = threading.Event()
        self.user_comment_release.set()
        self.user_comment_calls = 0

    def sync(self) -> bool:
        """テストでは外部Git操作を行わない。"""
        return True

    def user_comment(self, state: str, filename: str, comment: str, expected_content: str) -> bool:
        """Gitを使わず、ユーザーコメントの本文が期待値に一致するかを確かめて保存する。"""
        self.user_comment_calls += 1
        if self.delay_user_comment:
            self.user_comment_started.set()
            if not self.user_comment_release.wait(timeout=5):
                raise TimeoutError("ユーザーコメント保存の解放を待機できませんでした")
            self.delay_user_comment = False
        path = self.private_notes / state / filename
        current = path.read_text(encoding="utf-8")
        if current != expected_content:
            raise RuntimeError("編集中に他プロセスが対象を変更しました")
        updated = user_comment_mutations.update_user_comment(current, comment)
        path.write_text(updated, encoding="utf-8")
        return True

    def background_sync(self) -> bool:
        return False

    def edit(
        self,
        state: str,
        filename: str,
        content: str,
        expected_content: str | None = None,
    ) -> bool:
        self.edit_calls += 1
        if self.persist_mutations:
            path = self.private_notes / state / filename
            if expected_content is not None and path.read_text(encoding="utf-8") != expected_content:
                raise RuntimeError("編集中に他プロセスが対象を変更しました")
            path.write_text(content, encoding="utf-8")
        if self.delay_edit:
            self.edit_started.set()
            if not self.edit_release.wait(timeout=5):
                raise TimeoutError("編集処理の解放を待機できませんでした")
            self.delay_edit = False
        return True

    def transition(
        self,
        action: str,
        filenames: list[str],
        **kwargs: Any,
    ) -> list[str]:
        self.last_transition = (action, filenames, kwargs.get("note"))
        if action in {"hold", "unhold", "return-to-inbox"} and self.persist_mutations:
            source = kwargs.get("state") or (
                "hold" if action == "unhold" else "processing" if action == "return-to-inbox" else "inbox"
            )
            destination = "hold" if action == "hold" else "inbox"
            (self.private_notes / destination).mkdir(exist_ok=True)
            for filename in filenames:
                (self.private_notes / source / filename).rename(self.private_notes / destination / filename)
        if action in {"adopt", "reject"} and self.persist_mutations:
            source = kwargs.get("state") or "inbox"
            destination = "adopted" if action == "adopt" else "rejected"
            for filename in filenames:
                (self.private_notes / source / filename).rename(self.private_notes / destination / filename)
        if action == "remove":
            self.remove_calls += 1
            if self.persist_mutations:
                state = kwargs.get("state")
                expected_content = kwargs.get("expected_content")
                force = kwargs.get("force", False)
                for filename in filenames:
                    state_names = (state,) if state is not None else ("processing", "inbox")
                    for state_name in state_names:
                        path = self.private_notes / state_name / filename
                        if not path.exists():
                            continue
                        if state_name == "processing" and not force:
                            raise serve_app.WebApiInputError("指定したエントリを操作できません")
                        if expected_content is not None:
                            try:
                                current_content = path.read_text(encoding="utf-8")
                            except (OSError, UnicodeError) as error:
                                raise RuntimeError("編集中に他プロセスが対象を変更しました") from error
                            if current_content != expected_content:
                                raise RuntimeError("編集中に他プロセスが対象を変更しました")
                        path.unlink()
                        break
            if self.delay_remove:
                self.remove_started.set()
                if not self.remove_release.wait(timeout=5):
                    raise TimeoutError("削除処理の解放を待機できませんでした")
                self.delay_remove = False
        return filenames

    def add(
        self,
        messages: list[str],
        *,
        entry_type: str,
        target_repo: str | None,
        scope: str | None = None,
        question_type: str | None = None,
        choices: list[str] | None = None,
    ) -> list[str]:
        """Gitを使わず、対象リポジトリの必須検証と一時リポジトリへの書込みだけを行う。"""
        del scope, question_type, choices
        self.add_calls.append({"messages": messages, "target_repo": target_repo})
        filenames: list[str] = []
        for index, message in enumerate(messages):
            parsed = serve_app.frontmatter.parse_frontmatter(message)
            metadata, body = parsed if parsed is not None else ({}, message)
            repo = target_repo if target_repo is not None else metadata.get("target_repo")
            if not isinstance(repo, str) or not repo:
                raise serve_app.common.WebInputError(
                    "target_repoを指定するか各メッセージのfrontmatterへ記載してください",
                    next_action="--target-repoを指定して再実行する",
                )
            filename = f"created-{len(self.add_calls)}-{index}.md"
            (self.private_notes / "inbox" / filename).write_text(
                f"---\ntarget_repo: {repo}\ntype: {entry_type}\n---\n\n{body.strip()}\n",
                encoding="utf-8",
            )
            filenames.append(filename)
        return filenames

    def add_batch(self, text: str) -> dict[str, object]:
        """Gitを使わず、実装と同じ解析結果を一時リポジトリへ原文保持で書き込む。"""
        self.batch_calls.append(text)
        entries = serve_app.awi_batch.parse_show_batch(text)
        for entry in entries:
            (self.private_notes / "inbox" / entry.original_name).write_text(entry.raw_text, encoding="utf-8")
        return {
            "filenames": [entry.original_name for entry in entries],
            "mapping": {entry.original_name: entry.original_name for entry in entries},
            "warnings": [],
        }

    def answer_uwi(
        self,
        filename: str,
        answer: str,
        expected_content: str | None = None,
        state: str | None = None,
    ) -> bool:
        self.answer_calls += 1
        if self.persist_mutations:
            marker = "<!-- ユーザーはこの行以降に回答を追記する -->"
            state_names = (state,) if state is not None else ("processing", "inbox")
            for state_name in state_names:
                path = self.private_notes / state_name / filename
                if not path.exists():
                    continue
                content = path.read_text(encoding="utf-8")
                if expected_content is not None and content != expected_content:
                    raise RuntimeError("編集中に他プロセスが対象を変更しました")
                updated = content.rsplit(marker, maxsplit=1)[0] + marker + "\n" + answer.strip() + "\n"
                path.write_text(updated, encoding="utf-8")
                # 実装（`uwi.answer_uwi`）と同じ判定で、事後承認型UWIへの肯定回答は項目を`adopted`へ移す。
                if uwi_mutations._is_affirmative_post_approval(content, answer):  # pylint: disable=protected-access
                    path.rename(self.private_notes / "adopted" / filename)
                break
        return True

    def arm_edit_delay(self) -> None:
        self.delay_edit = True
        self.edit_started.clear()
        self.edit_release.clear()

    def arm_remove_delay(self) -> None:
        self.delay_remove = True
        self.remove_started.clear()
        self.remove_release.clear()

    def arm_user_comment_delay(self) -> None:
        self.delay_user_comment = True
        self.user_comment_started.clear()
        self.user_comment_release.clear()

    def enable_file_mutations(self) -> None:
        """回答・削除で一時リポジトリの実ファイルを更新する。"""
        self.persist_mutations = True


@dataclasses.dataclass
class _BrowserHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    root: Path
    current_state: serve_state.ServeState
    operations: _BrowserOperations
    base_url: str


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        return int(server_socket.getsockname()[1])


async def _wait_for_server(port: int) -> None:
    deadline = asyncio.get_running_loop().time() + _SERVER_START_TIMEOUT_SEC
    while True:
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as error:
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("テスト用サーバーが起動しませんでした") from error
            await asyncio.sleep(0.01)
            continue
        writer.close()
        await writer.wait_closed()
        del reader
        return


def _write_entries(root: Path) -> None:
    inbox = root / "inbox"
    inbox.mkdir(parents=True)
    adopted = root / "adopted"
    adopted.mkdir()
    rejected = root / "rejected"
    rejected.mkdir()
    long_body = "\n\n".join(f"段落{i} `inline-{i}`" for i in range(80))
    (inbox / "question.md").write_text(
        "---\ntype: uwi\ntarget_repo: example/repo\nsource: session-review\npriority: high\n"
        f"z_key: z\na_key: a\n{_LONG_UNKNOWN_FRONTMATTER_KEY}: long\n"
        "metadata:\n  branch: main\n  flags:\n    - one\nquestion_type: choice\nchoices: A, B\n---\n\n"
        f"## 質問\n\n{long_body}\n\n```text\n折り返す長いコード {'x' * 240}\n```\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "awi.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: alert-monitor\nplan_file: /tmp/plan.md\n---\n\n編集対象の本文\n",
        encoding="utf-8",
    )
    (inbox / "unknown.md").write_text("種別を判定できない本文\n", encoding="utf-8")
    (inbox / "empty.md").write_text(
        "---\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    (adopted / "adopted.md").write_text(
        "---\ntype: awi\ntarget_repo: adopted/repo\nsource: browser\n---\n\n採用済みの本文\n",
        encoding="utf-8",
    )
    (rejected / "rejected.md").write_text(
        "---\ntype: awi\ntarget_repo: rejected/repo\nsource: browser\n---\n\n不採用の本文\n",
        encoding="utf-8",
    )
    for index in range(6):
        (rejected / f"many-terminal-{index}.md").write_text(
            "---\ntype: awi\ntarget_repo: rejected/repo\nsource: browser\n---\n\n多数終端検索\n",
            encoding="utf-8",
        )


@pytest_asyncio.fixture(scope="session", name="browser")
async def _browser_fixture() -> AsyncGenerator[playwright.async_api.Browser]:
    playwright_instance = await playwright.async_api.async_playwright().start()
    browser = None
    try:
        browser = await playwright_instance.chromium.launch()
        yield browser
    finally:
        if browser is not None:
            await browser.close()
        await playwright_instance.stop()


@pytest_asyncio.fixture(name="browser_harness")
async def _browser_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_BrowserHarness]:
    _write_entries(tmp_path)
    original_parse = serve_app.frontmatter.parse_frontmatter

    def parse_with_integer_key(text: str) -> tuple[dict[Any, Any], str] | None:
        parsed = original_parse(text)
        if parsed is None or "priority: high\n" not in text:
            return parsed
        metadata, body = parsed
        enriched: dict[Any, Any] = {}
        for key, value in metadata.items():
            enriched[key] = value
            if key == "z_key":
                enriched[1] = "numeric"
                enriched["1"] = "textual"
        return enriched, body

    monkeypatch.setattr(serve_app.frontmatter, "parse_frontmatter", parse_with_integer_key)
    current_state = serve_state.ServeState(tmp_path)
    operations = _BrowserOperations(tmp_path)
    app = serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state,
        operations=operations,
    )
    port = _reserve_port()
    shutdown = asyncio.Event()

    async def shutdown_trigger() -> None:
        await shutdown.wait()

    server_task = asyncio.create_task(app.run_task("127.0.0.1", port, shutdown_trigger=shutdown_trigger))
    context = None
    try:
        await _wait_for_server(port)
        context = await browser.new_context()
        page = await context.new_page()
        yield _BrowserHarness(
            page=page,
            context=context,
            root=tmp_path,
            current_state=current_state,
            operations=operations,
            base_url=f"http://127.0.0.1:{port}",
        )
    finally:
        if context is not None:
            await context.close()
        shutdown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


async def _open_question(page: playwright.async_api.Page) -> playwright.async_api.Locator:
    row = page.locator('#entry-list .entry-select[data-kind="uwi"]')
    await row.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    return row


async def _open_filters(page: playwright.async_api.Page) -> None:
    """WI一覧のフィルターが閉じている場合にエンドユーザーの操作で開く。"""
    details = page.locator(".filters details")
    if not await details.evaluate("element => element.open"):
        await details.locator("summary").click()


async def _shift_click_default_prevented(link: playwright.async_api.Locator) -> bool:
    """Shiftクリックを送り、アプリケーションの処理後、ブラウザーの標準動作が抑止された状態を返す。"""
    default_prevented = await link.evaluate(
        """element => {
          let defaultPrevented = null;
          element.addEventListener("click", event => {
            defaultPrevented = event.defaultPrevented;
            event.preventDefault();
          }, {once: true});
          element.dispatchEvent(new MouseEvent("click", {
            bubbles: true,
            cancelable: true,
            button: 0,
            shiftKey: true,
          }));
          return defaultPrevented;
        }"""
    )
    assert default_prevented is not None, "Shiftクリックの観測ハンドラーへ到達しなかった"
    return bool(default_prevented)


# --------------------------------------------------------------------------------------
# 計画ファイル画面とセッション画面
# --------------------------------------------------------------------------------------


def _valid_diagram_markdown(label: str) -> str:
    """MermaidとSVGの両方を含む計画本文を組み立てる。"""
    return (
        f"# {label}\n\n"
        f"本文-{label}\n\n"
        "```mermaid\n"
        f'graph TD\n  A["{label}"] --> B["完了"]\n'
        "```\n\n"
        "```svg\n"
        '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="40">'
        '<rect width="120" height="40" fill="#4f46e5"/>'
        f'<text x="8" y="25" fill="white">{label}</text>'
        "</svg>\n"
        "```\n"
    )


@dataclasses.dataclass
class _ScreenHarness:
    page: playwright.async_api.Page
    context: playwright.async_api.BrowserContext
    root: Path
    plan_path: Path
    plans_state: serve_plans.BroadcastState
    base_url: str
    requests: list[str]
    responses: list[tuple[str, int]]

    async def notify_file_update(self, markdown: str) -> None:
        """計画ファイルを書き換え、更新通知を配信する。"""
        for _ in range(100):
            if self.plans_state.subscribers:
                break
            await asyncio.sleep(0.01)
        assert self.plans_state.subscribers
        if self.plans_state.debounce_task is not None:
            await self.plans_state.debounce_task
        previous_mtime_ns = self.plan_path.stat().st_mtime_ns
        self.plan_path.write_text(markdown, encoding="utf-8")
        updated = self.plan_path.stat()
        os.utime(self.plan_path, ns=(updated.st_atime_ns, max(updated.st_mtime_ns, previous_mtime_ns + 1_000_000_000)))
        await serve_plans.schedule_broadcast(self.plans_state)
        if self.plans_state.debounce_task is not None:
            await self.plans_state.debounce_task


def _isolate_creation_time_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """作成日時インデックスを一時ディレクトリへ隔離し、開発環境の索引を書き換えない。"""
    monkeypatch.setattr(serve_plans, "_CREATION_TIME_INDEX_PATH", tmp_path / "cache" / "index.json")


@contextlib.asynccontextmanager
async def _serve(
    app: Any,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[tuple[playwright.async_api.BrowserContext, playwright.async_api.Page, int]]:
    """テスト用サーバーを起動し、ページとブラウザーコンテキストを提供する。"""
    port = _reserve_port()
    shutdown = asyncio.Event()

    async def shutdown_trigger() -> None:
        await shutdown.wait()

    server_task = asyncio.create_task(app.run_task("127.0.0.1", port, shutdown_trigger=shutdown_trigger))
    context = None
    try:
        await _wait_for_server(port)
        context = await browser.new_context()
        await context.grant_permissions(["clipboard-read", "clipboard-write"], origin=f"http://127.0.0.1:{port}")
        page = await context.new_page()
        yield context, page, port
    finally:
        if context is not None:
            await context.close()
        shutdown.set()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task


def _browser_app(tmp_path: Path, plans_root: Path, **sessions_options: Any) -> Any:
    """3画面を登録したテスト用アプリを生成する。`sessions_options`はセッション画面のコンテキストへ渡す。"""
    return serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        serve_state.ServeState(tmp_path),
        operations=_BrowserOperations(tmp_path),
        plans_context=serve_plans.create_context(root=plans_root, hostname="browser-test"),
        sessions_context=serve_sessions.create_context(
            hostname="browser-test",
            claude_home=tmp_path / "claude",
            codex_home=tmp_path / "codex",
            **sessions_options,
        ),
    )


@pytest_asyncio.fixture(name="screen_harness")
async def _screen_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_ScreenHarness]:
    """3画面を登録したアプリと、計画ファイル1件・両実行系のセッション記録を用意する。"""
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    plan_path = plans_root / "plan.md"
    plan_path.write_text(_valid_diagram_markdown("初回"), encoding="utf-8")
    _write_session_records(tmp_path)
    app = _browser_app(tmp_path, plans_root)
    plans_state: serve_plans.BroadcastState = app.config["PLANS_CONTEXT"].state
    async with _serve(app, browser) as (context, page, port):
        requests: list[str] = []
        responses: list[tuple[str, int]] = []
        page.on("request", lambda request: requests.append(request.url))
        page.on("response", lambda response: responses.append((response.url, response.status)))
        yield _ScreenHarness(
            page=page,
            context=context,
            root=plans_root,
            plan_path=plan_path,
            plans_state=plans_state,
            base_url=f"http://127.0.0.1:{port}",
            requests=requests,
            responses=responses,
        )


def _write_session_records(root: Path) -> None:
    """Claude CodeとCodexのセッション記録を1件ずつ作成する。"""
    claude_path = root / "claude" / "projects" / "-home-aki-proj" / "11111111-2222-3333-4444-555555555555.jsonl"
    claude_path.parent.mkdir(parents=True, exist_ok=True)
    claude_path.write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-09-01T00:00:00Z",
                "cwd": "/home/aki/proj",
                "message": {"content": "Claudeの発話"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:01Z",
                "message": {
                    "usage": {"input_tokens": 12, "output_tokens": 3},
                    "content": [
                        {"type": "thinking", "thinking": "Claudeの思考"},
                        {"type": "tool_use", "name": "Bash", "input": {"command": f"ls {'x' * 160}\npwd"}},
                        {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/input.md"}},
                        {"type": "tool_use", "name": "Search", "input": {"pattern": "needle", "count": 1}},
                    ],
                },
            },
            ensure_ascii=False,
        )
        + "\n"
        # 書き込み途中の行が混在しても他の行を失わせないことを画面側でも確認する。
        + '{"type":"user","message":{"content":"途中で途絶\n',
        encoding="utf-8",
    )
    # 記録本体を持つサブエージェントと、meta情報だけが残るより深いサブエージェントを1件ずつ用意する。
    subagents = claude_path.with_suffix("") / "subagents"
    subagents.mkdir(parents=True, exist_ok=True)
    (subagents / "agent-first.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "所在の調査", "spawnDepth": 1}, ensure_ascii=False),
        encoding="utf-8",
    )
    (subagents / "agent-first.jsonl").write_text(
        json.dumps(
            {"type": "user", "timestamp": "2026-09-01T00:10:00Z", "message": {"content": "サブエージェントの発話"}},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    (subagents / "agent-second.meta.json").write_text(
        json.dumps({"agentType": "Plan", "description": "設計の検討", "spawnDepth": 2}, ensure_ascii=False),
        encoding="utf-8",
    )
    codex_path = (
        root
        / "codex"
        / "sessions"
        / "2026"
        / "09"
        / "01"
        / "rollout-2026-09-01T00-00-00-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.jsonl"
    )
    codex_path.parent.mkdir(parents=True, exist_ok=True)
    codex_path.write_text(
        json.dumps(
            {
                "type": "session_meta",
                "timestamp": "2026-09-01T01:00:00Z",
                "payload": {"cwd": "/home/aki/other", "timestamp": "2026-09-01T01:00:00Z"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:01Z",
                "payload": {"type": "message", "role": "user", "content": [{"text": "Codexの発話"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:02Z",
                "payload": {"type": "message", "role": "developer", "content": [{"text": "Codexの開発者指示"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:03Z",
                "payload": {"type": "message", "role": "assistant", "content": [{"text": "Codexの応答"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:04Z",
                "payload": {"type": "reasoning", "summary": [{"text": "Codexの思考"}]},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:05Z",
                "payload": {"type": "function_call_output", "output": "Codexのツール結果"},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "compacted",
                "timestamp": "2026-09-01T01:00:06Z",
                "payload": {"message": "コンテキストを圧縮しました", "window_number": 2},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:07Z",
                "payload": {"type": "function_call", "name": "shell", "arguments": '{"cmd":"pwd"}'},
            },
            ensure_ascii=False,
        )
        + "\n"
        + json.dumps(
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:08Z",
                "payload": {"type": "function_call", "name": "broken", "arguments": "{invalid-json"},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


__all__ = [
    "_BROWSER_TEST_ENV",
    "_SERVER_START_TIMEOUT_SEC",
    "_LONG_UNKNOWN_FRONTMATTER_KEY",
    "_hold_route",
    "_browser_tests_enabled",
    "_BrowserOperations",
    "_BrowserHarness",
    "_reserve_port",
    "_wait_for_server",
    "_write_entries",
    "_browser_fixture",
    "_browser_harness_fixture",
    "_open_question",
    "_open_filters",
    "_shift_click_default_prevented",
    "_valid_diagram_markdown",
    "_ScreenHarness",
    "_isolate_creation_time_index",
    "_serve",
    "_browser_app",
    "_screen_harness_fixture",
    "_write_session_records",
]
