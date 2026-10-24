"""`atk serve`の設定を解決する。

AWI・計画ファイル・セッションの3画面が必要とする値を`atk config`の統一設定（`serve_*`キー）から解決する。
旧`serve.toml`が残っていれば、解決の前に統一設定へ移行する（`agent_toolkit._atk.config.migrate_serve_settings`）。
"""

import dataclasses
import os

from agent_toolkit._atk import config as _config


@dataclasses.dataclass(frozen=True)
class PlansConfig:
    """計画ファイル画面の参照元。"""

    # 未指定時はprivate-notes配下のplansと`~/.claude/plans`の2つのrootを用いる。
    root: str | None = None
    remote_hosts: tuple[str, ...] = ()


@dataclasses.dataclass(frozen=True)
class SessionsConfig:
    """セッション画面の参照元。"""

    # 未指定時はそれぞれClaude Codeの設定ディレクトリとCodexのホーム（`agent_toolkit._common.host_homes`）を用いる。
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


def _path_or_none(key: str) -> str | None:
    """パスの設定値を返す。空文字列は未指定として`None`を返す。"""
    return _config.resolve_mutable_setting(key) or None


def resolve_config(*, host: str | None = None, port: int | None = None) -> ServeConfig:
    """CLI、`AGENT_TOOLKIT_SERVE_HOST`・`AGENT_TOOLKIT_SERVE_PORT`、`atk config`の実効値の順で設定を解決する。

    `atk config`の実効値は環境変数`AGENT_TOOLKIT_CONFIG_SERVE_*`、保存値、省略時の値の順に決まる。
    """
    _config.migrate_serve_settings()
    resolved_host = host
    if resolved_host is None:
        resolved_host = os.environ.get("AGENT_TOOLKIT_SERVE_HOST") or _config.resolve_mutable_setting("serve_host")
    resolved_port = port
    if resolved_port is None:
        raw_port = os.environ.get("AGENT_TOOLKIT_SERVE_PORT")
        if raw_port is not None:
            try:
                resolved_port = int(raw_port)
            except ValueError as error:
                raise ValueError("AGENT_TOOLKIT_SERVE_PORTは整数で指定してください") from error
        else:
            resolved_port = int(_config.resolve_mutable_setting("serve_port"))
    if not isinstance(resolved_host, str) or not resolved_host.strip():
        raise ValueError("hostは空でない文字列で指定してください")
    if not isinstance(resolved_port, int) or isinstance(resolved_port, bool) or not 1 <= resolved_port <= 65535:
        raise ValueError("portは1から65535までの整数で指定してください")
    return ServeConfig(
        resolved_host,
        resolved_port,
        PlansConfig(
            root=_path_or_none("serve_plans_root"),
            remote_hosts=_config.parse_remote_hosts(_config.resolve_mutable_setting("serve_plans_remote_hosts")),
        ),
        SessionsConfig(
            claude_home=_path_or_none("serve_sessions_claude_home"),
            codex_home=_path_or_none("serve_sessions_codex_home"),
            remote_hosts=_config.parse_remote_hosts(_config.resolve_mutable_setting("serve_sessions_remote_hosts")),
        ),
    )
