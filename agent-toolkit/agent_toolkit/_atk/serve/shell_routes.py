"""`atk serve`の画面の配信、共通の例外応答、計画ファイル画面とセッション画面のルートの登録。"""

import asyncio
import dataclasses
import functools
import html
import json
import re
import subprocess
import typing

import filelock
import pytilpack.quart
import pytilpack.sse
import quart

from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.serve import assets
from agent_toolkit._atk.serve import plans as serve_plans
from agent_toolkit._atk.serve import runtime as _runtime
from agent_toolkit._atk.serve import sessions as serve_sessions
from agent_toolkit._atk.serve import state as serve_state
from agent_toolkit._atk.serve import wi_operations as _wi_operations
from agent_toolkit._atk.wi import web_input as wi_web_input

# base_pathが安全な形式に一致するかを確認するパターン。先頭スラッシュ必須、英数字と`._~/-`のみ許可し、
# 連続スラッシュ（スキーム相対URL扱いになり外部オリジン誘導の口になる）は別途禁止する。
# 配布物独立性の制約（agent-toolkit配下は他ディレクトリの実装を参照しない）により、
# 同種の検証を行う実装は本ファイル内で完結させる。
_BASE_PATH_ALLOWED_RE = re.compile(r"^/[A-Za-z0-9._~-][A-Za-z0-9._~/-]*$")


def safe_base_path(raw: str) -> str:
    """`request.root_path`を信頼境界として正規化する。

    不正値・空値は空文字列として返す。呼び出し元はそのままURL前置として扱える。
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


def register_error_handlers(app: quart.Quart) -> None:
    """Web API共通の例外応答を登録する。"""

    @app.errorhandler(wi_web_input.WebInputError)
    async def input_error(error: wi_web_input.WebInputError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=str(error)), 400

    @app.errorhandler(_wi_operations.WebApiInputError)
    async def web_api_input_error(error: _wi_operations.WebApiInputError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=str(error)), 400

    @app.errorhandler(FileNotFoundError)
    async def not_found(error: FileNotFoundError) -> tuple[quart.Response, int]:
        return quart.jsonify(error=f"見つかりません: {error}"), 404

    @app.errorhandler(filelock.Timeout)
    async def lock_conflict(error: filelock.Timeout) -> tuple[quart.Response, int]:
        del error
        return quart.jsonify(error="別の操作が進行中です", code="lock_conflict"), 409

    @app.errorhandler(_atk_git_sync.RebaseInProgressError)
    async def rebase_in_progress(error: _atk_git_sync.RebaseInProgressError) -> tuple[quart.Response, int]:
        # 解消操作を要する状態のため`edit_conflict`と別のcodeで返す。同じcodeにすると画面が
        # 「外部で更新されました」の回復文を付け、詳細の開き直しを促す誤った案内になる。
        # `RuntimeError`の派生であり、Quartは例外の型の継承順で最も近いハンドラを選ぶ。
        message = f"WIの保存リポジトリが{error}。次の操作: {error.next_action}"
        return quart.jsonify(error=message, code="rebase_in_progress"), 409

    @app.errorhandler(RuntimeError)
    async def edit_conflict(error: RuntimeError) -> tuple[quart.Response, int]:
        if str(error) != _wi_operations.EDIT_CONFLICT_MESSAGE:
            raise error
        return quart.jsonify(error=str(error), code="edit_conflict"), 409

    @app.errorhandler(subprocess.CalledProcessError)
    async def git_error(error: subprocess.CalledProcessError) -> tuple[quart.Response, int]:
        del error
        status = 503 if quart.request.method == "GET" else 500
        return quart.jsonify(error="Git同期に失敗しました"), status


def render_index(
    plans_context: serve_plans.PlansContext,
    base_path: str,
    initial_screen: str,
) -> str:
    """3画面を含むHTMLへ初期表示とbootstrap値を埋め込む。"""
    local_root_info = plans_context.state.root_info[plans_context.hostname]
    if len(local_root_info) == 1 and "" in local_root_info:
        root_dirs: dict[str, typing.Any] = {plans_context.hostname: plans_context.state.host_info[plans_context.hostname]}
    else:
        root_dirs = {plans_context.hostname: local_root_info}
    # 3画面が共通に使う値（`BASE_PATH`とSSEの無通信判定時間）は`serve-bootstrap`へ、計画ファイル画面だけが
    # 使う値は`plans-bootstrap`へ置く。資産ファイルは要求ごとに変わらないため、要求ごとに変わる値だけを埋め込む。
    serve_bootstrap = {"base_path": base_path, "stall_ms": int(_runtime.SSE_STALL_SEC * 1000)}
    plans_bootstrap = {
        "local_host_name": plans_context.hostname,
        "root_dirs": root_dirs,
    }
    hidden = {
        "wi": "" if initial_screen == "wi" else "hidden",
        "plans": "" if initial_screen == "plans" else "hidden",
        "sessions": "" if initial_screen == "sessions" else "hidden",
    }
    return (
        assets.HTML.replace("__BASE_PATH_HTML__", html.escape(base_path, quote=True))
        .replace("__INITIAL_SCREEN__", initial_screen)
        .replace("__WI_HIDDEN__", hidden["wi"])
        .replace("__PLANS_HIDDEN__", hidden["plans"])
        .replace("__SESSIONS_HIDDEN__", hidden["sessions"])
        .replace("__SERVE_BOOTSTRAP_JSON__", json.dumps(serve_bootstrap, ensure_ascii=False).replace("</", "<\\/"))
        .replace("__PLANS_BOOTSTRAP_JSON__", json.dumps(plans_bootstrap, ensure_ascii=False).replace("</", "<\\/"))
    )


def register_awi_asset_routes(app: quart.Quart, plans_context: serve_plans.PlansContext) -> None:
    """WI画面のHTMLと静的資産のルートを登録する。"""

    @app.get("/")
    async def index() -> quart.Response:
        base_path = safe_base_path(quart.request.root_path)
        body = render_index(plans_context, base_path, "wi")
        return quart.Response(body, content_type="text/html; charset=utf-8")

    @app.get("/static/app.css")
    async def css() -> quart.Response:
        body = f"{assets.CSS}\n{serve_plans.read_pygments_css()}"
        return quart.Response(body, content_type="text/css; charset=utf-8")


def register_shell_routes(app: quart.Quart) -> None:
    """3画面が共有するナビゲーション資産とPWAメタデータのルートを登録する。"""

    @app.get("/static/<name>.js")
    async def javascript(name: str) -> quart.Response:
        # 3画面のESモジュールは全て同じ方式で配信する。モジュールは相対パスで互いを読み込み、
        # 要求ごとに変わる値はHTMLのJSONブロックから読むため、本文を要求ごとに書き換えない。
        body = assets.SCRIPTS.get(f"{name}.js")
        if body is None:
            raise FileNotFoundError(f"{name}.js")
        return no_store(body, "text/javascript; charset=utf-8")

    @app.get("/manifest.webmanifest")
    async def manifest() -> quart.Response:
        base_path = safe_base_path(quart.request.root_path)
        root_url = f"{base_path}/"
        body = {
            "name": "atk serve",
            "short_name": "atk serve",
            "start_url": root_url,
            "scope": root_url,
            "display": "standalone",
            "theme_color": assets.THEME_COLOR,
            "background_color": assets.THEME_COLOR,
            "icons": [
                {
                    "src": f"{base_path}/favicon.svg",
                    "sizes": "192x192 512x512 any",
                    "type": "image/svg+xml",
                    "purpose": "any",
                }
            ],
        }
        response = quart.Response(json.dumps(body, ensure_ascii=False), content_type="application/manifest+json")
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/favicon.svg")
    async def favicon_svg() -> quart.Response:
        return quart.Response(
            assets.FAVICON_SVG,
            content_type="image/svg+xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=3600"},
        )

    def png_response(content: bytes) -> quart.Response:
        response = quart.Response(content, content_type="image/png")
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.get("/static/icon-192.png")
    async def icon_192() -> quart.Response:
        return png_response(assets.ICON_192_PNG)

    @app.get("/static/icon-512.png")
    async def icon_512() -> quart.Response:
        return png_response(assets.ICON_512_PNG)


def no_store(body: str, content_type: str, *, status: int = 200) -> quart.Response:
    """計画ファイル・セッションの応答を常に再取得させるヘッダーで返す。"""
    return quart.Response(body, status=status, content_type=content_type, headers={"Cache-Control": "no-store"})


def _with_assistant_html(detail: dict[str, typing.Any]) -> dict[str, typing.Any]:
    """セッションの詳細のうち、本文を持つアシスタントの発言へ整形したHTMLを`html`として加える。

    整形はWI本文と同じ生HTML無効の設定で行い、記録の本文に含まれるタグとスクリプトを実行させない。
    サーバー側で付与するため、リモートホストの記録も同じ整形になり、リモートのヘルパーを変えずに済む。
    `sessions.py`の表示モデルは`agents_server`のログ出力も使うため、HTMLはこの応答にだけ加える。
    対象をアシスタントの発言に限るのは、他の種別がツールの入出力や挿入本文など記号をそのまま読む本文だからである。
    """
    events = []
    for event in detail.get("events", []):
        text = event.get("text") if isinstance(event, dict) else None
        if isinstance(event, dict) and event.get("kind") == "assistant" and isinstance(text, str) and text:
            event = {**event, "html": _wi_operations.MARKDOWN.render(text)}
        events.append(event)
    return {**detail, "events": events}


def json_no_store(payload: typing.Any) -> quart.Response:
    """JSON応答を常に再取得させるヘッダーで返す。"""
    return no_store(json.dumps(payload, ensure_ascii=False), "application/json; charset=utf-8")


def register_plan_routes(app: quart.Quart, context: serve_plans.PlansContext) -> None:
    """計画ファイル画面のページ・資産・APIのルートを登録する。"""

    @app.get("/plans")
    async def plans_index() -> quart.Response:
        base_path = safe_base_path(quart.request.root_path)
        body = render_index(context, base_path, "plans")
        return no_store(body, "text/html; charset=utf-8")

    @app.get("/api/plans/host-status")
    async def plans_host_status() -> quart.Response:
        async with context.state.lock:
            snapshot = dict(context.state.host_status)
        return json_no_store(snapshot)

    @app.get("/api/plans/host-info")
    async def plans_host_info() -> quart.Response:
        async with context.state.lock:
            snapshot = dict(context.state.host_info)
        return json_no_store(snapshot)

    @app.get("/api/plans/root-info")
    async def plans_root_info() -> quart.Response:
        async with context.state.lock:
            snapshot = json.loads(json.dumps(context.state.root_info, ensure_ascii=False))
        return json_no_store(snapshot)

    @app.get("/api/plans/root-status")
    async def plans_root_status() -> quart.Response:
        async with context.state.lock:
            snapshot = json.loads(json.dumps(context.state.root_status, ensure_ascii=False))
        return json_no_store(snapshot)

    @app.get("/api/plans/files")
    async def plans_files() -> quart.Response:
        entries = await serve_plans.all_entries(context)
        return json_no_store([dataclasses.asdict(entry) for entry in entries])

    @app.get("/api/plans/search")
    async def plans_search() -> quart.Response:
        matched = await serve_plans.search_entries(context, quart.request.args.get("q", ""))
        if matched is None:
            return no_store("search superseded", "text/plain; charset=utf-8")
        return json_no_store([dataclasses.asdict(entry) for entry in matched])

    @app.get("/api/plans/file")
    async def plans_file() -> quart.Response:
        host, source_id, rel = _plan_request_target(context)
        try:
            body = await serve_plans.render_file_html(context, host, source_id, rel)
        except serve_plans.PlanFileError as error:
            return no_store(error.message, "text/plain; charset=utf-8", status=error.status)
        return no_store(body, "text/html; charset=utf-8")

    @app.get("/api/plans/raw")
    async def plans_raw() -> quart.Response:
        # クライアントのコピーボタン用に原文を返す。`/api/plans/file`はHTMLを返すため別のAPIとする。
        host, source_id, rel = _plan_request_target(context)
        try:
            text = await serve_plans.resolve_text(context, host, source_id, rel)
        except serve_plans.PlanFileError as error:
            return no_store(error.message, "text/plain; charset=utf-8", status=error.status)
        content_type = "text/plain; charset=utf-8" if serve_plans.is_review_table_path(rel) else "text/markdown; charset=utf-8"
        return no_store(text, content_type)

    @app.get("/api/plans/events")
    async def plans_events() -> quart.Response:
        return _subscription_stream(
            functools.partial(serve_plans.subscribe, context.state),
            functools.partial(serve_plans.unsubscribe, context.state),
        )


def _subscription_stream(
    subscribe: typing.Callable[[], typing.Awaitable[asyncio.Queue[str]]],
    unsubscribe: typing.Callable[[asyncio.Queue[str]], typing.Awaitable[None]],
) -> quart.Response:
    """購読キューの通知をSSEで配信し、通知の無い間は名前付きのheartbeatを送る応答を返す。

    通知はクライアントの`onmessage`が受け取るよう、event名を付けずdataのみで配信する。
    heartbeatは`event: heartbeat`とし、`onmessage`へ届かないため再取得を起こさない。
    `pytilpack.sse.generator`のコメント行のkeep-aliveは、内側が間隔内に送るため発生しない。
    """
    # 停止要求は生成処理の外で取得する。生成処理はリクエストの文脈の外で実行されるため`current_app`を参照できない。
    current_state: serve_state.ServeState = quart.current_app.config["SERVE_STATE"]
    shutdown = current_state.shutdown_requested

    @pytilpack.sse.generator()
    async def generate() -> typing.AsyncGenerator[pytilpack.sse.SSE]:
        queue = await subscribe()
        try:
            while True:
                received = await serve_state.next_subscription_item(queue, shutdown, _runtime.SSE_HEARTBEAT_SEC)
                if received is serve_state.SHUTDOWN:
                    return
                if received is serve_state.HEARTBEAT:
                    yield pytilpack.sse.SSE(event="heartbeat", data="{}")
                    continue
                yield pytilpack.sse.SSE(data=typing.cast("str", received))
        finally:
            await unsubscribe(queue)

    return quart.Response(
        generate(),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-store", "Connection": "keep-alive"},
    )


def _plan_request_target(context: serve_plans.PlansContext) -> tuple[str, str, str]:
    """`/api/plans/file`・`/api/plans/raw`共通でhost・source・pathを取り出して検証する。

    `host`未指定時はローカルホストを採用する。許可リスト外のhostは入力エラーとして拒否する
    （サーバーが外部へ公開された場合に、クライアントが任意のSSH先へ接続試行を誘発できないようにするため）。
    """
    rel = quart.request.args.get("path")
    if not rel:
        raise _wi_operations.WebApiInputError("pathを指定してください")
    host = quart.request.args.get("host")
    if host is None:
        host = context.hostname
    if host != context.hostname and host not in context.allowed_remote_hosts:
        raise _wi_operations.WebApiInputError("未知のhostです")
    requested_source = quart.request.args.get("source") or quart.request.args.get("source_id") or ""
    source_id = serve_plans.resolve_source_id(context, host, requested_source, rel)
    if source_id is None:
        raise _wi_operations.WebApiInputError("sourceを解決できません")
    return host, source_id, rel


def register_session_routes(
    app: quart.Quart,
    context: serve_sessions.SessionsContext,
    plans_context: serve_plans.PlansContext,
) -> None:
    """セッション画面のページ・資産・APIのルートを登録する。"""

    @app.get("/sessions")
    async def sessions_index() -> quart.Response:
        base_path = safe_base_path(quart.request.root_path)
        body = render_index(plans_context, base_path, "sessions")
        return no_store(body, "text/html; charset=utf-8")

    @app.get("/api/sessions/list")
    async def sessions_list() -> quart.Response:
        entries, warnings = await serve_sessions.list_sessions(context)
        roots = [str(context.claude_home / "projects"), str(context.codex_home / "sessions")]
        return json_no_store({"sessions": [entry.to_json() for entry in entries], "warnings": warnings, "roots": roots})

    @app.get("/api/sessions/detail")
    async def sessions_detail() -> quart.Response:
        unknown = set(quart.request.args) - {"host", "engine", "path"}
        if unknown:
            raise _wi_operations.WebApiInputError(f"未知のqueryです: {', '.join(sorted(unknown))}")
        engine = quart.request.args.get("engine", "")
        path = quart.request.args.get("path", "")
        host = quart.request.args.get("host") or context.hostname
        if not engine or not path:
            raise _wi_operations.WebApiInputError("engineとpathを指定してください")
        try:
            detail = await serve_sessions.session_detail(context, engine, host, path)
        except serve_sessions.SessionNotFoundError as error:
            raise FileNotFoundError(str(error)) from error
        return json_no_store(_with_assistant_html(detail))

    @app.get("/api/sessions/host-status")
    async def sessions_host_status() -> quart.Response:
        return json_no_store({"hosts": await serve_sessions.host_status(context)})

    @app.get("/api/sessions/events")
    async def sessions_events() -> quart.Response:
        return _subscription_stream(
            functools.partial(serve_sessions.subscribe, context.state),
            functools.partial(serve_sessions.unsubscribe, context.state),
        )
