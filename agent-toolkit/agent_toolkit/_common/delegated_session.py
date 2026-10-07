"""委譲先セッションの印となる環境変数の名前と、その判定。

委譲先を起動する側（agents_server）と判定する側（hook、`atk`）が同じ名前と条件を使うため、
環境変数名と判定を本モジュールだけに置く。
"""

from collections.abc import Mapping

DELEGATED_SESSION_ENV = "AGENT_TOOLKIT_DELEGATED_SESSION"
"""Claude Codeの委譲先へ`1`を設定する環境変数の名前。"""

OWNER_SESSION_ENV = "AGENT_TOOLKIT_OWNER_SESSION"
"""委譲先へ、委譲元（所有する）セッションの識別子を設定する環境変数の名前。"""


def is_delegated(environ: Mapping[str, str]) -> bool:
    """委譲先セッションの環境変数の印を判定する。

    Codex backendの委譲先は`AGENT_TOOLKIT_DELEGATED_SESSION`を持たないため、
    `AGENT_TOOLKIT_OWNER_SESSION`も入力とする。
    """
    return environ.get(DELEGATED_SESSION_ENV) == "1" or bool(environ.get(OWNER_SESSION_ENV))


def owner_session_id(environ: Mapping[str, str]) -> str | None:
    """委譲元（所有する）セッションの識別子を返す。設定されていないか空なら`None`を返す。"""
    return environ.get(OWNER_SESSION_ENV) or None
