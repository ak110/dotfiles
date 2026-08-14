"""Quartアプリ生成とAPIハンドラ群。"""

import asyncio
import contextlib
import dataclasses
import html
import json
import logging
import pathlib
import re
import socket
import typing

import markdown_it
import pytilpack.quart
import pytilpack.sse
import quart

from pytools.claude_plans_viewer import _assets, _local, _remote, _state

logger = logging.getLogger(__name__)

# 安全な`base_path`の照合パターン。先頭スラッシュ強制、英数字と`._~/-`のみ、
# 連続スラッシュ（`//`）はスキーム相対URL扱いになり外部オリジン誘導の口になるため別途禁止する。
_BASE_PATH_ALLOWED_RE = re.compile(r"^/[A-Za-z0-9._~-][A-Za-z0-9._~/-]*$")


def safe_base_path(raw: str) -> str:
    """`request.root_path`を信頼境界として正規化する。

    リバースプロキシ前段が`X-Forwarded-Prefix`を破棄しない構成での悪意ある値の
    HTML/JS/JSON埋め込みを防ぐため、文字種・連続スラッシュ・末尾スラッシュを厳格に検査する。
    不正値や空値は空文字列として返す。呼び出し元はそのままURL前置として扱える。
    """
    if not raw:
        return ""
    candidate = raw.rstrip("/")
    if not candidate:
        return ""
    if "//" in candidate:
        return ""
    if not _BASE_PATH_ALLOWED_RE.fullmatch(candidate):
        return ""
    return candidate


def _resolve_request_target(local_host: str, allowed_remote_hosts: set[str]) -> tuple[str, str] | quart.Response:
    """`/api/file`・`/api/raw`共通でhostとpathを取り出して許可リスト検証する。

    `host`未指定時は`local_host`を採用する。許可リスト外のhostは400で拒否する
    （サーバーが0.0.0.0等で公開された場合に、クライアントが任意SSH先へ
    接続試行を誘発できないようにするため）。
    """
    rel = quart.request.args.get("path")
    if not rel:
        return quart.Response("path is required", status=400)
    host = quart.request.args.get("host")
    if host is None:
        host = local_host
    if host != local_host and host not in allowed_remote_hosts:
        return quart.Response("unknown host", status=400)
    return host, rel


@dataclasses.dataclass(frozen=True)
class _AppContext:
    """ルート登録関数が共有するアプリ単位の依存。"""

    root: pathlib.Path
    hostname: str
    remote_hosts: list[str]
    allowed_remote_hosts: set[str]
    runner: _remote.SshRunner
    search_coordinator: _remote.RemoteSearchCoordinator
    renderer: markdown_it.MarkdownIt
    markdown_cache: _local.MarkdownCache
    state: _state.BroadcastState


def create_app(
    root: pathlib.Path,
    hostname: str | None = None,
    remote_hosts: list[str] | None = None,
    ssh_runner: _remote.SshRunner | None = None,
    *,
    remote_search_limit: int = _remote.DEFAULT_REMOTE_SEARCH_LIMIT,
) -> quart.Quart:
    """Quartアプリを生成する。

    `root`はMarkdownの探索対象ディレクトリ（resolve済み絶対パス）。
    `hostname`はローカル分のファイルエントリに付与する`host`ラベルおよび
    リモートホストとの一意性検査に使う。`None`のとき`socket.gethostname()`を使う。
    `remote_hosts`が空でない場合、各ホストへSSH越しにwatchを起動して差分イベントを配信する。
    `ssh_runner=None`のときは`default_ssh_runner`を使う（`/api/file`/`/api/raw`の
    リモート参照経路でのみ使用する。watch経路は`RemoteWatcher`が直接asyncio subprocessを起動する）。
    `remote_search_limit`はSSHフォールバック経路の検索を同時に実行する全ホスト合計の上限。
    """
    # 前回の異常終了で残った作成日時インデックスの一時ファイルを起動時に除去する。
    _local.cleanup_creation_time_temporaries()
    app = quart.Quart(__name__)
    context = _create_app_context(root, hostname, remote_hosts, ssh_runner, remote_search_limit)
    _configure_app(app, context)
    _register_lifecycle_handlers(app, context)
    _register_asset_routes(app, context)
    _register_listing_routes(app, context)
    _register_file_routes(app, context)
    _register_event_routes(app, context)

    # X-Forwarded-Proto/Prefix を解釈してASGI scopeへ反映するミドルウェアを介在させる。
    # `app.asgi_app`（バウンドメソッド）を入れ替えるQuartの公式パターンを使うことで、
    # `app.config`等のハンドラ参照は維持しつつ、ASGIディスパッチだけを上流に通す。
    # 前提: リバースプロキシ前段が`X-Forwarded-Prefix`を保持して転送する構成
    # （prefixを除去しない構成）であること。Quartは`scope.root_path`をパス冒頭から除去するため、
    # prefixを除去する構成では404を返す。
    # method-assignとASGIプロトコル不一致は意図的なため型チェッカは抑制する。
    app.asgi_app = pytilpack.quart.ProxyFix(app)  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
    return app


def _create_app_context(
    root: pathlib.Path,
    hostname: str | None,
    remote_hosts: list[str] | None,
    ssh_runner: _remote.SshRunner | None,
    remote_search_limit: int,
) -> _AppContext:
    """アプリ単位の依存と初期接続状態を生成する。"""
    renderer = _local.make_md_renderer()
    markdown_cache = _local.MarkdownCache()
    state = _state.BroadcastState()
    resolved_hostname = hostname if hostname is not None else socket.gethostname()
    remote_host_list = list(remote_hosts) if remote_hosts else []
    if resolved_hostname in remote_host_list:
        # `remote_files`のキーが衝突しローカル/リモートが上書きし合うため、起動時に拒絶する。
        raise ValueError("local hostname conflicts with --remote-host")
    allowed_remote_hosts = set(remote_host_list)
    runner: _remote.SshRunner = ssh_runner if ssh_runner is not None else _remote.default_ssh_runner
    # モジュールレベルの可変状態を避けるため、コーディネーターもアプリ単位で生成する。
    search_coordinator = _remote.RemoteSearchCoordinator(remote_search_limit)

    # 初期接続状態を設定する。ローカルは常にconnected、リモートはconnecting開始。
    state.host_status[resolved_hostname] = "connected"
    for host in remote_host_list:
        state.host_status[host] = "connecting"
    # ローカルホスト分の`host_info`は起動時に即座にセットする。
    # リモート分は接続確立時（初回snapshot受信）にRemoteWatcher側で追加する。
    state.host_info[resolved_hostname] = _local.local_host_info(root)

    return _AppContext(
        root=root,
        hostname=resolved_hostname,
        remote_hosts=remote_host_list,
        allowed_remote_hosts=allowed_remote_hosts,
        runner=runner,
        search_coordinator=search_coordinator,
        renderer=renderer,
        markdown_cache=markdown_cache,
        state=state,
    )


def _configure_app(app: quart.Quart, context: _AppContext) -> None:
    """公開済みのアプリ設定値を登録する。"""
    app.config["PLANS_ROOT"] = context.root
    app.config["PLANS_RENDERER"] = context.renderer
    app.config["PLANS_MARKDOWN_CACHE"] = context.markdown_cache
    app.config["PLANS_STATE"] = context.state
    app.config["PLANS_HOSTNAME"] = context.hostname
    app.config["PLANS_REMOTE_HOSTS"] = context.remote_hosts
    app.config["PLANS_SSH_RUNNER"] = context.runner


def _register_lifecycle_handlers(app: quart.Quart, context: _AppContext) -> None:
    """リモートwatchの起動・終了ハンドラを登録する。"""
    state = context.state

    @app.before_serving
    async def _capture_loop() -> None:
        # watchdogスレッドからの配信ブリッジに必要なイベントループ参照を保持する。
        state.loop = asyncio.get_running_loop()
        # リモートwatchタスクを起動する。test_client経由ではbefore_serving自体が
        # 発火しないため、テスト側は`RemoteWatcher`を直接駆動する。
        for host in context.remote_hosts:
            watcher = _remote.RemoteWatcher(host, state)
            # `/api/file`/`/api/raw`がwatch経路のRPCを利用できるよう参照を共有する。
            state.remote_watchers[host] = watcher
            task = asyncio.create_task(watcher.run())
            state.remote_tasks.append(task)

    @app.after_serving
    async def _cancel_remote_tasks() -> None:
        for task in state.remote_tasks:
            task.cancel()
        for task in state.remote_tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        state.remote_tasks.clear()


def _register_asset_routes(app: quart.Quart, context: _AppContext) -> None:
    """画面本体と静的資産のルートを登録する。"""

    @app.get("/")
    async def index() -> quart.Response:
        base_path = safe_base_path(quart.request.root_path)
        # ページロード時はローカル分`host_info`のみを注入する。リモート分はSSE経由の
        # `host_info_update`イベント受信、または`/api/host-info`への再取得で反映する。
        root_dirs_js = {context.hostname: context.state.host_info[context.hostname]}
        # HTML属性向けには`html.escape(quote=True)`、JavaScriptリテラル向けには`json.dumps`で
        # 文字列リテラル化し、コンテキスト別のエスケープ経路で埋め込む。
        body = (
            _assets.INDEX_HTML.replace("__BASE_PATH_HTML__", html.escape(base_path, quote=True))
            .replace("__BASE_PATH_JS__", json.dumps(base_path))
            .replace("__LOCAL_HOST_NAME_JS__", json.dumps(context.hostname))
            .replace("__ROOT_DIRS_JS__", json.dumps(root_dirs_js, ensure_ascii=False))
        )
        return quart.Response(body, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/static/markdown.css")
    async def markdown_css() -> quart.Response:
        return quart.Response(
            await _local.read_css(),
            content_type="text/css; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/static/pygments.css")
    async def pygments_css() -> quart.Response:
        return quart.Response(
            _local.read_pygments_css(),
            content_type="text/css; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/static/mermaid.min.js")
    async def mermaid_script() -> quart.Response:
        bundle = await asyncio.to_thread(_local.read_mermaid_bundle)
        return quart.Response(
            bundle,
            content_type="text/javascript; charset=utf-8",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @app.get("/favicon.svg")
    async def favicon() -> quart.Response:
        return quart.Response(
            _assets.FAVICON_SVG,
            content_type="image/svg+xml; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/manifest.webmanifest")
    async def manifest() -> quart.Response:
        # PWA manifestは静的JSONを文字列置換するとurl値の検証/エスケープを誤りやすいため、
        # 各リクエストで辞書からビルドし`json.dumps`で安全に直列化する。
        base_path = safe_base_path(quart.request.root_path)
        body = json.dumps(_assets.build_manifest(base_path), ensure_ascii=False)
        return quart.Response(
            body,
            content_type="application/manifest+json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/sw.js")
    async def service_worker() -> quart.Response:
        return quart.Response(
            _assets.SERVICE_WORKER_JS,
            content_type="application/javascript; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )


def _register_listing_routes(app: quart.Quart, context: _AppContext) -> None:
    """ホスト情報・ファイル一覧・本文検索のルートを登録する。"""

    @app.get("/api/host-status")
    async def api_host_status() -> quart.Response:
        # SPA起動時の初期同期用。SSE取りこぼし時の救済経路としても使う。
        async with context.state.lock:
            snapshot = dict(context.state.host_status)
        body = json.dumps(snapshot, ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/host-info")
    async def api_host_info() -> quart.Response:
        # SPA起動時の初期同期用。`host_info_update`のSSE取りこぼし時の救済経路としても使う。
        async with context.state.lock:
            snapshot = dict(context.state.host_info)
        body = json.dumps(snapshot, ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/files")
    async def api_files() -> quart.Response:
        merged = await _all_entries(context)
        body = json.dumps([dataclasses.asdict(e) for e in merged], ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/search")
    async def api_search() -> quart.Response:
        """ローカルと全リモートホストから本文一致するファイル一覧を返す。"""
        query = quart.request.args.get("q", "")
        entries = await _all_entries(context)
        if not query:
            matched = entries
        else:
            local_paths = await asyncio.to_thread(_local.search_files, context.root, query)

            # 1ホストの打ち切りで他ホストの結果が未回収の例外にならないよう、全件を回収してから判定する。
            results = await asyncio.gather(
                *(_search_remote(context, host, query) for host in context.remote_hosts),
                return_exceptions=True,
            )
            remote_matches: dict[str, set[str]] = {}
            for result in results:
                if isinstance(result, _remote.RemoteSearchSuperseded):
                    return quart.Response("search superseded", status=409, headers={"Cache-Control": "no-store"})
                if isinstance(result, BaseException):
                    # `search_remote`は`Exception`のみを捕捉するため、この分岐はキャンセル等に限られる。
                    raise result
                matched_host, matched_paths = result
                remote_matches[matched_host] = matched_paths
            matched = [
                entry
                for entry in entries
                if entry.path in (local_paths if entry.host == context.hostname else remote_matches.get(entry.host, set()))
            ]
        body = json.dumps([dataclasses.asdict(entry) for entry in matched], ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})


async def _all_entries(context: _AppContext) -> list[_state.FileEntry]:
    """ローカルとリモートの一覧を作成日時の降順で返す。"""
    # ローカル一覧はリモート集約と並列実行できるよう`asyncio.to_thread`経由で取得する。
    local_entries = await asyncio.to_thread(_local.list_files, context.root, context.hostname)
    async with context.state.lock:
        remote_entries: list[_state.FileEntry] = []
        for cached in context.state.remote_files.values():
            remote_entries.extend(cached)
    merged = local_entries + remote_entries
    merged.sort(key=lambda entry: entry.ctime_epoch, reverse=True)
    return merged


async def _search_remote(context: _AppContext, host: str, query: str) -> tuple[str, set[str]]:
    """1台のリモートホストを本文検索する。"""
    try:
        paths = await _remote.search_remote_files(
            host,
            query,
            context.runner,
            context.state.remote_watchers.get(host),
            coordinator=context.search_coordinator,
        )
    except _remote.RemoteSearchSuperseded:
        # 打ち切りは失敗として扱わず、別の検索語の結果で埋めないため呼び出し元へ伝える。
        raise
    except Exception as error:  # noqa: BLE001
        logger.warning("リモート本文検索失敗 host=%s: %s", host, error)
        paths = set()
    return host, paths


def _register_file_routes(app: quart.Quart, context: _AppContext) -> None:
    """MarkdownのHTML表示と原文取得ルートを登録する。"""

    @app.get("/api/file")
    async def api_file() -> quart.Response:
        resolved = _resolve_request_target(context.hostname, context.allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        result = await _resolve_text_and_mtime(context, host, rel)
        if isinstance(result, quart.Response):
            return result
        text, mtime = result
        # `mtime`が取れた場合のみキャッシュを参照する。リモート応答にmtimeが欠落した場合は
        # 古い結果を返さないよう安全側に倒してバイパスする。
        cache_key: _local.MarkdownCacheKey | None = (host, rel, mtime) if mtime is not None else None
        rendered: str | None = None
        if cache_key is not None:
            rendered = context.markdown_cache.get(cache_key)
        if rendered is None:
            rendered = _local.markdown_to_html(text, context.renderer)
            if cache_key is not None:
                context.markdown_cache.put(cache_key, rendered)
        return quart.Response(rendered, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/raw")
    async def api_raw() -> quart.Response:
        # クライアントのコピーボタン用に生Markdownを返す。`/api/file`はHTMLレンダリング結果を返すため
        # 経路を分離し、`Cache-Control`扱いやテストを単純に保つ。
        resolved = _resolve_request_target(context.hostname, context.allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        result = await _resolve_text_and_mtime(context, host, rel)
        if isinstance(result, quart.Response):
            return result
        text, _ = result
        return quart.Response(text, content_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-store"})


async def _resolve_text_and_mtime(
    context: _AppContext,
    host: str,
    rel: str,
) -> tuple[str, float | None] | quart.Response:
    """ファイル本文と`mtime_epoch`を取得する。"""
    if host == context.hostname:
        target = _local.resolve_under_root(context.root, rel)
        if target is None:
            return quart.Response("not found", status=404)
        return await asyncio.to_thread(_read_with_mtime, target)
    if not _remote.is_safe_remote_relpath(rel):
        return quart.Response("invalid path", status=400)
    watcher = context.state.remote_watchers.get(host)
    try:
        return await _remote.fetch_remote_file(host, rel, context.runner, watcher)
    except Exception as error:  # noqa: BLE001
        logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, error)
        return quart.Response("not found", status=404)


def _read_with_mtime(path: pathlib.Path) -> tuple[str, float]:
    """ファイル本文と更新日時を連続して取得する。"""
    data = path.read_text(encoding="utf-8", errors="replace")
    return data, path.stat().st_mtime


def _register_event_routes(app: quart.Quart, context: _AppContext) -> None:
    """ファイル更新イベントのSSEルートを登録する。"""

    @app.get("/api/events")
    async def api_events() -> quart.Response:
        @pytilpack.sse.generator()
        async def generate() -> typing.AsyncGenerator[pytilpack.sse.SSE, None]:
            q = await _state.subscribe(context.state)
            try:
                while True:
                    msg = await q.get()
                    # 既存クライアント(EventSourceの`onmessage`)が受け取るよう、
                    # event名を付けずdataのみで配信する。
                    yield pytilpack.sse.SSE(data=msg)
            finally:
                await _state.unsubscribe(context.state, q)

        return quart.Response(
            generate(),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-store", "Connection": "keep-alive"},
        )
