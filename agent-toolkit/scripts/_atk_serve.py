"""`atk serve`の起動処理。"""

import asyncio
import pathlib

import _atk_fb_common as common
import _atk_serve_app
import _atk_serve_config
import _atk_serve_state
import hypercorn.asyncio
import hypercorn.config


async def _serve(private_notes: pathlib.Path, config: _atk_serve_config.ServeConfig) -> None:
    state = _atk_serve_state.ServeState(private_notes)
    state.start(asyncio.get_running_loop())
    app = _atk_serve_app.create_app(private_notes, config, state)
    hypercorn_config = hypercorn.config.Config()
    hypercorn_config.bind = [f"{config.host}:{config.port}"]
    try:
        await hypercorn.asyncio.serve(app, hypercorn_config)
    finally:
        state.stop()


def run(*, host: str | None = None, port: int | None = None, home: pathlib.Path | None = None) -> None:
    """設定と環境を解決してWebサーバーを起動する。"""
    resolved_home = pathlib.Path.home() if home is None else home
    private_notes = common.ensure_environment(resolved_home)
    asyncio.run(_serve(private_notes, _atk_serve_config.resolve_config(host=host, port=port)))
