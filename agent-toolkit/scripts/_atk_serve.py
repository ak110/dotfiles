"""`atk serve`の起動処理。"""

import asyncio
import contextlib
import logging
import pathlib
import signal

import _atk_serve_app
import _atk_serve_config
import _atk_serve_state
import _atk_wi_common as common
import _console_title
import hypercorn.asyncio
import hypercorn.config

logger = logging.getLogger(__name__)


async def _serve(private_notes: pathlib.Path, config: _atk_serve_config.ServeConfig) -> None:
    """Quartアプリをhypercornで起動し、シグナル受信でgraceful shutdownする。

    シグナル（SIGINT・SIGTERM・SIGHUP）を単一の`shutdown_trigger`へ集約する。
    hypercorn既定のシグナル処理はSIGHUPを含まないため、プロセス監視ツール等からの
    SIGHUP到達時も購読中のSSE接続を片付けてから終了できるようにする。
    """
    state = _atk_serve_state.ServeState(private_notes)
    state.start(asyncio.get_running_loop())
    app = _atk_serve_app.create_app(private_notes, config, state)
    hypercorn_config = hypercorn.config.Config()
    hypercorn_config.bind = [f"{config.host}:{config.port}"]
    # アクセスログは常駐運用で情報価値が低いため出力しない。
    hypercorn_config.accesslog = None
    # SSEのgeneratorは`CancelledError`を捕捉して購読解除まで完了するため、
    # 短時間で打ち切っても整合性は保たれる。終了要求後の体感遅延を1秒以内へ抑える。
    hypercorn_config.graceful_timeout = 1.0

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signal_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        received = getattr(signal, signal_name, None)
        if received is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(received, shutdown_event.set)

    async def shutdown_trigger() -> None:
        await shutdown_event.wait()

    try:
        await hypercorn.asyncio.serve(app, hypercorn_config, shutdown_trigger=shutdown_trigger)
    finally:
        state.stop()


def build_console_title(port: int) -> str:
    """起動ターミナルのウィンドウタイトル文字列を組み立てる。"""
    return f"atk serve :{port}"


def run(*, host: str | None = None, port: int | None = None, home: pathlib.Path | None = None) -> None:
    """設定と環境を解決してWebサーバーを起動する。"""
    # format: 日時・ロガー名・レベルを付与して運用診断性を向上させる。
    # force=True: 既存のログハンドラーが設定済みの場合も本ツールの書式を適用するため。
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        force=True,
    )
    # hypercornは`hypercorn.error`へ独自書式のハンドラーを設定するが`propagate`が真のため、
    # rootのハンドラーへも伝搬して二重出力になる。hypercorn側の書式を活かすため伝搬を止める。
    logging.getLogger("hypercorn.error").propagate = False
    resolved_home = pathlib.Path.home() if home is None else home
    private_notes = common.ensure_environment(resolved_home)
    config = _atk_serve_config.resolve_config(host=host, port=port)
    logger.info("WI管理Web UIを http://%s:%s/ で配信します", config.host, config.port)
    with _console_title.console_title(build_console_title(config.port)):
        asyncio.run(_serve(private_notes, config))
