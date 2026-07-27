"""`atk serve`の設定を解決する。"""

import dataclasses
import logging
import os
import pathlib
import sys
import tomllib

import platformdirs

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
# IDEのリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、
# Windowsローカル実行時に既定値が衝突する。Windowsのみ別値へずらして回避する。
LINUX_DEFAULT_PORT = 28766
WINDOWS_DEFAULT_PORT = 28876


@dataclasses.dataclass(frozen=True)
class ServeConfig:
    """Webサーバーの解決済み設定。"""

    host: str
    port: int


def default_port(platform: str | None = None) -> int:
    """OS別の既定ポートを返す。"""
    return WINDOWS_DEFAULT_PORT if (platform or sys.platform) == "win32" else LINUX_DEFAULT_PORT


def default_config_path() -> pathlib.Path:
    r"""既定のTOML設定パスを返す。

    Linuxでは`~/.config/agent-toolkit/serve.toml`、
    Windowsでは`%LOCALAPPDATA%\agent-toolkit\serve.toml`になる。

    `appauthor=False`を渡すのは、未指定時にWindowsで`appname`が
    appauthorとしても付与され`%LOCALAPPDATA%\agent-toolkit\agent-toolkit\...`に
    なる挙動を回避するため。
    """
    return pathlib.Path(platformdirs.user_config_dir("agent-toolkit", appauthor=False)) / "serve.toml"


def resolve_config(
    *,
    host: str | None = None,
    port: int | None = None,
    environ: dict[str, str] | None = None,
    platform: str | None = None,
) -> ServeConfig:
    """CLI、環境変数、TOML、組み込み値の順で設定を解決する。"""
    env = dict(os.environ if environ is None else environ)
    config_path = pathlib.Path(env.get("AGENT_TOOLKIT_SERVE_CONFIG", default_config_path()))
    values: dict[str, object] = {}
    try:
        with config_path.open("rb") as stream:
            loaded = tomllib.load(stream)
    except FileNotFoundError:
        loaded = {}
    unknown = set(loaded) - {"host", "port"}
    if unknown:
        logger.warning("設定ファイルの未知キーを無視します: %s (%s)", ", ".join(sorted(unknown)), config_path)
    values.update({key: loaded[key] for key in ("host", "port") if key in loaded})
    if "AGENT_TOOLKIT_SERVE_HOST" in env:
        values["host"] = env["AGENT_TOOLKIT_SERVE_HOST"]
    if "AGENT_TOOLKIT_SERVE_PORT" in env:
        raw_port = env["AGENT_TOOLKIT_SERVE_PORT"]
        try:
            values["port"] = int(raw_port)
        except ValueError as error:
            raise ValueError("AGENT_TOOLKIT_SERVE_PORTは整数で指定してください") from error
    if host is not None:
        values["host"] = host
    if port is not None:
        values["port"] = port
    resolved_host = values.get("host", DEFAULT_HOST)
    resolved_port = values.get("port", default_port(platform))
    if not isinstance(resolved_host, str) or not resolved_host.strip():
        raise ValueError("hostは空でない文字列で指定してください")
    if not isinstance(resolved_port, int) or isinstance(resolved_port, bool) or not 1 <= resolved_port <= 65535:
        raise ValueError("portは1から65535までの整数で指定してください")
    return ServeConfig(resolved_host, resolved_port)
