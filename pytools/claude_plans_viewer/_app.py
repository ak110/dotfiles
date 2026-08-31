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

from pytools.claude_plans_viewer import _assets, _config, _local, _remote, _state

logger = logging.getLogger(__name__)

# 安全な`base_path`の照合パターン。先頭スラッシュ強制、英数字と`._~/-`のみ、
# 連続スラッシュ（`//`）はスキーム相対URL扱いになり外部オリジン誘導の口になるため別途禁止する。
_BASE_PATH_ALLOWED_RE = re.compile(r"^/[A-Za-z0-9._~-][A-Za-z0-9._~/-]*$")

# 付属計画ファイルの接尾辞。計画一覧からは除外されるため、表示応答内のリンクが到達経路になる。
_DETAIL_SUFFIX = ".detail.md"
_BUGS_SUFFIX = ".bugs.md"
_TARGET_TSV_SUFFIXES = (".plan-review.tsv", ".exec-review.tsv")
_PLAN_SUFFIX_LABELS = (
    (_DETAIL_SUFFIX, "詳細"),
    (_BUGS_SUFFIX, "バグ"),
    (_TARGET_TSV_SUFFIXES[0], "計画レビュー指摘管理表"),
    (_TARGET_TSV_SUFFIXES[1], "実行レビュー指摘管理表"),
)
_REVIEW_TABLE_HEADERS = ("ラウンド", "系統", "箇所", "指摘内容", "対応要否", "対応内容", "対応不要理由")


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


def _resolve_request_target(context: "_AppContext") -> tuple[str, str, str] | quart.Response:
    """`/api/file`・`/api/raw`共通でhost・source・pathを取り出して検証する。

    `host`未指定時は`local_host`を採用する。許可リスト外のhostは400で拒否する
    （サーバーが0.0.0.0等で公開された場合に、クライアントが任意SSH先へ
    接続試行を誘発できないようにするため）。
    """
    rel = quart.request.args.get("path")
    if not rel:
        return quart.Response("path is required", status=400)
    host = quart.request.args.get("host")
    if host is None:
        host = context.hostname
    if host != context.hostname and host not in context.allowed_remote_hosts:
        return quart.Response("unknown host", status=400)
    source_id = quart.request.args.get("source") or quart.request.args.get("source_id") or ""
    resolved_source = _resolve_source_id(context, host, source_id, rel)
    if isinstance(resolved_source, quart.Response):
        return resolved_source
    return host, resolved_source, rel


@dataclasses.dataclass(frozen=True)
class _AppContext:
    """ルート登録関数が共有するアプリ単位の依存。"""

    root: pathlib.Path
    roots: tuple[_state.RootSpec, ...]
    hostname: str
    remote_hosts: list[str]
    allowed_remote_hosts: set[str]
    runner: _remote.SshRunner
    search_coordinator: _remote.RemoteSearchCoordinator
    renderer: markdown_it.MarkdownIt
    markdown_cache: _local.MarkdownCache
    state: _state.BroadcastState


def _root_status(warning: str | None) -> dict[str, str]:
    """rootの利用状態をAPI/SSE向けの小さな辞書へ変換する。"""
    if warning is None:
        return {"status": "ok", "message": ""}
    return {"status": "warning", "message": warning}


def _resolve_source_id(
    context: _AppContext,
    host: str,
    source_id: str,
    rel: str,
) -> str | quart.Response:
    """要求のsource IDを検証・補完する。"""
    if host == context.hostname:
        specs = context.roots
        known = {spec.source_id for spec in specs}
        if source_id:
            if source_id not in known:
                return quart.Response("unknown source", status=400)
            return source_id
        candidates = [spec.source_id for spec in specs if _local.resolve_under_root(spec.path, rel) is not None]
    else:
        known = set(context.state.root_info.get(host, {}))
        if source_id:
            if known and source_id not in known:
                return quart.Response("unknown source", status=400)
            return source_id
        candidates = [entry.source_id for entry in context.state.remote_files.get(host, []) if entry.path == rel]
        if not candidates:
            candidates = list(known)
    unique = list(dict.fromkeys(candidates))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return quart.Response("source is required", status=400)
    # 旧プロトコル・旧単一root接続ではsource IDが存在しない。
    return ""


def create_app(
    root: pathlib.Path | None = None,
    hostname: str | None = None,
    remote_hosts: list[str] | None = None,
    ssh_runner: _remote.SshRunner | None = None,
    *,
    remote_search_limit: int = _remote.DEFAULT_REMOTE_SEARCH_LIMIT,
    roots: typing.Iterable[_state.RootSpec] | None = None,
) -> quart.Quart:
    """Quartアプリを生成する。

    `root`はMarkdownの探索対象ディレクトリ（resolve済み絶対パス）。`roots`を渡した場合は
    そのroot群を使い、`root`は後方互換のため先頭rootとして扱う。
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
    context = _create_app_context(root, roots, hostname, remote_hosts, ssh_runner, remote_search_limit)
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
    root: pathlib.Path | None,
    roots: typing.Iterable[_state.RootSpec] | None,
    hostname: str | None,
    remote_hosts: list[str] | None,
    ssh_runner: _remote.SshRunner | None,
    remote_search_limit: int,
) -> _AppContext:
    """アプリ単位の依存と初期接続状態を生成する。"""
    renderer = _local.make_md_renderer()
    markdown_cache = _local.MarkdownCache()
    state = _state.BroadcastState()
    if roots is None:
        root_specs = _config.default_root_specs() if root is None else (_config.explicit_root_spec(root),)
    else:
        root_specs = _state.normalize_root_specs(roots)
    if not root_specs:
        raise ValueError("no plan roots configured")
    resolved_root = root_specs[0].path
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
    state.host_info[resolved_hostname] = _local.local_host_info(resolved_root)
    state.root_info[resolved_hostname] = {spec.source_id: _local.root_info(spec) for spec in root_specs}
    state.root_status[resolved_hostname] = {
        spec.source_id: _root_status(spec.warning or _local.root_warning(spec.path)) for spec in root_specs
    }

    return _AppContext(
        root=resolved_root,
        roots=root_specs,
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
    app.config["PLANS_ROOTS"] = context.roots
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
        # ページロード時はローカルroot情報を注入する。リモート分はSSE経由の
        # `root_info_update`イベント受信、または`/api/root-info`への再取得で反映する。
        local_root_info = context.state.root_info[context.hostname]
        if len(local_root_info) == 1 and "" in local_root_info:
            # 既存の単一root用HTML契約を維持する。
            root_dirs_js: dict[str, typing.Any] = {context.hostname: context.state.host_info[context.hostname]}
        else:
            root_dirs_js = {context.hostname: local_root_info}
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

    @app.get("/api/root-info")
    async def api_root_info() -> quart.Response:
        """ホスト・保存元単位の可搬パス情報を返す。"""
        async with context.state.lock:
            snapshot = json.loads(json.dumps(context.state.root_info, ensure_ascii=False))
        body = json.dumps(snapshot, ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/root-status")
    async def api_root_status() -> quart.Response:
        """root単位の利用可能状態と警告を返す。"""
        async with context.state.lock:
            snapshot = json.loads(json.dumps(context.state.root_status, ensure_ascii=False))
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
            local_results = await asyncio.gather(
                *(asyncio.to_thread(_local.search_files, spec.path, query) for spec in context.roots)
            )
            local_matches = {
                (spec.source_id, _listed_plan_path(path))
                for spec, paths in zip(context.roots, local_results, strict=True)
                for path in paths
            }

            # 1ホストの打ち切りで他ホストの結果が未回収の例外にならないよう、全件を回収してから判定する。
            results = await asyncio.gather(
                *(_search_remote(context, host, query) for host in context.remote_hosts),
                return_exceptions=True,
            )
            remote_matches: dict[str, set[tuple[str, str]]] = {}
            for result in results:
                if isinstance(result, _remote.RemoteSearchSuperseded):
                    return quart.Response("search superseded", status=409, headers={"Cache-Control": "no-store"})
                if isinstance(result, BaseException):
                    # `search_remote`は`Exception`のみを捕捉するため、この分岐はキャンセル等に限られる。
                    raise result
                matched_host, matched_paths = result
                remote_matches[matched_host] = {(source_id, _listed_plan_path(path)) for source_id, path in matched_paths}
            matched = [
                entry
                for entry in entries
                if (
                    (entry.source_id, entry.path)
                    in (local_matches if entry.host == context.hostname else remote_matches.get(entry.host, set()))
                )
            ]
        body = json.dumps([dataclasses.asdict(entry) for entry in matched], ensure_ascii=False)
        return quart.Response(body, content_type="application/json; charset=utf-8", headers={"Cache-Control": "no-store"})


async def _all_entries(context: _AppContext) -> list[_state.FileEntry]:
    """ローカルとリモートの一覧を作成日時の降順で返す。"""
    # ローカル一覧はリモート集約と並列実行できるよう`asyncio.to_thread`経由で取得する。
    local_results = await asyncio.gather(
        *(asyncio.to_thread(_scan_local_root, spec, context.hostname) for spec in context.roots)
    )
    local_entries = [entry for entries, _ in local_results for entry in entries]
    local_status = {
        spec.source_id: _root_status(warning) for spec, (_, warning) in zip(context.roots, local_results, strict=True)
    }
    async with context.state.lock:
        previous_status = context.state.root_status.get(context.hostname)
        context.state.root_status[context.hostname] = local_status
    if previous_status != local_status:
        await _state.deliver_root_status(context.state, context.hostname, local_status)
    async with context.state.lock:
        remote_entries: list[_state.FileEntry] = []
        for cached in context.state.remote_files.values():
            remote_entries.extend(cached)
    merged = local_entries + remote_entries
    merged.sort(key=lambda entry: (-entry.ctime_epoch, entry.host, entry.source_id, entry.path))
    return merged


def _scan_local_root(spec: _state.RootSpec, host: str) -> tuple[list[_state.FileEntry], str | None]:
    """root解決時の警告を保ったまま、利用可能なローカルrootだけを走査する。"""
    if spec.warning is not None:
        return [], spec.warning
    return _local.scan_files(
        spec.path,
        host,
        spec.source_id,
        migrate_legacy_ctime=spec.migrate_legacy_ctime,
    )


async def _search_remote(context: _AppContext, host: str, query: str) -> tuple[str, set[tuple[str, str]]]:
    """1台のリモートホストを本文検索する。"""
    try:
        raw_paths = await _remote.search_remote_files(
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
        raw_paths = set()
    source_ids = set(context.state.root_info.get(host, {}))
    fallback_source = next(iter(source_ids)) if len(source_ids) == 1 else ""
    paths: set[tuple[str, str]] = set()
    for value in raw_paths:
        if isinstance(value, tuple) and len(value) == 2:
            paths.add((str(value[0]), str(value[1])))
        else:
            paths.add((fallback_source, str(value)))
    return host, paths


def _register_file_routes(app: quart.Quart, context: _AppContext) -> None:
    """計画ファイルのHTML表示と原文取得ルートを登録する。"""

    @app.get("/api/file")
    async def api_file() -> quart.Response:
        resolved = _resolve_request_target(context)
        if isinstance(resolved, quart.Response):
            return resolved
        host, source_id, rel = resolved
        result = await _resolve_text_and_mtime(context, host, source_id, rel)
        if isinstance(result, quart.Response):
            return result
        text, mtime = result
        # `mtime`が取れた場合のみキャッシュを参照する。リモート応答にmtimeが欠落した場合は
        # 古い結果を返さないよう安全側に倒してバイパスする。
        cache_key: _local.MarkdownCacheKey | None = None
        if mtime is not None:
            cache_key = (host, source_id, rel, mtime) if source_id else (host, rel, mtime)
        rendered: str | None = None
        if cache_key is not None:
            rendered = context.markdown_cache.get(cache_key)
        if rendered is None:
            rendered = (
                _review_table_html(text)
                if rel.endswith(_TARGET_TSV_SUFFIXES)
                else _local.markdown_to_html(text, context.renderer)
            )
            if cache_key is not None:
                context.markdown_cache.put(cache_key, rendered)
        # 付属計画リンクは応答組み立て層で付与する。本文変換とそのキャッシュへ混ぜると、
        # 付属計画の出現・消失のたびに本文HTMLキャッシュを無効化する必要が生じるため。
        body = await _plan_links_html(context, host, source_id, rel) + rendered
        return quart.Response(body, content_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})

    @app.get("/api/raw")
    async def api_raw() -> quart.Response:
        # クライアントのコピーボタン用に原文を返す。`/api/file`はHTMLレンダリング結果を返すため
        # 経路を分離し、`Cache-Control`扱いやテストを単純に保つ。
        resolved = _resolve_request_target(context)
        if isinstance(resolved, quart.Response):
            return resolved
        host, source_id, rel = resolved
        result = await _resolve_text_and_mtime(context, host, source_id, rel)
        if isinstance(result, quart.Response):
            return result
        text, _ = result
        content_type = "text/plain; charset=utf-8" if rel.endswith(_TARGET_TSV_SUFFIXES) else "text/markdown; charset=utf-8"
        return quart.Response(text, content_type=content_type, headers={"Cache-Control": "no-store"})


def _review_table_html(text: str) -> str:
    """JSON文字列7列のレビュー指摘管理表をHTML表へ変換し、不正入力は原文表示へ戻す。"""
    rows: list[list[str]] = []
    try:
        for line in text.splitlines():
            encoded_cells = line.split("\t")
            if len(encoded_cells) != len(_REVIEW_TABLE_HEADERS):
                raise ValueError("レビュー指摘管理表の列数が不正です")
            cells = [json.loads(cell) for cell in encoded_cells]
            if not all(isinstance(cell, str) for cell in cells):
                raise ValueError("レビュー指摘管理表のセルがJSON文字列ではありません")
            rows.append(cells)
    except (json.JSONDecodeError, ValueError):
        return f"<pre>{html.escape(text)}</pre>\n"

    head = "".join(f"<th>{html.escape(header)}</th>" for header in _REVIEW_TABLE_HEADERS)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table>\n<thead><tr>{head}</tr></thead>\n<tbody>{body}</tbody>\n</table>\n"


def _plan_paths(rel: str) -> tuple[tuple[str, str], ...]:
    """同じstemに属する計画ファイルの相対パスと表示名を返す。"""
    suffix = next((suffix for suffix, _ in _PLAN_SUFFIX_LABELS if rel.endswith(suffix)), None)
    if suffix is None:
        if not rel.endswith(".md"):
            return ()
        stem = rel[: -len(".md")]
    else:
        stem = rel[: -len(suffix)]
    return (
        (f"{stem}.md", "メイン"),
        *((f"{stem}{attached_suffix}", label) for attached_suffix, label in _PLAN_SUFFIX_LABELS),
    )


def _listed_plan_path(rel: str) -> str:
    """付属ファイルの検索一致を一覧で選択できる計画ファイル（メイン）へ接続する。"""
    suffix = next((suffix for suffix, _ in _PLAN_SUFFIX_LABELS if rel.endswith(suffix)), None)
    return rel if suffix is None else f"{rel[: -len(suffix)]}.md"


async def _plan_links_html(context: _AppContext, host: str, source_id: str, rel: str) -> str:
    """同じstemで実在する他の計画だけを、表示応答の先頭へリンクとして付ける。"""
    plan_paths = _plan_paths(rel)
    if not plan_paths:
        return ""
    existing: list[tuple[str, str]] = []
    for plan_rel, label in plan_paths:
        if plan_rel == rel or await _plan_exists(context, host, source_id, plan_rel):
            existing.append((plan_rel, label))
    if len(existing) < 2:
        return ""
    parts: list[str] = []
    for plan_rel, label in existing:
        if plan_rel == rel:
            parts.append(html.escape(label))
            continue
        escaped = html.escape(plan_rel, quote=True)
        parts.append(f'<a href="#" data-plan-path="{escaped}">{html.escape(label)}</a>')
    return f'<nav class="detail-link">{" | ".join(parts)}</nav>\n'


async def _plan_exists(context: _AppContext, host: str, source_id: str, plan_rel: str) -> bool:
    """付属計画の実在を判定する。

    ローカルはファイルシステムの存在確認、リモートは`_remote_helper.py`のファイル取得の成否で判定する
    （付属計画は一覧から除外されるため、リモートでは一覧応答から実在を判定できない）。
    """
    if host == context.hostname:
        spec = next((item for item in context.roots if item.source_id == source_id), None)
        return spec is not None and _local.resolve_under_root(spec.path, plan_rel) is not None
    if not _remote.is_safe_remote_relpath(plan_rel):
        return False
    watcher = context.state.remote_watchers.get(host)
    try:
        await _remote.fetch_remote_file(host, plan_rel, context.runner, watcher, source_id=source_id)
    except Exception as error:  # noqa: BLE001
        # 付属計画未作成は通常状態であり障害ではないため、記録レベルはdebugとする。
        logger.debug("付属計画の取得不可 host=%s path=%s: %s", host, plan_rel, error)
        return False
    return True


async def _resolve_text_and_mtime(
    context: _AppContext,
    host: str,
    source_id: str,
    rel: str,
) -> tuple[str, float | None] | quart.Response:
    """ファイル本文と`mtime_epoch`を取得する。"""
    if host == context.hostname:
        spec = next((item for item in context.roots if item.source_id == source_id), None)
        target = _local.resolve_under_root(spec.path, rel) if spec is not None else None
        if target is None:
            return quart.Response("not found", status=404)
        return await asyncio.to_thread(_read_with_mtime, target)
    if not _remote.is_safe_remote_relpath(rel):
        return quart.Response("invalid path", status=400)
    watcher = context.state.remote_watchers.get(host)
    try:
        return await _remote.fetch_remote_file(host, rel, context.runner, watcher, source_id=source_id)
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
        async def generate() -> typing.AsyncGenerator[pytilpack.sse.SSE]:
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
