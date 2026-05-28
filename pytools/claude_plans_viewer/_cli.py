"""コマンドライン引数解析とエントリーポイント。"""

import argparse
import asyncio
import contextlib
import logging
import os
import pathlib
import signal
import stat
import sys
from typing import Any

import hypercorn.asyncio
import hypercorn.config
import quart
import watchdog.observers

from pytools._internal.cli import enable_completion
from pytools.claude_plans_viewer import _app, _config, _console_title, _local, _state

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

    解決の優先順位は「CLI引数 > 環境変数 > 設定ファイル > 組み込み既定値」。
    設定ファイルは`_config.load_config()`が返すsnake_case辞書を採用する。
    TOML構文エラーは`_config.load_config()`が`ValueError`を送出する。
    """
    parser = argparse.ArgumentParser(
        description="Serve ~/.claude/plans Markdown via local HTTP.",
        epilog=(f"設定ファイル: 既定 {_config.default_config_path()}、環境変数 {_config.ENV_CONFIG} で上書き可。"),
    )
    parser.add_argument(
        "--root",
        default=os.environ.get(ENV_ROOT),
        help=(f"Markdownのルートディレクトリ（環境変数 {ENV_ROOT}、設定ファイルからも参照、既定: {DEFAULT_ROOT}）"),
    )
    parser.add_argument(
        "--host",
        default=os.environ.get(ENV_HOST),
        help=f"bindアドレス（環境変数 {ENV_HOST}、設定ファイルからも参照、既定: {DEFAULT_HOST}）",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=os.environ.get(ENV_PORT),
        help=f"ポート（環境変数 {ENV_PORT}、設定ファイルからも参照、既定: {DEFAULT_PORT}）",
    )
    parser.add_argument(
        "--remote-host",
        action="append",
        default=None,
        metavar="HOST",
        help=(
            f"SSH経由で監視するリモートホスト（複数指定可、`user@host`形式可、"
            f"環境変数 {ENV_REMOTE_HOSTS} はコロン区切り、設定ファイルからも参照）"
        ),
    )
    enable_completion(parser)
    args = parser.parse_args(argv)
    config = _config.load_config()
    _resolve_defaults(args, config)
    return args


def _resolve_defaults(args: argparse.Namespace, config: dict[str, Any]) -> None:
    """`args`のうち未設定の項目を「設定ファイル → 組み込み既定値」の順で補完する。

    `argparse`の`default`には環境変数の値だけを渡しているため、CLI引数・環境変数の
    いずれも未指定だった項目は`None`で残る。本関数はその欠落を設定ファイル値で
    補い、最後に組み込み既定値へフォールバックする。`--port`はCLI引数経由なら
    `argparse`の`type=int`で`int`化済みだが、環境変数経由（`str`）と
    設定ファイル経由（TOMLの`int`）双方の経路を統一するため最終段階でも
    `int()`を適用する。
    """
    if args.root is None:
        args.root = config.get("root", DEFAULT_ROOT)
    if args.host is None:
        args.host = config.get("host", DEFAULT_HOST)
    if args.port is None:
        args.port = config.get("port", DEFAULT_PORT)
    args.port = int(args.port)
    args.remote_host = _resolve_remote_hosts(args.remote_host, config.get("remote_hosts"))


def _resolve_remote_hosts(cli_value: list[str] | None, config_value: Any) -> list[str]:
    """`--remote-host`の最終値を解決する。

    `action="append"`はCLI未指定時にNone固定となるため、ここで
    「環境変数（コロン区切り）→ 設定ファイル（リスト）→ 空リスト」の順に解決する。
    """
    if cli_value is not None:
        return cli_value
    env_hosts = os.environ.get(ENV_REMOTE_HOSTS, "")
    if env_hosts:
        return [h for h in env_hosts.split(":") if h]
    if isinstance(config_value, list):
        return [str(h) for h in config_value]
    return []


async def serve(app: quart.Quart, host: str, port: int) -> None:
    """hypercornでQuartアプリを起動する。

    シグナル（SIGINT/SIGTERM/SIGHUP）とstdin EOF（非PTY SSH切断検知）を
    単一の`shutdown_trigger`に集約し、SSE接続中の体感遅延を抑えるため
    `graceful_timeout`を1.0秒へ短縮する。
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

    対象はstdinがFIFO（パイプ）のときのみ。TTY（対話起動）やCHR（`/dev/null`等
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


def build_console_title(port: int, remote_hosts: list[str]) -> str:
    """起動ターミナルのウィンドウタイトル文字列を組み立てる。

    リモートホストは起動ログと同じ`", ".join(...)`表記で列挙する。
    """
    title = f"claude-plans-viewer :{port}"
    if remote_hosts:
        title += f" ({', '.join(remote_hosts)})"
    return title


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。

    `pyproject.toml`の`[project.scripts]`から
    `claude-plans-viewer = "pytools.claude_plans_viewer:main"`の形で参照される。
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # hypercornは`hypercorn.error`に独自フォーマット付きハンドラーを設定するが、
    # `propagate`は既定でTrueのためrootの`basicConfig`ハンドラーへも伝搬し二重出力になる。
    # hypercorn側のフォーマット（タイムスタンプ・PID付き）を活かすため、伝搬を止める。
    logging.getLogger("hypercorn.error").propagate = False
    try:
        args = parse_args(argv)
    except ValueError as e:
        logger.error("設定エラー: %s", e)
        return 1
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
        with _console_title.console_title(build_console_title(args.port, args.remote_host)):
            asyncio.run(serve(app, args.host, args.port))
    finally:
        observer.stop()
        observer.join()
    return 0
