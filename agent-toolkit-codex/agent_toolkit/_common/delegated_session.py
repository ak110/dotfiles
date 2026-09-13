"""委譲先セッションかを環境変数から判定する。"""

from collections.abc import Mapping


def is_delegated(environ: Mapping[str, str]) -> bool:
    """委譲先セッションの環境変数の印を判定する。

    Codex backendの委譲先は`AGENT_TOOLKIT_DELEGATED_SESSION`を持たないため、
    `AGENT_TOOLKIT_OWNER_SESSION`も入力とする。
    """
    return environ.get("AGENT_TOOLKIT_DELEGATED_SESSION") == "1" or bool(environ.get("AGENT_TOOLKIT_OWNER_SESSION"))
