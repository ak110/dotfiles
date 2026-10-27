"""`atk serve`の起動処理。"""

import asyncio
import contextlib
import logging
import pathlib
import signal
import subprocess

# ヘルプや他の処理で使わない依存は、使用時まで読み込まない。
lazy import hypercorn.asyncio
lazy import hypercorn.config

from agent_toolkit._common import console_title as _console_title
from agent_toolkit._common import next_action as _next_action
lazy from agent_toolkit._atk.serve import app as _atk_serve_app
lazy from agent_toolkit._atk.serve import config as _atk_serve_config
lazy from agent_toolkit._atk.serve import state as _atk_serve_state
lazy from agent_toolkit._atk.wi import sync as _wi_sync

logger = logging.getLogger(__name__)


def show_logs(*, follow: bool = False) -> int:
    """Atk serveのuser serviceに属するjournalを表示する。"""
    command = [
        "journalctl",
        "--user",
        "_SYSTEMD_USER_UNIT=atk-serve.service",
        "+",
        "USER_UNIT=atk-serve.service",
        "+",
        "SYSLOG_IDENTIFIER=atk-serve-setup",
        "-n",
        "100",
    ]
    if follow:
        command.append("-f")
    try:
        return subprocess.run(command, check=False).returncode
    except OSError as error:
        _next_action.report(
            f"atk serveのログを開けません: {error}",
            next_action=(
                "ログの表示にはsystemdのuser環境（journalctl）が必要。"
                "使えない環境では`atk serve`を前景で起動し、標準エラーへ出るログを読む"
            ),
        )
        return 1


async def _serve(private_notes: pathlib.Path, config: _atk_serve_config.ServeConfig) -> None:
    """Quartアプリをhypercornで起動し、シグナル受信でgraceful shutdownする。

    シグナル（SIGINT・SIGTERM・SIGHUP）を単一の`shutdown_trigger`へ集約する。
    hypercornが標準で行うシグナル処理はSIGHUPを含まないため、プロセス監視ツール等からの
    SIGHUP到達時も購読中のSSE接続を片付けてから終了できるようにする。
    """
    async with _atk_serve_state.ServeState(private_notes) as state:
        await _serve_with_state(private_notes, config, state)


async def _serve_with_state(
    private_notes: pathlib.Path, config: _atk_serve_config.ServeConfig, state: _atk_serve_state.ServeState
) -> None:
    """所有済みの監視状態の内側で設定・アプリの作成から配信までを行う。"""
    app = await _atk_serve_app.create_app_async(private_notes, config, state)
    hypercorn_config = hypercorn.config.Config()
    hypercorn_config.bind = [f"{config.host}:{config.port}"]
    # アクセスログは常駐運用で情報価値が低いため出力しない。
    hypercorn_config.accesslog = None
    # hypercornは停止時に`server.wait_closed()`で全接続の終了を待ってから本値を適用する。
    # 停止要求（`ServeState.request_shutdown`）で、アプリのASGI呼び出しが処理中の要求を打ち切り、
    # SSEの生成処理が応答を完了し、スレッドの走査と常駐接続も止まるため、接続が残っていても本値の範囲で停止する。
    # 終了要求後の体感遅延を1秒以内へ抑える。
    hypercorn_config.graceful_timeout = 1.0

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def request_shutdown(signal_name: str) -> None:
        logger.info("atk serveの停止シグナルを受信しました: %s", signal_name)
        state.request_shutdown()
        shutdown_event.set()

    for signal_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        received = getattr(signal, signal_name, None)
        if received is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(received, request_shutdown, signal_name)

    async def shutdown_trigger() -> None:
        await shutdown_event.wait()

    await hypercorn.asyncio.serve(app, hypercorn_config, shutdown_trigger=shutdown_trigger)


def build_console_title() -> str:
    """起動ターミナルのウィンドウタイトル文字列を返す。

    待受ポートなどの起動ごとに変わる値を含めず、コマンド名だけの固定値とする。
    """
    return "atk serve"


def run(*, host: str | None = None, port: int | None = None, home: pathlib.Path | None = None) -> None:
    """設定と環境を解決してWebサーバーを起動する。"""
    # format: 日時・ロガー名・レベルを付与し、運用中の診断で発生時刻と出力元を特定できるようにする。
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
    private_notes = _wi_sync.ensure_environment(resolved_home)
    config = _atk_serve_config.resolve_config(host=host, port=port)
    logger.info("atk serveを http://%s:%s/ で配信します", config.host, config.port)
    with _console_title.console_title(build_console_title()):
        asyncio.run(_serve(private_notes, config))
