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

from pytools._internal import claude_common
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

    doctor = sub.add_parser(
        "doctor",
        help="listen状態・FW規則・プロファイル・URL等を一括診断する（Windows専用）",
    )
    doctor.add_argument("--host", default=None, help="アクセスURL表示用ホスト名/IP（既定: ローカルIP自動検出）")
    doctor.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"ポート（既定: {DEFAULT_PORT}）")
    doctor.add_argument(
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


def _ps(script: str) -> tuple[str, int]:
    """PowerShellを実行して`(stdout, returncode)`を返す。失敗時は`("", -1)`。"""
    result = claude_common.run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=30.0,
        tag="media-remote-doctor",
    )
    if result is None:
        return "", -1
    return result.stdout, result.returncode


def _section(title: str) -> str:
    return f"== {title} =="


def _doctor_listen() -> tuple[str, bool]:
    """listen状態セクションを描画し、listen中か否かを返す。"""
    script = (
        f"$c = Get-NetTCPConnection -State Listen -LocalPort {DEFAULT_PORT} -ErrorAction SilentlyContinue; "
        "if ($c) { "
        "$c | ForEach-Object { "
        '[Console]::Out.WriteLine("$($_.LocalAddress):$($_.LocalPort) (PID=$($_.OwningProcess))") '
        "} } else { [Console]::Out.WriteLine('NONE') }"
    )
    stdout, _ = _ps(script)
    text = stdout.strip()
    listening = text not in ("", "NONE")
    body = text if listening else "未起動（ポート29123でlistenしていない）"
    return f"{_section('listen')}\n{body}\n", listening


def _doctor_process() -> str:
    """PIDファイル経由でプロセス情報を取得するセクションを描画する。"""
    pid_path = default_pid_path()
    if not pid_path.is_file():
        return f"{_section('process')}\nPIDファイル未存在: {pid_path}\n"
    pid_text = pid_path.read_text(encoding="utf-8").strip()
    script = (
        f'$p = Get-CimInstance Win32_Process -Filter "ProcessId={pid_text}" -ErrorAction SilentlyContinue; '
        "if ($p) { "
        '[Console]::Out.WriteLine("PID=$($p.ProcessId) CommandLine=$($p.CommandLine)") '
        "} else { [Console]::Out.WriteLine('NONE') }"
    )
    stdout, _ = _ps(script)
    body = stdout.strip()
    if not body or body == "NONE":
        body = f"PID={pid_text} のプロセスが見つからない（未稼働）"
    return f"{_section('process')}\n{body}\n"


def _doctor_profile() -> tuple[str, bool]:
    """ネットワークプロファイルセクションを描画し、Publicプロファイルがあるか返す。"""
    script = (
        'Get-NetConnectionProfile | ForEach-Object { [Console]::Out.WriteLine("$($_.InterfaceAlias): $($_.NetworkCategory)") }'
    )
    stdout, _ = _ps(script)
    body = stdout.strip() or "（取得不可）"
    has_public = "Public" in body
    return f"{_section('profile')}\n{body}\n", has_public


def _doctor_firewall() -> tuple[str, bool]:
    """FW規則セクションを描画し、規則の有無を返す。"""
    script = (
        "$r = Get-NetFirewallRule -DisplayName '*media-remote*' -ErrorAction SilentlyContinue; "
        "if ($r) { "
        "$r | ForEach-Object { "
        '[Console]::Out.WriteLine("$($_.DisplayName) [Enabled=$($_.Enabled) Action=$($_.Action) "+'
        '"Profile=$($_.Profile) Direction=$($_.Direction)]") '
        "} } else { [Console]::Out.WriteLine('NONE') }"
    )
    stdout, _ = _ps(script)
    text = stdout.strip()
    has_rule = text not in ("", "NONE")
    body = text if has_rule else "該当するFW規則なし"
    return f"{_section('firewall')}\n{body}\n", has_rule


def _doctor_url(args: argparse.Namespace) -> str:
    """アクセスURLセクションを描画する。"""
    token_path = args.token_file if args.token_file is not None else _token.default_token_path()
    token = _token.load_or_create_token(token_path)
    host = args.host if args.host else detect_local_ip()
    url = build_access_url(host, args.port, token)
    return f"{_section('url')}\n{url}\n"


def _doctor_recommendations(listening: bool, has_rule: bool, has_public: bool) -> str:
    """推奨修復コマンドセクションを描画する。"""
    lines: list[str] = []
    if not listening:
        lines.append(
            "- サーバー未起動。起動方法:\n"
            '    Start-Process "$env:USERPROFILE\\.local\\bin\\dotfiles-media-remote.exe" -ArgumentList "serve"'
        )
    if not has_rule:
        lines.append(
            "- FW規則未登録。管理者PowerShellで以下を実行:\n"
            "    New-NetFirewallRule -DisplayName 'dotfiles-media-remote' -Direction Inbound "
            f"-Action Allow -Protocol TCP -LocalPort {DEFAULT_PORT} -Profile Private"
        )
    if has_public:
        lines.append(
            "- Publicプロファイルのインターフェースあり。LANを信頼できるならPrivateへ変更:\n"
            "    Set-NetConnectionProfile -InterfaceAlias '<InterfaceAlias>' -NetworkCategory Private"
        )
    body = "\n".join(lines) if lines else "推奨事項なし（listen中・FW規則あり・全プロファイルPrivate）"
    return f"{_section('recommendations')}\n{body}\n"


def _doctor_command(args: argparse.Namespace) -> int:
    if sys.platform != "win32":
        logger.error("doctorサブコマンドはWindows専用です（現在: %s）", sys.platform)
        return 1
    sections: list[str] = []
    listen_text, listening = _doctor_listen()
    sections.append(listen_text)
    sections.append(_doctor_process())
    profile_text, has_public = _doctor_profile()
    sections.append(profile_text)
    fw_text, has_rule = _doctor_firewall()
    sections.append(fw_text)
    sections.append(_doctor_url(args))
    sections.append(_doctor_recommendations(listening, has_rule, has_public))
    print("\n".join(sections))
    return 0


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
    if args.command == "doctor":
        return _doctor_command(args)
    parser.print_help()
    return 1
