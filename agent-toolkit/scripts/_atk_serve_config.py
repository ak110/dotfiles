"""`atk serve`の設定を解決する。

フィードバック・計画ファイル・セッションの3画面が必要とする値を同じ`serve.toml`から解決する。
"""

import dataclasses
import logging
import os
import pathlib
import sys
import tomllib
import typing

import platformdirs

logger = logging.getLogger(__name__)

DEFAULT_HOST = "127.0.0.1"
# IDEのリモート開発拡張はLinux側の待受ポートをWindows側へ自動転送するため、
# Windowsローカル実行時に既定値が衝突する。Windowsのみ別値へずらして回避する。
LINUX_DEFAULT_PORT = 28766
WINDOWS_DEFAULT_PORT = 28876

_TOP_LEVEL_KEYS = ("host", "port")
_PLANS_KEYS = ("root", "remote_hosts")
_SESSIONS_KEYS = ("claude_home", "codex_home", "remote_hosts")


@dataclasses.dataclass(frozen=True)
class PlansConfig:
    """計画ファイル画面の参照元。"""

    # 未指定時はprivate-notes配下のplansと`~/.claude/plans`の2rootを用いる。
    root: str | None = None
    remote_hosts: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class SessionsConfig:
    """セッション画面の参照元。"""

    # 未指定時はそれぞれ`~/.claude`と、空でない`CODEX_HOME`または`~/.codex`を用いる。
    claude_home: str | None = None
    codex_home: str | None = None
    remote_hosts: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class ServeConfig:
    """Webサーバーの解決済み設定。"""

    host: str
    port: int
    plans: PlansConfig = dataclasses.field(default_factory=PlansConfig)
    sessions: SessionsConfig = dataclasses.field(default_factory=SessionsConfig)


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


def _warn_unknown(known: typing.Iterable[str], section: dict[str, typing.Any], config_path: pathlib.Path) -> None:
    """未知キーを警告して無視する。"""
    unknown = set(section) - set(known)
    if unknown:
        logger.warning("設定ファイルの未知キーを無視します: %s (%s)", ", ".join(sorted(unknown)), config_path)


def _string_or_none(section: dict[str, typing.Any], key: str, label: str, config_path: pathlib.Path) -> str | None:
    """文字列として受理できる値だけを返し、それ以外は警告して無視する。"""
    if key not in section:
        return None
    value = section[key]
    if isinstance(value, str) and value.strip():
        return value
    logger.warning("設定ファイルの%s.%sは空でない文字列で指定してください (%s)", label, key, config_path)
    return None


def _hosts(section: dict[str, typing.Any], label: str, config_path: pathlib.Path) -> tuple[str, ...]:
    """ホスト名の配列として受理できる値だけを返し、それ以外は警告して無視する。"""
    if "remote_hosts" not in section:
        return ()
    value = section["remote_hosts"]
    if isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
        return tuple(value)
    logger.warning("設定ファイルの%s.remote_hostsは文字列の配列で指定してください (%s)", label, config_path)
    return ()


def _section(loaded: dict[str, typing.Any], name: str, config_path: pathlib.Path) -> dict[str, typing.Any]:
    """テーブルとして受理できる節だけを返し、それ以外は警告して空として扱う。"""
    if name not in loaded:
        return {}
    value = loaded[name]
    if isinstance(value, dict):
        return value
    logger.warning("設定ファイルの%sはテーブルで指定してください (%s)", name, config_path)
    return {}


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
    _warn_unknown((*_TOP_LEVEL_KEYS, "plans", "sessions"), loaded, config_path)
    values.update({key: loaded[key] for key in _TOP_LEVEL_KEYS if key in loaded})
    plans_section = _section(loaded, "plans", config_path)
    sessions_section = _section(loaded, "sessions", config_path)
    _warn_unknown(_PLANS_KEYS, plans_section, config_path)
    _warn_unknown(_SESSIONS_KEYS, sessions_section, config_path)
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
    return ServeConfig(
        resolved_host,
        resolved_port,
        PlansConfig(
            root=_string_or_none(plans_section, "root", "plans", config_path),
            remote_hosts=_hosts(plans_section, "plans", config_path),
        ),
        SessionsConfig(
            claude_home=_string_or_none(sessions_section, "claude_home", "sessions", config_path),
            codex_home=_string_or_none(sessions_section, "codex_home", "sessions", config_path),
            remote_hosts=_hosts(sessions_section, "sessions", config_path),
        ),
    )
