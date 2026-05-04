"""Quartアプリ生成とAPIハンドラ。

`create_app`はパッケージ外へも公開する公開API。それ以外のヘルパー（`resolve_request_target`・
`safe_base_path`）はpackage-internalとしてunderscoreなしで定義する。

本モジュールは`pytilpack.quart.ProxyFix`を採用しており、リバースプロキシ前段は
`X-Forwarded-Prefix`を保持して転送する構成（prefixを除去しない構成）を前提とする。
Quartは`scope.root_path`をパス冒頭から除去する仕様のため、prefixを除去する構成では404を返す。
`safe_base_path`は信頼境界として`request.root_path`を厳格に検査する。
"""

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
    不正値や空値は空文字列として返し、呼び出し元がそのままURL前置として扱えるようにする。
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


def resolve_request_target(local_host: str, allowed_remote_hosts: set[str]) -> tuple[str, str] | quart.Response:
    """`/api/file`・`/api/raw`共通: hostとpathを取り出して許可リスト検証する。

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


def create_app(
    root: pathlib.Path,
    hostname: str | None = None,
    remote_hosts: list[str] | None = None,
    ssh_runner: _remote.SshRunner | None = None,
) -> quart.Quart:
    """Quartアプリを生成する。

    `root`はMarkdownの探索対象ディレクトリ（resolve済み絶対パス）。
    `hostname`はローカル分のファイルエントリーに付与する`host`ラベルおよび
    リモートホストとの一意性検査に使う。`None`のとき`socket.gethostname()`を使う。
    `remote_hosts`が空でない場合、各ホストへSSH越しにwatchを起動して差分イベントを配信する。
    `ssh_runner=None`のときは`default_ssh_runner`を使う（`/api/file`/`/api/raw`の
    リモート参照経路でのみ使用する。watch経路は`RemoteWatcher`が直接asyncio subprocessを起動する）。
    """
    app = quart.Quart(__name__)
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

    # 初期接続状態を設定する。ローカルは常にconnected、リモートはconnecting開始。
    state.host_status[resolved_hostname] = "connected"
    for host in remote_host_list:
        state.host_status[host] = "connecting"

    # app.configに格納してモジュールレベルの可変状態を避ける。
    # ルートハンドラからは`quart.current_app.config`経由で参照する。
    app.config["PLANS_ROOT"] = root
    app.config["PLANS_RENDERER"] = renderer
    app.config["PLANS_MARKDOWN_CACHE"] = markdown_cache
    app.config["PLANS_STATE"] = state
    app.config["PLANS_HOSTNAME"] = resolved_hostname
    app.config["PLANS_REMOTE_HOSTS"] = remote_host_list
    app.config["PLANS_SSH_RUNNER"] = runner

    @app.before_serving
    async def _capture_loop() -> None:
        # watchdogスレッドからの配信ブリッジに必要なイベントループ参照を保持する。
        state.loop = asyncio.get_running_loop()
        # リモートwatchタスクを起動する。test_client経由ではbefore_serving自体が
        # 発火しないため、テスト側は`RemoteWatcher`を直接駆動する。
        for host in remote_host_list:
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

    @app.get("/")
    async def index() -> quart.Response:
        base_path = safe_base_path(quart.request.root_path)
        # HTML属性向けには`html.escape(quote=True)`、JavaScriptリテラル向けには`json.dumps`で
        # 文字列リテラル化し、コンテキスト別のエスケープ経路で埋め込む。
        body = _assets.INDEX_HTML.replace("__BASE_PATH_HTML__", html.escape(base_path, quote=True)).replace(
            "__BASE_PATH_JS__", json.dumps(base_path)
        )
        return quart.Response(body, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/static/markdown.css")
    async def markdown_css() -> quart.Response:
        return quart.Response(
            await _local.read_css(),
            content_type="text/css; charset=utf-8",
            headers={"Cache-Control": "no-store"},
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

    @app.get("/api/host-status")
    async def api_host_status() -> quart.Response:
        # SPA起動時の初期同期用。SSE取りこぼし時の救済経路としても使う。
        async with state.lock:
            snapshot = dict(state.host_status)
        body = json.dumps(snapshot, ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/files")
    async def api_files() -> quart.Response:
        # ローカル一覧はリモート集約と並列実行できるよう`asyncio.to_thread`経由で取得する。
        local_entries = await asyncio.to_thread(_local.list_files, root, resolved_hostname)
        async with state.lock:
            remote_entries: list[_state.FileEntry] = []
            for cached in state.remote_files.values():
                remote_entries.extend(cached)
        merged = local_entries + remote_entries
        merged.sort(key=lambda e: e.mtime_epoch, reverse=True)
        body = json.dumps([dataclasses.asdict(e) for e in merged], ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    async def _resolve_text_and_mtime(host: str, rel: str) -> tuple[str, float | None] | quart.Response:
        """`api_file`/`api_raw`共通: 本文と`mtime_epoch`を取得する。

        ローカルは`stat`から、リモートは`fetch_remote_file`が本文と同時取得した値を使う。
        パスやホストが不正な場合はQuart応答を返す。
        """
        if host == resolved_hostname:
            target = _local.resolve_under_root(root, rel)
            if target is None:
                return quart.Response("not found", status=404)

            def _read_with_mtime(path: pathlib.Path) -> tuple[str, float]:
                # 本文とstatを連続取得して、`mtime_epoch`の整合を最大限保つ。
                data = path.read_text(encoding="utf-8", errors="replace")
                return data, path.stat().st_mtime

            text, mtime = await asyncio.to_thread(_read_with_mtime, target)
            return text, mtime
        if not _remote.is_safe_remote_relpath(rel):
            return quart.Response("invalid path", status=400)
        watcher = state.remote_watchers.get(host)
        try:
            return await _remote.fetch_remote_file(host, rel, runner, watcher)
        except Exception as e:  # noqa: BLE001
            logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, e)
            return quart.Response("not found", status=404)

    @app.get("/api/file")
    async def api_file() -> quart.Response:
        resolved = resolve_request_target(resolved_hostname, allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        result = await _resolve_text_and_mtime(host, rel)
        if isinstance(result, quart.Response):
            return result
        text, mtime = result
        # `mtime`が取れた場合のみキャッシュを参照する。リモート応答にmtimeが欠落した場合は
        # 古い結果を返さないよう安全側に倒してバイパスする。
        cache_key: _local.MarkdownCacheKey | None = (host, rel, mtime) if mtime is not None else None
        rendered: str | None = None
        if cache_key is not None:
            rendered = markdown_cache.get(cache_key)
        if rendered is None:
            rendered = _local.markdown_to_html(text, renderer)
            if cache_key is not None:
                markdown_cache.put(cache_key, rendered)
        return quart.Response(rendered, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/raw")
    async def api_raw() -> quart.Response:
        # クライアントのコピーボタン用に生Markdownを返す。`/api/file`はHTMLレンダリング結果を返すため
        # 経路を分離し、`Cache-Control`扱いやテストを単純に保つ。
        resolved = resolve_request_target(resolved_hostname, allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        result = await _resolve_text_and_mtime(host, rel)
        if isinstance(result, quart.Response):
            return result
        text, _ = result
        return quart.Response(text, content_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/events")
    async def api_events() -> quart.Response:
        @pytilpack.sse.generator()
        async def generate() -> typing.AsyncGenerator[pytilpack.sse.SSE, None]:
            q = await _state.subscribe(state)
            try:
                while True:
                    msg = await q.get()
                    # 既存クライアント(EventSourceの`onmessage`)が受け取るよう、
                    # event名を付けずdataのみで配信する。
                    yield pytilpack.sse.SSE(data=msg)
            finally:
                await _state.unsubscribe(state, q)

        return quart.Response(
            generate(),
            content_type="text/event-stream",
            headers={"Cache-Control": "no-store", "Connection": "keep-alive"},
        )

    # X-Forwarded-Proto/Prefix を解釈してASGI scopeへ反映するミドルウェアを介在させる。
    # `app.asgi_app`（バウンドメソッド）を入れ替えるQuartの公式パターンを使うことで、
    # `app.config`等のハンドラ参照は維持しつつ、ASGIディスパッチだけを上流に通す。
    # method-assignとASGIプロトコル不一致は意図的なため型チェッカは抑制する。
    app.asgi_app = pytilpack.quart.ProxyFix(app)  # type: ignore[method-assign,assignment]  # ty: ignore[invalid-assignment]
    return app
