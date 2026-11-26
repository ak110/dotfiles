import argparse
import asyncio
import contextlib
import dataclasses
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from collections.abc import AsyncGenerator, Generator, Iterable
from pathlib import Path
from typing import Any, cast

import playwright.async_api
import pytest
import pytest_asyncio

from agent_toolkit._atk import config as atk_config
from agent_toolkit._atk import config_migration, config_settings
from agent_toolkit._atk.serve import app as serve_app
from agent_toolkit._atk.serve import config, entry_index, timings, wi_operations
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.wi import frontmatter as wi_frontmatter
from agent_toolkit._atk.wi import sync as wi_sync
from agent_toolkit._common import host_homes, state_paths
from agent_toolkit._common import private_notes as private_notes_store
from agent_toolkit._plan import creation_times as plan_creation_times
from agent_toolkit._testing import isolation, wi_entry_files
from agent_toolkit._testing.pytest_plugin import _BROWSER_EVIDENCE

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


async def _observe_screen_requests(page: playwright.async_api.Page) -> None:
    await page.add_init_script(
        """(() => {
          // completedByUrlは、同じパスへ並行する別条件の取得と区別して特定の要求の本文消費を待つ試験が使う。
          const probe = window.__screenRequests = {
            pending: 0, completed: {}, completedByUrl: {}, opened: {}, subscribed: {}, heartbeats: {}
          };
          const observed = new Set(['/api/entries', '/api/repos', '/api/sessions/list', '/api/plans/files']);
          const originalFetch = window.fetch.bind(window);
          window.fetch = async (...args) => {
            const requestUrl = new URL(args[0]?.url || args[0], location.href);
            const path = requestUrl.pathname;
            if (!observed.has(path)) return originalFetch(...args);
            probe.pending++;
            let finished = false;
            const finish = () => { if (!finished) { finished = true; probe.pending--; } };
            try {
              const response = await originalFetch(...args);
              for (const method of ['json', 'text']) {
                const consume = response[method].bind(response);
                response[method] = async (...params) => {
                  try {
                    const value = await consume(...params);
                    probe.completed[path] = (probe.completed[path] || 0) + 1;
                    probe.completedByUrl[requestUrl.href] = (probe.completedByUrl[requestUrl.href] || 0) + 1;
                    return value;
                  } finally { finish(); }
                };
              }
              return response;
            } catch (error) { finish(); throw error; }
          };
          const NativeEventSource = window.EventSource;
          window.EventSource = class extends NativeEventSource {
            constructor(url, options) {
              super(url, options);
              const path = new URL(url, location.href).pathname;
              this.addEventListener('open', () => { probe.opened[path] = (probe.opened[path] || 0) + 1; });
              this.addEventListener('subscribed', () => { probe.subscribed[path] = (probe.subscribed[path] || 0) + 1; });
              this.addEventListener('heartbeat', () => {
                probe.heartbeats[path] = (probe.heartbeats[path] || 0) + 1;
              });
            }
          };
        })();"""
    )


async def _wait_screen_requests_consumed(page: playwright.async_api.Page, streams: tuple[str, ...]) -> None:
    await page.wait_for_function(
        """paths => {
          const probe = window.__screenRequests;
          // 計画・セッションはヘッダーのopen後に購読を登録する。WIの独立したSSE経路はopenで同期する。
          return probe && paths.every(path => (path === '/api/events' ? probe.opened : probe.subscribed)[path] > 0)
            && probe.pending === 0;
        }""",
        arg=list(streams),
    )


class _BrowserMutation(wi_sync.Mutation):
    def commit(self, message: str, rel_paths: Iterable[str], *, exclusive_claim: bool = False) -> int | None:
        del message, rel_paths, exclusive_claim
        return 0


@dataclasses.dataclass(frozen=True)
class _BrowserStorage(wi_sync.Storage):
    """外部Gitとプロセス間ロックを使わず、試験内の更新を直列化する。"""

    lock: Any = dataclasses.field(default_factory=threading.RLock)

    @contextlib.contextmanager
    def mutation(
        self, private_notes: Path, *, push_pending: bool = False, skip_push: bool = False
    ) -> Generator[wi_sync.Mutation]:
        del push_pending, skip_push
        with self.lock:
            yield _BrowserMutation(private_notes, skip_push=True)


class _BrowserOperations(wi_operations.Operations):
    """製品の更新処理を通し、画面の待機制御と呼出し記録を付加する。"""

    def __init__(self, private_notes: Path) -> None:
        super().__init__(private_notes, index_cache_path=None)
        self._storage = _BrowserStorage(local_only=True)
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
        self.add_calls: list[dict[str, Any]] = []
        self.batch_calls: list[str] = []
        self.delay_user_comment = False
        self.user_comment_started = threading.Event()
        self.user_comment_release = threading.Event()
        self.user_comment_release.set()
        self.user_comment_calls = 0

    def sync(self) -> bool:
        return True

    def background_sync(self) -> bool:
        return False

    def user_comment(self, state: str, filename: str, comment: str, expected_content: str) -> bool:
        self.user_comment_calls += 1
        if self.delay_user_comment:
            self.user_comment_started.set()
            if not self.user_comment_release.wait(timeout=5):
                raise TimeoutError("ユーザーコメント保存の解放を待機できませんでした")
            self.delay_user_comment = False
        return super().user_comment(state, filename, comment, expected_content)

    def edit(self, state: str, filename: str, content: str, expected_content: str | None = None) -> bool:
        self.edit_calls += 1
        result = super().edit(state, filename, content, expected_content)
        if self.delay_edit:
            self.edit_started.set()
            if not self.edit_release.wait(timeout=5):
                raise TimeoutError("編集処理の解放を待機できませんでした")
            self.delay_edit = False
        return result

    def transition(self, action: str, filenames: list[str], **kwargs: Any) -> list[str]:
        self.last_transition = (action, filenames, kwargs.get("note"))
        result = super().transition(action, filenames, **kwargs)
        if action == "remove":
            self.remove_calls += 1
            if self.delay_remove:
                self.remove_started.set()
                if not self.remove_release.wait(timeout=5):
                    raise TimeoutError("削除処理の解放を待機できませんでした")
                self.delay_remove = False
        return result

    def add(
        self,
        messages: list[str],
        *,
        entry_type: str,
        target_repo: str | None,
        scope: str | None = None,
        question_type: str | None = None,
        choices: list[str] | None = None,
        target_branch: str | None = None,
    ) -> list[str]:
        self.add_calls.append({"messages": messages, "target_repo": target_repo})
        return super().add(
            messages,
            entry_type=entry_type,
            target_repo=target_repo,
            scope=scope,
            question_type=question_type,
            choices=choices,
            target_branch=target_branch,
        )

    def add_batch(self, text: str) -> dict[str, object]:
        self.batch_calls.append(text)
        return super().add_batch(text)

    def answer_uwi(self, filename: str, answer: str, expected_content: str | None = None, state: str | None = None) -> bool:
        self.answer_calls += 1
        return super().answer_uwi(filename, answer, expected_content, state)

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
    inbox = wi_entry_files.entry_path(root, "question.md").parent
    adopted = rejected = inbox
    long_body = "\n\n".join(f"段落{i} `inline-{i}`" for i in range(80))
    (inbox / "question.md").write_text(
        "---\nstate: inbox\ntype: uwi\ntarget_repo: example/repo\nsource: session-review\npriority: high\n"
        f"z_key: z\na_key: a\n{_LONG_UNKNOWN_FRONTMATTER_KEY}: long\n"
        "metadata:\n  branch: main\n  flags:\n    - one\nquestion_type: choice\nchoices: A, B\n---\n\n"
        f"## 質問\n\n{long_body}\n\n```text\n折り返す長いコード {'x' * 240}\n```\n\n"
        "## 回答\n\n<!-- ユーザーはこの行以降に回答を追記する -->\n",
        encoding="utf-8",
    )
    (inbox / "awi.md").write_text(
        "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\nsource: alert-monitor\n"
        "plan_file: /tmp/plan.md\n---\n\n編集対象の本文\n",
        encoding="utf-8",
    )
    (inbox / "unknown.md").write_text("---\nstate: inbox\n---\n\n種別を判定できない本文\n", encoding="utf-8")
    (inbox / "empty.md").write_text(
        "---\nstate: inbox\ntype: awi\ntarget_repo: example/repo\nsource: browser\n---\n",
        encoding="utf-8",
    )
    (inbox / "invalid.md").write_bytes(b"\xff")
    (adopted / "adopted.md").write_text(
        "---\nstate: adopted\ntype: awi\ntarget_repo: adopted/repo\nsource: browser\n---\n\n採用済みの本文\n",
        encoding="utf-8",
    )
    (rejected / "rejected.md").write_text(
        "---\nstate: rejected\ntype: awi\ntarget_repo: rejected/repo\nsource: browser\n---\n\n不採用の本文\n",
        encoding="utf-8",
    )
    for index in range(6):
        (rejected / f"many-terminal-{index}.md").write_text(
            "---\nstate: rejected\ntype: awi\ntarget_repo: rejected/repo\nsource: browser\n---\n\n多数終了検索\n",
            encoding="utf-8",
        )


@pytest_asyncio.fixture(loop_scope="session", name="browser")
async def _browser_fixture(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    shared_serve_browser: playwright.async_api.Browser,
    browser_evidence_root: Path,
) -> AsyncGenerator[playwright.async_api.Browser]:
    _isolate_creation_time_index(tmp_path, monkeypatch)
    evidence = Path(tempfile.mkdtemp(prefix="browser-failure-", dir=browser_evidence_root))
    request.node.stash[_BROWSER_EVIDENCE] = evidence
    browser = shared_serve_browser
    create_context = browser.new_context
    contexts: list[playwright.async_api.BrowserContext] = []

    async def traced_context(**kwargs: Any) -> playwright.async_api.BrowserContext:
        context = await create_context(**kwargs)
        contexts.append(context)
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        close = context.close
        index = len(contexts)

        async def close_with_evidence(**options: Any) -> None:
            try:
                for number, page in enumerate(context.pages):
                    with contextlib.suppress(Exception):
                        await page.screenshot(path=evidence / f"{index}-{number}.png")
                await context.tracing.stop(path=evidence / f"{index}-trace.zip")
            except Exception as error:
                request.node.add_report_section("teardown", "ブラウザー証拠保存", str(error))
            finally:
                await close(**options)

        monkeypatch.setattr(context, "close", close_with_evidence)
        return context

    monkeypatch.setattr(browser, "new_context", traced_context)
    try:
        yield browser
    finally:
        for context in list(browser.contexts):
            await context.close()


@pytest_asyncio.fixture(loop_scope="session", name="browser_harness")
async def _browser_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[_BrowserHarness]:
    _write_entries(tmp_path)
    original_parse = wi_frontmatter.parse_frontmatter

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

    monkeypatch.setattr(wi_frontmatter, "parse_frontmatter", parse_with_integer_key)
    current_state = serve_state.ServeState(tmp_path)
    operations = _BrowserOperations(tmp_path)
    app = _browser_app(tmp_path, tmp_path / "plans", current_state=current_state, operations=operations)
    async with _serve(app, browser) as (context, page, port):
        yield _BrowserHarness(
            page=page,
            context=context,
            root=tmp_path,
            current_state=current_state,
            operations=operations,
            base_url=f"http://127.0.0.1:{port}",
        )


async def _open_question(page: playwright.async_api.Page) -> playwright.async_api.Locator:
    row = page.locator('#entry-list .entry-select[data-kind="uwi"]')
    await row.click()
    await page.get_by_role("dialog", name="詳細").wait_for(state="visible")
    return row


async def _open_filters(page: playwright.async_api.Page) -> None:
    details = page.locator(".filters details")
    if not await details.evaluate("element => element.open"):
        await details.locator("summary").click()


async def _shift_click_default_prevented(link: playwright.async_api.Locator) -> bool:
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


def _valid_diagram_markdown(label: str) -> str:
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
    sessions_context: serve_sessions.SessionsContext

    async def notify_file_update(self, markdown: str) -> None:
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
    monkeypatch.setattr(plan_creation_times, "_CREATION_TIME_INDEX_PATH", tmp_path / "cache" / "index.json")


@contextlib.asynccontextmanager
async def _serve(
    app: Any,
    browser: playwright.async_api.Browser,
) -> AsyncGenerator[tuple[playwright.async_api.BrowserContext, playwright.async_api.Page, int]]:
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
        try:
            if context is not None:
                await context.close()
        finally:
            shutdown.set()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task


def _require_browser_paths(root: Path, paths: dict[str, Path]) -> None:
    for label, path in paths.items():
        if not path.is_absolute() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"ブラウザー検証の一時領域外: {label} ({path})")


def _manual_cli_inputs(root: Path, port: int) -> tuple[Path, config.ServeConfig]:
    home = Path.home()
    state = state_paths.state_dir()
    notes = private_notes_store.default_private_notes(home)
    claude = host_homes.claude_config_dir()
    codex = host_homes.codex_home()
    _require_browser_paths(
        root,
        {
            "home": home,
            "設定": config_settings.config_file_path(),
            "旧設定": config_migration.legacy_serve_config_path(),
            "状態": state,
            "WI": notes,
            "WI設定": notes / ".git" / "config",
            "WI索引": entry_index.default_cache_path(),
            "計測": timings.default_log_path(),
            "計画作成時刻の索引": plan_creation_times._CREATION_TIME_INDEX_PATH,  # noqa: SLF001  # pylint: disable=protected-access
            "Claude Code": claude / "projects",
            "Codex": codex / "sessions",
            "古いCodex記録": codex / "archived_sessions",
            **{
                name: state / "serve" / name
                for name in ("session-summaries.json", "session-list.json", "session-costs.json", "cost-records")
            },
        },
    )
    if not (notes / ".git").is_dir():
        raise ValueError("ブラウザー検証のWIは独立したローカルGitリポジトリにする")
    remotes = subprocess.run(["git", "-C", str(notes), "remote"], check=True, capture_output=True, timeout=10)  # noqa: S607
    if remotes.stdout.strip():
        raise ValueError("ブラウザー検証のWIにはリモートを設定しない")
    serve = config.resolve_config(host="127.0.0.1", port=port)
    if serve.plans.remote_hosts or serve.sessions.remote_hosts:
        raise ValueError("ブラウザー検証の計画・会話にはリモートを設定しない")
    if atk_config.resolve_mutable_setting("serve_web_push") == "true":
        raise ValueError("ブラウザーの手動検証ではWeb Pushを無効にする")
    roots = (
        {"計画": Path(serve.plans.root).expanduser()}
        if serve.plans.root
        else {"WI内の計画": notes / "plans", "作業中の計画": claude / "plans"}
    )
    _require_browser_paths(root, roots)
    return notes, serve


_BROWSER_SUPPORT_MODULE = "agent_toolkit._atk.serve.browser_support_test"


def _manual_browser_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="隔離した子プロセスでブラウザーの手動確認を起動する。")
    parser.add_argument("mode", choices=("cli", "fixture"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--port", type=int, default=28766)
    parser.add_argument("--isolated-child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if not args.isolated_child:
        if not root.is_dir() or any(root.iterdir()):
            parser.error("別に作成した空の一時ディレクトリを指定する")
        with isolation.isolated_development_state(root):
            environment = {**os.environ, "CLAUDE_CONFIG_DIR": str(root / "claude"), "CODEX_HOME": str(root / "codex")}
            with subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    _BROWSER_SUPPORT_MODULE,
                    args.mode,
                    str(root),
                    "--port",
                    str(args.port),
                    "--isolated-child",
                ],
                env=environment,
            ) as child:
                try:
                    return child.wait()
                except KeyboardInterrupt:
                    try:
                        return child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.terminate()
                        return child.wait(timeout=10)

    if args.mode == "cli":
        from agent_toolkit import atk  # noqa: PLC0415  # pylint: disable=import-outside-toplevel
        from agent_toolkit._atk.serve import cli  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

        notes = root / "private-notes"
        _require_browser_paths(root, {"home": Path.home(), "WI": notes})
        if not notes.exists():
            notes.mkdir()
            subprocess.run(["git", "-C", str(notes), "init", "--quiet"], timeout=10, check=True)  # noqa: S607
        notes, serve = _manual_cli_inputs(root, args.port)
        plans = Path(serve.plans.root).expanduser() if serve.plans.root else notes / "plans"
        _seed_manual_browser(notes, plans, root)
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(cli, "_weekly_loop", _wait_without_host_weekly_work)
            atk.main(["serve", "--host", "127.0.0.1", "--port", str(args.port)])
        return 0
    plans = root / "plans"
    app = _browser_app(root, plans)
    _seed_manual_browser(root, plans, root)
    asyncio.run(app.run_task(host="127.0.0.1", port=args.port))
    return 0


async def _wait_without_host_weekly_work(stop: asyncio.Event) -> None:
    await stop.wait()


def test_manual_cli_rejects_external_legacy_config_before_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _isolate_creation_time_index(tmp_path, monkeypatch)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("AGENT_TOOLKIT_SERVE_CONFIG", str(tmp_path.parent / "outside-serve.toml"))
    monkeypatch.setattr(config, "resolve_config", lambda **_kwargs: pytest.fail("旧設定の移行より先に拒否する"))
    with pytest.raises(ValueError, match="一時領域外: 旧設定"):
        _manual_cli_inputs(tmp_path, 28766)


def _seed_manual_browser(notes: Path, plans: Path, root: Path) -> None:
    _write_entries(notes)
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "sample.md").write_text("# 検証用の計画\n", encoding="utf-8")
    _write_session_records(root)


def _browser_app(
    tmp_path: Path,
    plans_root: Path | None = None,
    *,
    plan_roots: tuple[serve_plans.RootSpec, ...] = (),
    hostname: str = "browser-test",
    agent_listing: Any = None,
    current_state: serve_state.ServeState | None = None,
    operations: _BrowserOperations | None = None,
    **sessions_options: Any,
) -> Any:
    if (plans_root is None) == (not plan_roots):
        raise ValueError("ブラウザー検証の計画ルートを単一または複数で指定する")
    session_state = sessions_options.setdefault("state_dir", tmp_path / "state")
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    timing = tmp_path / "state" / "serve" / "timings.jsonl"
    if sessions_options.get("remote_hosts") and sessions_options.get("ssh_runner") in (None, serve_sessions.default_ssh_runner):
        raise ValueError("ブラウザー検証のリモートホストには代替SSH応答が必要")
    _require_browser_paths(
        tmp_path,
        {
            "home": Path.home(),
            "状態": state_paths.state_dir(),
            "既定の計測": timings.default_log_path(),
            "WI": tmp_path,
            "Claude Code": claude / "projects",
            "Codex": codex / "sessions",
            "古いCodex記録": codex / "archived_sessions",
            "セッション状態": session_state,
            "要約キャッシュ": session_state / "serve" / "session-summaries.json",
            "一覧キャッシュ": session_state / "serve" / "session-list.json",
            "費用キャッシュ": session_state / "serve" / "session-costs.json",
            "費用記録": session_state / "serve" / "cost-records",
            "計測": timing,
            "計画作成時刻の索引": plan_creation_times._CREATION_TIME_INDEX_PATH,  # noqa: SLF001  # pylint: disable=protected-access
            **({"計画": plans_root} if plans_root is not None else {}),
            **{f"計画{index}": spec.path for index, spec in enumerate(plan_roots)},
        },
    )
    return serve_app.create_app(
        tmp_path,
        config.ServeConfig("127.0.0.1", 28766),
        current_state if current_state is not None else serve_state.ServeState(tmp_path),
        operations=operations if operations is not None else _BrowserOperations(tmp_path),
        agent_listing=agent_listing,
        timing_log_path=timing,
        plans_context=serve_plans.create_context(root=plans_root, roots=plan_roots or None, hostname=hostname),
        sessions_context=serve_sessions.create_context(
            hostname=hostname,
            claude_home=claude,
            codex_home=codex,
            **sessions_options,
        ),
    )


@pytest.mark.parametrize("blocked", ["home", "state", "plan", "index", "remote"])
def test_browser_app_rejects_nonisolated_inputs_before_context_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, blocked: str
) -> None:
    outside = tmp_path.parent / "outside-browser"
    _isolate_creation_time_index(tmp_path, monkeypatch)
    options: dict[str, Any] = {}
    plans = tmp_path / "plans"
    if blocked == "home":
        monkeypatch.setenv("HOME", str(outside))
        monkeypatch.setenv("USERPROFILE", str(outside))
    elif blocked == "state":
        options["state_dir"] = outside
    elif blocked == "plan":
        plans = outside
    elif blocked == "index":
        _isolate_creation_time_index(outside, monkeypatch)
    else:
        options["remote_hosts"] = ("unreachable.invalid",)
    monkeypatch.setattr(serve_plans, "create_context", lambda **_kwargs: pytest.fail("計画の初期化より先に拒否する"))
    with pytest.raises(ValueError, match="ブラウザー検証"):
        _browser_app(tmp_path, plans, **options)


@pytest.mark.asyncio
async def test_serve_stops_server_when_browser_context_close_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """ブラウザーの終了失敗でも、サーバーの終了待機まで完遂する。"""
    stopped = asyncio.Event()

    class Context:
        """終了だけを失敗させるブラウザーコンテキスト。"""

        async def grant_permissions(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def new_page(self) -> Any:
            return object()

        async def close(self) -> None:
            raise RuntimeError("context close failed")

    class Browser:
        async def new_context(self) -> Any:
            return Context()

    class App:
        async def run_task(self, *_args: Any, shutdown_trigger: Any, **_kwargs: Any) -> None:
            try:
                await shutdown_trigger()
            finally:
                stopped.set()

    async def ready(_port: int) -> None:
        pass

    monkeypatch.setattr(f"{__name__}._wait_for_server", ready)
    with pytest.raises(RuntimeError, match="context close failed"):
        async with _serve(App(), cast(playwright.async_api.Browser, Browser())):
            pass
    assert stopped.is_set()


@pytest_asyncio.fixture(loop_scope="session", name="screen_harness")
async def _screen_harness_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    browser: playwright.async_api.Browser,
    request: pytest.FixtureRequest,
) -> AsyncGenerator[_ScreenHarness]:
    _isolate_creation_time_index(tmp_path, monkeypatch)
    _write_entries(tmp_path)
    plans_root = tmp_path / "plans"
    plans_root.mkdir()
    plan_path = plans_root / "plan.md"
    plan_path.write_text(getattr(request, "param", "# 初回\n\n本文-初回\n"), encoding="utf-8")
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
            sessions_context=app.config["SESSIONS_CONTEXT"],
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
        + '{"type":"user","message":{"content":"途中で途絶\n',
        encoding="utf-8",
    )

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
    "_SERVER_START_TIMEOUT_SEC",
    "_LONG_UNKNOWN_FRONTMATTER_KEY",
    "_hold_route",
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


if __name__ == "__main__":
    raise SystemExit(_manual_browser_main(sys.argv[1:]))
