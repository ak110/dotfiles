import re
from collections.abc import Mapping

lazy from agent_toolkit._common.delegated_session import is_delegated
lazy from agent_toolkit._common.session_state import read_state, update_state

_SESSION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION"
_SESSION_ID_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"
CLAIM_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM"
_CLAIM_STATE_PREFIX = "process-loop-claim-"
_CLAIMED_KEY = "claimed_session_id"
_TOKEN = re.compile(r"[A-Za-z0-9_-]+\Z")


def _claim_key(environ: Mapping[str, str]) -> str | None:
    token = environ.get(CLAIM_ENV)
    return f"{_CLAIM_STATE_PREFIX}{token}" if token and _TOKEN.match(token) else None


def _expected_session_id(environ: Mapping[str, str]) -> str | None:
    if (expected_id := environ.get(_SESSION_ID_ENV)) is not None:
        return expected_id
    key = _claim_key(environ)
    claimed = read_state(key).get(_CLAIMED_KEY) if key else None
    return claimed if isinstance(claimed, str) else None


def is_process_loop_session(session_id: str | None, environ: Mapping[str, str]) -> bool:
    """`atk wi process-loop`が起動した会話のhookかを返す。

    入れ子の`claude`・`codex exec`・`agy -p`は親の環境印を継承するが、hook入力の会話IDは異なる。
    統括の会話IDを確定できる場合は両IDの一致で判定する。確定できない旧起動では、環境印だけを使う既存の判定を維持する。
    """
    if environ.get(_SESSION_ENV) != "1":
        return False
    expected_id = _expected_session_id(environ)
    if expected_id is None:
        return True
    return bool(expected_id) and session_id == expected_id


def is_identified_process_loop_session(session_id: str | None, environ: Mapping[str, str]) -> bool:
    """統括の会話IDを確定でき、hook入力の会話IDと一致する場合だけ真を返す。

    終了要求は祖先の対話CLIへ届くため、環境印だけで判定すると、
    統括が入れ子で起動した非対話CLIのhookから外側の統括を止めうる。
    """
    if environ.get(_SESSION_ENV) != "1":
        return False
    expected_id = _expected_session_id(environ)
    return bool(expected_id) and session_id == expected_id


def claim_process_loop_session(session_id: str, environ: Mapping[str, str]) -> None:
    """会話IDを渡さずに起動した統括で、最初のSessionStartの会話IDを統括の会話IDとして確保する。

    統括のSessionStartは入れ子のCLIの起動より先に発火するため、後から始まった会話のSessionStartは確保済みの値を変えない。
    委譲先の会話は確保しない。確保が先に済むよう、各ホストのSessionStart処理の失敗しうる処理より前で呼ぶ。
    """
    if environ.get(_SESSION_ENV) != "1" or environ.get(_SESSION_ID_ENV) is not None or not _TOKEN.match(session_id):
        return
    if is_delegated(environ):
        return
    if (key := _claim_key(environ)) is not None:
        update_state(key, lambda state: None if state.get(_CLAIMED_KEY) else {**state, _CLAIMED_KEY: session_id})
