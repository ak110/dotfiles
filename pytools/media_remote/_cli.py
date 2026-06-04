"""コマンドライン引数解析とエントリポイント。"""

import argparse
import asyncio
import contextlib
import io
import logging
import os
import pathlib
import socket
import sys
from urllib.parse import quote

import hypercorn.asyncio
import hypercorn.config
import qrcode

from pytools._internal.cli import enable_completion
from pytools.media_remote import _app, _token

logger = logging.getLogger(__name__)

DEFAULT_HOST = "0.0.0.0"  # noqa: S104  LAN内スマホからアクセスさせる用途のため0.0.0.0で公開する。
DEFAULT_PORT = 29123


def default_pid_path() -> pathlib.Path:
    r"""既定のPIDファイル保存先（`%LOCALAPPDATA%\\dotfiles\\media-remote\\pid`）。"""
    local = os.environ.get("LOCALAPPDATA")
    base = pathlib.Path(local) if local else pathlib.Path.home() / "AppData" / "Local"
    return base / "dotfiles" / "media-remote" / "pid"


def detect_local_ip() -> str:
    """LAN内のローカルIPアドレスをUDPソケット経由で推定する。

    `connect`は実通信を行わずカーネルのルーティング選択結果だけを取得するため
    オフライン環境でも動作するが、`OSError`が発生した場合は`127.0.0.1`を返す。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def build_access_url(host: str, port: int, token: str) -> str:
    """スマホでアクセスするURLを組み立てる。"""
    return f"http://{host}:{port}/?t={quote(token, safe='')}"


def render_qr_ansi(text: str) -> str:
    """ANSIブロック文字でQRコードを描画した文字列を返す。"""
    qr = qrcode.QRCode(border=1)
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.StringIO()
    qr.print_ascii(out=buf, invert=True)
    return buf.getvalue()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dotfiles-media-remote",
        description="LAN内のスマホからWindowsへメディアキーを送信するPWAリモコン。",
    )
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="HTTPサーバーを起動する（Windows専用）")
    serve.add_argument("--host", default=DEFAULT_HOST, help=f"bindアドレス（既定: {DEFAULT_HOST}）")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"ポート（既定: {DEFAULT_PORT}）")
    serve.add_argument(
        "--token-file",
        type=pathlib.Path,
        default=None,
        help="トークン保存先パス（既定: %%LOCALAPPDATA%%/dotfiles/media-remote/token.txt）",
    )

    url_cmd = sub.add_parser("url", help="アクセスURLとQRコードを表示する")
    url_cmd.add_argument("--host", default=None, help="表示するホスト名/IP（既定: ローカルIP自動検出）")
    url_cmd.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"ポート（既定: {DEFAULT_PORT}）")
    url_cmd.add_argument(
        "--token-file",
        type=pathlib.Path,
        default=None,
        help="トークン保存先パス（既定: %%LOCALAPPDATA%%/dotfiles/media-remote/token.txt）",
    )

    enable_completion(parser)
    return parser


async def _serve(app: object, host: str, port: int) -> None:
    config = hypercorn.config.Config()
    config.bind = [f"{host}:{port}"]
    config.accesslog = None
    await hypercorn.asyncio.serve(app, config)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def _serve_command(args: argparse.Namespace) -> int:
    if sys.platform != "win32":
        logger.error("serveサブコマンドはWindows専用です（現在: %s）", sys.platform)
        return 1
    token_path = args.token_file if args.token_file is not None else _token.default_token_path()
    token = _token.load_or_create_token(token_path)
    pid_path = default_pid_path()
    _write_pid(pid_path)
    try:
        app = _app.create_app(token)
        logger.info("Serving media remote at http://%s:%s/", args.host, args.port)
        asyncio.run(_serve(app, args.host, args.port))
    finally:
        with contextlib.suppress(OSError):
            pid_path.unlink()
    return 0


def _write_pid(pid_path: pathlib.Path) -> None:
    pid_path.parent.mkdir(parents=True, exist_ok=True)
    pid_path.write_text(str(os.getpid()), encoding="utf-8")


def _url_command(args: argparse.Namespace) -> int:
    token_path = args.token_file if args.token_file is not None else _token.default_token_path()
    token = _token.load_or_create_token(token_path)
    host = args.host if args.host else detect_local_ip()
    url = build_access_url(host, args.port, token)
    print(url)
    print()
    print(render_qr_ansi(url))
    return 0


def main(argv: list[str] | None = None) -> int:
    """エントリポイント。

    `pyproject.toml`の`[project.scripts]`から
    `dotfiles-media-remote = "pytools.media_remote:main"`の形で参照される。
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        # サブコマンド省略時はserveの既定引数で起動する（スタートアップ自動起動経路と整合させる）。
        args = argparse.Namespace(command="serve", host=DEFAULT_HOST, port=DEFAULT_PORT, token_file=None)
    if args.command == "serve":
        return _serve_command(args)
    if args.command == "url":
        return _url_command(args)
    parser.print_help()
    return 1
