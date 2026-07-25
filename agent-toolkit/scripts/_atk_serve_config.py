"""`atk serve`の設定を解決する。"""

import dataclasses
import os
import pathlib
import sys
import tomllib
import warnings

import platformdirs

DEFAULT_HOST = "127.0.0.1"
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
    """既定のTOML設定パスを返す。"""
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
        warnings.warn(f"未知の設定キーを無視します: {', '.join(sorted(unknown))}", stacklevel=2)
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
