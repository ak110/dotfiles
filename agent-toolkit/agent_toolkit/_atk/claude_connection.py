"""Claude会話の役割と利用可能な認証から、接続候補と隔離した環境を決める。"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from collections.abc import Mapping

from agent_toolkit._atk import config
from agent_toolkit._common import claude_connection as settings

AUTH_ENV = settings.AUTH_ENV
Connection = settings.Connection
clean_environment = settings.clean_environment
connection_environment = settings.connection_environment
connection_overrides = settings.connection_overrides
subscription_available = settings.subscription_available
isolation_keys = settings.isolation_keys


def api_only() -> bool:
    """旧設定による全役割のAPI固定を互換入力として判定する。"""
    return config.claude_legacy_api_only()


@dataclasses.dataclass(frozen=True)
class Candidates:
    """利用できる順序と、選択できなかった候補の復旧案内。"""

    connections: tuple[Connection, ...]
    diagnostics: tuple[str, ...] = ()

    @property
    def warnings(self) -> tuple[str, ...]:
        """起動を続ける場合にも明示する設定不良を返す。"""
        return tuple(message for message in self.diagnostics if message.startswith("API環境ファイル"))

    def require_first(self) -> Connection:
        """最初の候補を返し、全候補不成立なら復旧案内付きで終了する。"""
        if not self.connections:
            raise ValueError("Claude接続候補がありません。" + "; ".join(self.diagnostics))
        return self.connections[0]


def candidates(role: str, *, env: Mapping[str, str] | None = None) -> Candidates:
    """オーケストレーターの接続固定と、委譲先の接続順から利用可能な候補を返す。"""
    raw_path = config.resolve_mutable_setting("claude_api_env_file")
    strict_api = api_only()
    orchestrator_api = config.resolve_mutable_setting("claude_orchestrator_api_only") == "true"
    worker_api_first = config.resolve_mutable_setting("claude_worker_api_first") == "true"
    if not raw_path:
        if strict_api:
            return Candidates((), ("API: atk config set claude_api_env_file <パス>で認証環境を設定する",))
        return Candidates((Connection("inherit"),))
    path = str(pathlib.Path(raw_path).absolute()) if raw_path else None
    diagnostic: list[str] = []
    api = False
    if path:
        try:
            # 値を1つも持たないファイル（空、コメントと空行だけ）は認証を渡せないため、読めない場合と同じく候補から除く。
            api = bool(settings.read_api_env_file(path))
        except OSError, ValueError, UnicodeError:
            api = False
        if not api:
            diagnostic.append(
                f"API環境ファイルを使えません（空、存在しない、読み取れない、または形式が不正）: {path}。"
                "atk config set claude_api_env_file <パス>で修正するか、atk config set claude_api_env_file ''で解除する"
            )
    else:
        diagnostic.append("API: atk config set claude_api_env_file <パス>で認証環境を設定する")
    if strict_api:
        if api and path:
            values = settings.read_api_env_file(path)
            api = any(
                values.get(key)
                for key in (
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK",
                    "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY",
                )
            )
            if not api:
                diagnostic.append("API環境ファイルにAPI認証または接続方式を設定し、同じ会話へ再送する")
        return Candidates((Connection("api", path, path),) if api else (), tuple(diagnostic))
    subscription_env = env
    subscription_isolation_keys = AUTH_ENV
    if api:
        selected = Connection("subscription", isolation_env_file=path)
        inherited = os.environ if env is None else env
        subscription_env = connection_environment(inherited, selected)
        subscription_isolation_keys = settings.isolation_keys(selected)
    subscription = subscription_available(subscription_env, isolation_keys=subscription_isolation_keys)
    if not subscription:
        diagnostic.append("subscription: claude auth loginでサブスクリプションへログインする")
    available = {"api": api, "subscription": subscription}
    if role == "orchestrator":
        order = ("api",) if orchestrator_api else ("subscription",)
    else:
        order = ("api", "subscription") if worker_api_first else ("subscription", "api")
    return Candidates(
        tuple(Connection(kind, path if kind == "api" else None, path if api else None) for kind in order if available[kind]),
        tuple(diagnostic),
    )


def with_current_isolation(connection: Connection, *, env: Mapping[str, str] | None = None) -> Connection:
    """保存済みsubscriptionの選択を保ち、現行のAPI専用キー集合だけを補う。"""
    if api_only():
        return candidates("delegate", env=env).require_first()
    if connection.kind != "subscription" or connection.isolation_env_file is not None:
        return connection
    choices = candidates("delegate", env=env)
    path = next((item.isolation_env_file for item in choices.connections if item.isolation_env_file), None)
    return dataclasses.replace(connection, isolation_env_file=path)
