"""process-loopが起動したClaude会話をhook入力のセッションIDで識別する。"""

from collections.abc import Mapping

_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_SESSION_ID_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"


def is_process_loop_session(session_id: str | None, environ: Mapping[str, str]) -> bool:
    """常駐処理が起動した会話のhookかを返す。

    入れ子の`claude`は親の環境印を継承するが、hook入力の会話IDは異なる。
    起動側が渡したIDがある場合は両IDの一致で判定する。IDを渡さない旧起動と
    ID無し再開では、環境印だけを使う既存の判定を維持する。
    """
    if environ.get(_SESSION_ENV) != "1":
        return False
    expected_id = environ.get(_SESSION_ID_ENV)
    if expected_id is None:
        return True
    return bool(expected_id) and session_id == expected_id
