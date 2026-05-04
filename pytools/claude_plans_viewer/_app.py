"""Quartアプリ生成とAPIハンドラ。

`create_app`はパッケージ外へも公開する公開API。それ以外のヘルパー（`resolve_request_target`）は
package-internalとしてunderscoreなしで定義する。
"""

import asyncio
import contextlib
import dataclasses
import html
import json
import logging
import pathlib
import socket
import typing

import pytilpack.sse
import quart

from pytools.claude_plans_viewer import _assets, _local, _remote, _state

logger = logging.getLogger(__name__)


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
    `hostname`はトップページとローカル分の`host`ラベルへ埋め込むホスト名。
    `None`のとき`socket.gethostname()`を使う。
    `remote_hosts`が空でない場合、各ホストへSSH越しにwatchを起動して差分イベントを配信する。
    `ssh_runner=None`のときは`default_ssh_runner`を使う（`/api/file`/`/api/raw`の
    リモート参照経路でのみ使用する。watch経路は`RemoteWatcher`が直接asyncio subprocessを起動する）。
    """
    app = quart.Quart(__name__)
    renderer = _local.make_md_renderer()
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
            task = asyncio.create_task(_remote.RemoteWatcher(host, state).run())
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
        body = _assets.INDEX_HTML.replace("__HOSTNAME__", html.escape(resolved_hostname))
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
        return quart.Response(
            _assets.MANIFEST_JSON,
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

    @app.get("/api/file")
    async def api_file() -> quart.Response:
        resolved = resolve_request_target(resolved_hostname, allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        if host == resolved_hostname:
            target = _local.resolve_under_root(root, rel)
            if target is None:
                return quart.Response("not found", status=404)
            # read_textはブロッキングI/Oのためスレッドプールで実行する。
            text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        else:
            if not _remote.is_safe_remote_relpath(rel):
                return quart.Response("invalid path", status=400)
            try:
                text = await _remote.fetch_remote_file(host, rel, runner)
            except Exception as e:  # noqa: BLE001
                logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, e)
                return quart.Response("not found", status=404)
        rendered = _local.markdown_to_html(text, renderer)
        return quart.Response(rendered, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/raw")
    async def api_raw() -> quart.Response:
        # クライアントのコピーボタン用に生Markdownを返す。`/api/file`はHTMLレンダリング結果を返すため
        # 経路を分離し、`Cache-Control`扱いやテストを単純に保つ。
        resolved = resolve_request_target(resolved_hostname, allowed_remote_hosts)
        if isinstance(resolved, quart.Response):
            return resolved
        host, rel = resolved
        if host == resolved_hostname:
            target = _local.resolve_under_root(root, rel)
            if target is None:
                return quart.Response("not found", status=404)
            text = await asyncio.to_thread(target.read_text, encoding="utf-8", errors="replace")
        else:
            if not _remote.is_safe_remote_relpath(rel):
                return quart.Response("invalid path", status=400)
            try:
                text = await _remote.fetch_remote_file(host, rel, runner)
            except Exception as e:  # noqa: BLE001
                logger.warning("リモートファイル取得失敗 host=%s path=%s: %s", host, rel, e)
                return quart.Response("not found", status=404)
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

    return app
