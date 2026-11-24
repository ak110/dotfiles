"""Claude会話の役割と利用可能な認証から、接続候補と隔離した環境を決める。  pyfltr: allow-comment

接続はサブスクリプション（`subscription`）、有限のAPIクレジット（`credits`）、従量課金API（`payg`）の3種別で、
認証を設定ディレクトリの種別別envファイル、役割ごとの使用順を`claude_orchestrator_connections`・`claude_worker_connections`が持つ。
認証ファイルがなく、使用順も設定していない環境は、親環境と通常の認証解決を引き継ぐ`inherit`接続だけを使う。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from collections.abc import Mapping

from agent_toolkit._atk import config_migration, config_settings
from agent_toolkit._atk.config_resolution import connection_order, connections_configured
from agent_toolkit._atk.config_settings import CONNECTION_KINDS, connection_order_key
from agent_toolkit._common import claude_connection as settings
from agent_toolkit._common import next_action

AUTH_ENV = settings.AUTH_ENV
API_KINDS = settings.API_KINDS
Connection = settings.Connection
clean_environment = settings.clean_environment
connection_environment = settings.connection_environment
connection_overrides = settings.connection_overrides
subscription_available = settings.subscription_available
isolation_keys = settings.isolation_keys

_API_AUTH_KEYS = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)
"""API接続のenvファイルが値を持つ必要がある、認証または接続方式の変数。"""

CLI_CREDITS_EXCLUDED = (
    "APIクレジットはClaude CLIで起動する会話に使えないため、credits候補から除いた"
    "（APIクレジットの対象はClaude APIとClaude Agent SDK）"
)
"""`claude` CLIで起動する会話から`credits`を除いたことを示す診断。"""


@dataclasses.dataclass(frozen=True)
class Candidates:
    connections: tuple[Connection, ...]
    diagnostics: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    role: str = "delegate"

    def require_first(self) -> Connection:
        if not self.connections:
            key = connection_order_key("claude", self.role)
            reasons = "; ".join(self.diagnostics) or "使用順の種別に認証が設定されていない"
            raise ValueError(
                f"Claude接続候補がありません。{reasons}。"
                f"`atk config set {key} <種別列>`で使える種別を並べ、各種別の認証を用意する"
            )
        return self.connections[0]


def managed() -> bool:
    return connections_configured("claude")


def _api_env_file(kind: str) -> tuple[str | None, str | None]:
    fixed = config_settings.config_dir() / config_settings.CLAUDE_ENV_FILENAMES[kind]
    path = str(fixed.absolute())
    reason = "空、またはAPI認証・接続方式の変数に値が無い"
    try:
        values = settings.read_api_env_file(path)
    except FileNotFoundError:
        return None, None
    except OSError, ValueError:
        values = {}
        reason = "アクセスできない、またはKEY=VALUE形式が不正"
    # 認証も接続方式も持たないファイル（空、コメントだけ、キーが空の値）では、API接続がサブスクリプションの認証を
    # 隔離した上で認証に失敗するため、読めない場合と同じく候補から除く。
    if any(values.get(name) for name in _API_AUTH_KEYS):
        return path, None
    return None, next_action.with_next_action(
        f"API環境ファイルを使えません（{kind}）: {path}: {reason}",
        f"{fixed.name}の権限・記述を確認する。接続に使わない場合は設定ディレクトリから移す",
    )


def isolation_env_files() -> tuple[str, ...]:
    config_migration.migrate_legacy_settings()
    root = config_settings.config_dir()
    return tuple(
        str(path.absolute()) for name in config_settings.CLAUDE_ENV_FILENAMES.values() if (path := root / name).is_file()
    )


def candidates(role: str, *, env: Mapping[str, str] | None = None, cli: bool = False) -> Candidates:
    """役割の使用順を先頭から評価し、認証のある種別だけを候補として返す。

    認証キーを設定していない種別は診断なしで除き、設定したのに使えない種別は修正方法を診断へ出して除く。
    `cli`は`claude` CLIで起動する会話を表し、APIクレジットの対象外のため`credits`を除く。
    """
    if not managed():
        return Candidates((Connection("inherit"),), role=role)
    order = connection_order("claude", role)
    warnings: list[str] = []
    diagnostics: list[str] = []
    api_files: dict[str, str] = {}
    for kind in ("credits", "payg"):
        path, warning = _api_env_file(kind)
        if path:
            api_files[kind] = path
        if warning and kind in order:
            warnings.append(warning)
    # サブスクリプションの起動環境からは、設定したAPI環境ファイルが与える変数も除く。
    isolation = isolation_env_files()
    connections: list[Connection] = []
    for kind in order:
        if kind == "subscription":
            selected = Connection("subscription", isolation_env_files=isolation)
            subscription_env = env
            subscription_isolation_keys = AUTH_ENV
            if isolation:
                subscription_env = connection_environment(os.environ if env is None else env, selected)
                subscription_isolation_keys = settings.isolation_keys(selected)
            if subscription_available(subscription_env, isolation_keys=subscription_isolation_keys):
                connections.append(selected)
            else:
                diagnostics.append("subscription: claude auth loginでサブスクリプションへログインする")
        elif kind == "credits" and cli:
            if "credits" in api_files:
                warnings.append(CLI_CREDITS_EXCLUDED)
        elif kind in api_files:
            connections.append(Connection(kind, api_files[kind], isolation))
    return Candidates(tuple(connections), (*warnings, *diagnostics), tuple(warnings), role)


def outside_order(kind: str | None, role: str) -> bool:
    """保持した接続の種別が、管理中の使用順に含まれないかを返す。

    接続を管理する前に開始した`inherit`の会話も使用順の種別ではないため対象にし、旧`api-only`と統括担当の接続固定が
    `inherit`の会話を次の継続で移していた挙動を保つ。接続を管理しない環境では対象にしない。
    """
    if kind == settings.LEGACY_API_KIND or not managed():
        return False
    return (kind == "inherit" or kind in CONNECTION_KINDS) and kind not in connection_order("claude", role)


def order_includes(kind: str, role: str) -> bool:
    return not managed() or kind in connection_order("claude", role)


def apply_current_settings(
    connection: Connection, *, role: str = "delegate", env: Mapping[str, str] | None = None, cli: bool = False
) -> Connection:
    """継続・再開する会話の保持した接続へ、現在の設定を適用した接続を返す。

    保持した種別が現在の使用順に含まれない場合は、使用順の先頭の利用可能な候補へ移す。
    含まれる場合は保持した接続を使い続け、subscriptionには現在のAPI環境ファイルの隔離キーを補う。
    """
    if connection.kind == settings.LEGACY_API_KIND:
        # 旧記録のパスから固定名への種別を一意に復元できない時は、現在の明示順で接続候補を選び直す。  # pyfltr: allow-comment
        for kind, name in config_settings.CLAUDE_ENV_FILENAMES.items():
            if (
                connection.api_env_file
                and pathlib.Path(connection.api_env_file).absolute() == config_settings.config_dir() / name
            ):
                connection = dataclasses.replace(connection, kind=kind)
                break
        else:
            return candidates(role, env=env, cli=cli).require_first()
    if (cli and connection.kind == "credits") or outside_order(connection.kind, role):
        return candidates(role, env=env, cli=cli).require_first()
    if connection.kind not in {"subscription", *API_KINDS}:
        return connection
    paths = tuple(dict.fromkeys((*connection.isolation_env_files, *isolation_env_files())))
    if connection.kind in API_KINDS:
        path, _ = _api_env_file(connection.kind)
        if path is None:
            return candidates(role, env=env, cli=cli).require_first()
        return dataclasses.replace(connection, api_env_file=path, isolation_env_files=paths)
    return dataclasses.replace(connection, isolation_env_files=paths)
