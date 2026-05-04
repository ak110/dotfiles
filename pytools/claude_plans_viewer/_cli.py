"""コマンドライン引数解析とエントリーポイント。

`_main`は`pyproject.toml`の`[project.scripts]`から参照される公開エントリーポイント。
historicalにunderscore接頭辞付きで配信されているため互換のためそのまま残す。
それ以外のヘルパーは同一パッケージ内の参照のみのためunderscore接頭辞を付けない。
"""

import argparse
import asyncio
import contextlib
import logging
import os
import pathlib
import signal
import stat
import sys

import hypercorn.asyncio
import hypercorn.config
import quart
import watchdog.observers

from pytools._internal.cli import enable_completion
from pytools.claude_plans_viewer import _app, _local, _state

logger = logging.getLogger(__name__)

DEFAULT_ROOT = "~/.claude/plans"
DEFAULT_HOST = "127.0.0.1"
# VSCodeリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、
# Windowsローカル実行時に既定値が衝突する。Windowsのみ別値へずらして回避する。
DEFAULT_PORT = 28875 if sys.platform == "win32" else 28765

ENV_ROOT = "CLAUDE_PLANS_VIEWER_ROOT"
ENV_HOST = "CLAUDE_PLANS_VIEWER_HOST"
ENV_PORT = "CLAUDE_PLANS_VIEWER_PORT"
ENV_REMOTE_HOSTS = "CLAUDE_PLANS_VIEWER_REMOTE_HOSTS"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """コマンドライン引数を解析する。

    各オプションの既定値は環境変数経由で解決する。
    優先順位は CLI引数 > 環境変数 > 組み込み既定値。
    """
    parser = argparse.ArgumentParser(description="Serve ~/.claude/plans Markdown via local HTTP.")
    parser.add_argument(
        "--root",
        default=os.environ.get(ENV_ROOT, DEFAULT_ROOT),
        help=f"Markdownのルートディレクトリ（環境変数 {ENV_ROOT}、既定: {DEFAULT_ROOT}）",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get(ENV_HOST, DEFAULT_HOST),
        help=f"bindアドレス（環境変数 {ENV_HOST}、既定: {DEFAULT_HOST}）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get(ENV_PORT, DEFAULT_PORT)),
        help=f"ポート（環境変数 {ENV_PORT}、既定: {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--remote-host",
        action="append",
        default=None,
        metavar="HOST",
        help=(f"SSH経由で監視するリモートホスト（複数指定可、`user@host`形式可、環境変数 {ENV_REMOTE_HOSTS} はコロン区切り）"),
    )
    enable_completion(parser)
    args = parser.parse_args(argv)
    # `action="append"`はCLI未指定時にNone固定のため、ここで環境変数→既定値の順に解決する。
    if args.remote_host is None:
        env_hosts = os.environ.get(ENV_REMOTE_HOSTS, "")
        args.remote_host = [h for h in env_hosts.split(":") if h] if env_hosts else []
    return args


async def serve(app: quart.Quart, host: str, port: int) -> None:
    """hypercornでQuartアプリを起動する。

    シグナル（SIGINT/SIGTERM/SIGHUP）とstdin EOF（非PTY SSH切断検知）を
    単一のshutdown_triggerに集約し、SSE接続中の体感遅延を抑えるため
    graceful_timeoutを1.0秒へ短縮する。
    """
    config = hypercorn.config.Config()
    config.bind = [f"{host}:{port}"]
    # アクセスログの標準出力抑制（既存実装の`log_message`抑制に相当）。
    config.accesslog = None
    # SSE generatorは`CancelledError`を捕捉して`finally`で`unsubscribe`するため、
    # 短時間で打ち切ってもデータ整合性は保たれる。Ctrl+C後の体感遅延を1秒以内へ抑える。
    config.graceful_timeout = 1.0

    shutdown_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    # hypercorn既定のsignal処理はSIGINT/SIGTERM/SIGBREAKのみでSIGHUPを含まない。
    # remote-plans経由の主経路はstdin EOF監視だが、プロセス監視ツール等からSIGHUPが
    # 到達した場合にもgraceful shutdownできるよう、3種を統一のshutdown_triggerに集約する。
    for sig_name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, shutdown_event.set)

    stdin_task = asyncio.create_task(watch_stdin_eof(shutdown_event))

    async def shutdown_trigger() -> None:
        await shutdown_event.wait()

    try:
        await hypercorn.asyncio.serve(app, config, shutdown_trigger=shutdown_trigger)
    finally:
        stdin_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await stdin_task


async def watch_stdin_eof(shutdown_event: asyncio.Event) -> None:
    """SSH経由（非PTY）で起動された場合の切断検知。

    OpenSSHのsshdは非PTYセッションのチャンネル閉鎖時に子プロセスへSIGHUPを送らないため
    （`session.c`の`session_close_by_channel`はPTYのときのみ`session_pty_cleanup`を呼ぶ）、
    代替としてsshdがリモートコマンドのstdinに割り当てるパイプのEOFを監視する。

    対象はstdinがFIFO（パイプ）のときのみ。TTY（対話起動）やCHR（`/dev/null`等の
    バックグラウンド起動）では誤発火を避けるため早期returnする。
    """
    try:
        st = os.fstat(sys.stdin.fileno())
    except (OSError, AttributeError, ValueError):
        return
    if not stat.S_ISFIFO(st.st_mode):
        return
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    try:
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
    except (OSError, ValueError, NotImplementedError):
        return
    try:
        while await reader.read(1024):
            pass
    finally:
        shutdown_event.set()


def _main(argv: list[str] | None = None) -> int:
    """エントリーポイント。

    `pyproject.toml`の`[project.scripts]`から
    `claude-plans-viewer = "pytools.claude_plans_viewer:_main"`の形で参照されるため、
    関数名はunderscore付きのまま維持する（変更すると配布物との互換が破綻する）。
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # hypercornは`hypercorn.error`に独自フォーマット付きハンドラーを設定するが、
    # `propagate`は既定でTrueのためrootの`basicConfig`ハンドラーへも伝搬し二重出力になる。
    # hypercorn側のフォーマット（タイムスタンプ・PID付き）を活かすため、伝搬を止める。
    logging.getLogger("hypercorn.error").propagate = False
    args = parse_args(argv)
    root = pathlib.Path(args.root).expanduser().resolve()
    if not root.is_dir():
        logger.error("ディレクトリが見つかりません: %s", root)
        return 1

    try:
        app = _app.create_app(
            root,
            remote_hosts=args.remote_host,
        )
    except ValueError as e:
        logger.error("設定エラー: %s", e)
        return 1
    state: _state.BroadcastState = app.config["PLANS_STATE"]

    observer = watchdog.observers.Observer()
    observer.schedule(_local.PlansEventHandler(root, state), str(root), recursive=True)
    observer.start()
    try:
        logger.info("Serving %s at http://%s:%s/", root, args.host, args.port)
        if args.remote_host:
            logger.info("Remote hosts: %s (watchdog)", ", ".join(args.remote_host))
        asyncio.run(serve(app, args.host, args.port))
    finally:
        observer.stop()
        observer.join()
    return 0
