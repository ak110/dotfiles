"""Claude会話の役割と利用可能な認証から、接続候補と隔離した環境を決める。"""

from __future__ import annotations

import dataclasses
import pathlib
from collections.abc import Mapping

from agent_toolkit._atk import config
from agent_toolkit._common import claude_connection as settings

AUTH_ENV = settings.AUTH_ENV
Connection = settings.Connection
clean_environment = settings.clean_environment
connection_environment = settings.connection_environment
subscription_available = settings.subscription_available


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
    """両認証があるときの役割別順序と、一方だけの環境での単独候補を返す。"""
    mode = config.resolve_mutable_setting("claude_api_mode")
    raw_path = config.resolve_mutable_setting("claude_api_env_file")
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
    subscription = subscription_available(env)
    if not subscription:
        diagnostic.append("subscription: claude auth loginでサブスクリプションへログインする")
    available = {"api": api, "subscription": subscription}
    if api and subscription:
        if role == "orchestrator":
            order = ("api",) if mode == "pay-as-you-go" else ("subscription",)
        else:
            order = ("subscription", "api") if mode == "pay-as-you-go" else ("api", "subscription")
    else:
        order = ("api", "subscription")
    return Candidates(
        tuple(Connection(kind, path if kind == "api" else None) for kind in order if available[kind]),
        tuple(diagnostic),
    )
