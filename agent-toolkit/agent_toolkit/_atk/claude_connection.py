"""Claude会話の役割と利用可能な認証から、接続候補と隔離した環境を決める。

接続はサブスクリプション（`subscription`）、有限のAPIクレジット（`credits`）、従量課金API（`payg`）の3種別で、
認証の所在を種別ごとの設定キー、役割ごとの使用順を`claude_orchestrator_connections`・`claude_worker_connections`が持つ。
認証キーと使用順のどれも設定していない環境は、親環境と通常の認証解決を引き継ぐ`inherit`接続だけを使う。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from collections.abc import Mapping

from agent_toolkit._atk import config
from agent_toolkit._common import claude_connection as settings

AUTH_ENV = settings.AUTH_ENV
API_KINDS = settings.API_KINDS
Connection = settings.Connection
clean_environment = settings.clean_environment
connection_environment = settings.connection_environment
connection_overrides = settings.connection_overrides
subscription_available = settings.subscription_available
isolation_keys = settings.isolation_keys

_ENV_FILE_KEYS = {"credits": "claude_credits_env_file", "payg": "claude_payg_env_file"}
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
    """利用できる順序と、選択できなかった候補の復旧案内。

    `warnings`は起動を続ける場合にも明示する設定不良で、`diagnostics`にも含める。
    """

    connections: tuple[Connection, ...]
    diagnostics: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    role: str = "delegate"

    def require_first(self) -> Connection:
        """最初の候補を返し、全候補不成立なら原因と設定方法を示して終了する。"""
        if not self.connections:
            key = config.connection_order_key("claude", self.role)
            reasons = "; ".join(self.diagnostics) or "使用順の種別に認証が設定されていない"
            raise ValueError(
                f"Claude接続候補がありません。{reasons}。"
                f"`atk config set {key} <種別列>`で使える種別を並べ、各種別の認証を用意する"
            )
        return self.connections[0]


def managed() -> bool:
    """Claudeの接続を設定で管理するか（認証キー2つと使用順2つのいずれかを設定したか）を返す。"""
    return config.connections_configured("claude")


def _api_env_file(kind: str) -> tuple[str | None, str | None]:
    """API種別のenvファイルの絶対パスと、設定したのに使えない場合の診断を返す。未設定は両方`None`とする。"""
    key = _ENV_FILE_KEYS[kind]
    raw = config.resolve_mutable_setting(key)
    if not raw:
        return None, None
    path = str(pathlib.Path(raw).absolute())
    try:
        values = settings.read_api_env_file(path)
    except OSError, ValueError, UnicodeError:
        values = {}
    # 認証も接続方式も持たないファイル（空、コメントだけ、キーが空の値）では、API接続がサブスクリプションの認証を
    # 隔離した上で認証に失敗するため、読めない場合と同じく候補から除く。
    if any(values.get(name) for name in _API_AUTH_KEYS):
        return path, None
    return None, (
        f"API環境ファイルを使えません（{kind}。空、存在しない、読み取れない、形式が不正、"
        f"またはAPI認証・接続方式の変数が無い）: {path}。"
        f"atk config set {key} <パス>で修正するか、atk config set {key} ''で解除する"
    )


def candidates(role: str, *, env: Mapping[str, str] | None = None, cli: bool = False) -> Candidates:
    """役割の使用順を先頭から評価し、認証のある種別だけを候補として返す。

    認証キーを設定していない種別は診断なしで除き、設定したのに使えない種別は修正方法を診断へ出して除く。
    `cli`は`claude` CLIで起動する会話を表し、APIクレジットの対象外のため`credits`を除く。
    """
    if not managed():
        return Candidates((Connection("inherit"),), role=role)
    order = config.connection_order("claude", role)
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
    isolation = api_files.get("credits") or api_files.get("payg")
    connections: list[Connection] = []
    for kind in order:
        if kind == "subscription":
            selected = Connection("subscription", isolation_env_file=isolation)
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
            connections.append(Connection(kind, api_files[kind], api_files[kind]))
    return Candidates(tuple(connections), (*warnings, *diagnostics), tuple(warnings), role)


def _normalize_legacy(connection: Connection) -> Connection:
    """種別を区別する前に保存したAPI接続を、保存済みのenvファイルが一致する種別へ読み替える。

    クレジットのenvファイルと一致すれば`credits`、それ以外は従量課金として`payg`とし、保存済みのパスはそのまま使う。
    """
    if connection.kind != settings.LEGACY_API_KIND:
        return connection
    credits_path = config.resolve_mutable_setting("claude_credits_env_file")
    same_credits = bool(
        connection.api_env_file
        and credits_path
        and pathlib.Path(connection.api_env_file).absolute() == pathlib.Path(credits_path).absolute()
    )
    return dataclasses.replace(connection, kind="credits" if same_credits else "payg")


def outside_order(kind: str | None, role: str) -> bool:
    """保持した接続の種別が、管理中の使用順に含まれないかを返す。

    接続を管理する前に開始した`inherit`の会話も使用順の種別ではないため対象にし、旧`api-only`と統括担当の接続固定が
    `inherit`の会話を次の継続で移していた挙動を保つ。接続を管理しない環境では対象にしない。
    """
    if kind == settings.LEGACY_API_KIND or not managed():
        return False
    return (kind == "inherit" or kind in config.CONNECTION_KINDS) and kind not in config.connection_order("claude", role)


def order_includes(kind: str, role: str) -> bool:
    """接続を管理していない環境、または管理中の使用順が種別を含む場合に真を返す。"""
    return not managed() or kind in config.connection_order("claude", role)


def apply_current_settings(
    connection: Connection, *, role: str = "delegate", env: Mapping[str, str] | None = None, cli: bool = False
) -> Connection:
    """継続・再開する会話の保持した接続へ、現在の設定を適用した接続を返す。

    保持した種別が現在の使用順に含まれない場合は、使用順の先頭の利用可能な候補へ移す。
    含まれる場合は保持した接続を使い続け、subscriptionには現在のAPI環境ファイルの隔離キーを補う。
    """
    connection = _normalize_legacy(connection)
    if outside_order(connection.kind, role):
        return candidates(role, env=env, cli=cli).require_first()
    if connection.kind != "subscription" or connection.isolation_env_file is not None:
        return connection
    choices = candidates(role, env=env, cli=cli)
    path = next((item.isolation_env_file for item in choices.connections if item.isolation_env_file), None)
    return dataclasses.replace(connection, isolation_env_file=path)
