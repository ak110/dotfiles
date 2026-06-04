"""Quartアプリ生成。"""

import json
from collections.abc import Callable

import quart

from pytools.media_remote import _assets, _keys

# Cookie名・寿命。`HttpOnly` + `SameSite=Lax`で他オリジン経路からの参照を抑える。
COOKIE_NAME = "mrt"
COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def create_app(token: str, send_key: Callable[[str], None] | None = None) -> quart.Quart:
    """メディアリモコンのQuartアプリを生成する。

    `send_key`を差し替えるとテストやドライランで実キー送出を回避できる。
    `None`のとき`_keys.send_key`を使う。
    """
    app = quart.Quart(__name__)
    actual_send_key = send_key if send_key is not None else _keys.send_key

    @app.before_request
    async def _auth() -> quart.Response | None:
        # `/icon.svg`等の静的資産も含めて全経路をトークン認証配下に置く。
        # クエリ`?t=`はホーム画面追加用の初回URL、Cookie`mrt`はその後の常用経路。
        provided = quart.request.args.get("t") or quart.request.cookies.get(COOKIE_NAME)
        if provided != token:
            return quart.Response("forbidden", status=401)
        return None

    @app.get("/")
    async def index() -> quart.Response:
        response = quart.Response(
            _assets.INDEX_HTML,
            content_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
        # クエリトークン経由の初回アクセス時にだけCookieを発行し、以降の起動はCookieだけで通す。
        if quart.request.args.get("t") == token:
            response.set_cookie(
                COOKIE_NAME,
                token,
                max_age=COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
            )
        return response

    @app.get("/manifest.json")
    async def manifest() -> quart.Response:
        body = json.dumps(_assets.build_manifest(), ensure_ascii=False)
        return quart.Response(
            body,
            content_type="application/manifest+json; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/icon.svg")
    async def icon() -> quart.Response:
        return quart.Response(
            _assets.ICON_SVG,
            content_type="image/svg+xml; charset=utf-8",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @app.post("/api/key/<name>")
    async def api_key(name: str) -> quart.Response:
        if name not in _keys.VK_CODES:
            return quart.Response("unknown key", status=404)
        actual_send_key(name)
        return quart.Response(status=204)

    return app
