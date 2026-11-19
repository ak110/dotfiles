"""process-loopの会話ID判定を、環境印とhook入力の組合せで検証する。"""

import pytest

from agent_toolkit._common.process_loop_session import (
    claim_process_loop_session,
    is_identified_process_loop_session,
    is_process_loop_session,
)


@pytest.mark.parametrize(
    ("session_id", "environ", "expected"),
    [
        ("parent", {}, False),
        ("parent", {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "0"}, False),
        ("parent", {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}, True),
        (
            "parent",
            {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "parent"},
            True,
        ),
        (
            "nested",
            {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "parent"},
            False,
        ),
        (
            None,
            {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "parent"},
            False,
        ),
        (
            "parent",
            {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": ""},
            False,
        ),
    ],
)
def test_process_loop_session_identity(session_id: str | None, environ: dict[str, str], expected: bool) -> None:
    """会話IDを渡した起動だけが別会話を環境印の継承から分離する。"""
    assert is_process_loop_session(session_id, environ) is expected


_CLAIM = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "launch-1"}


def test_first_session_start_claims_coordinator_id() -> None:
    """会話IDを渡さない起動では最初にSessionStartを迎えた会話だけを統括とし、入れ子の会話を分離する。"""
    assert is_process_loop_session("nested", _CLAIM)
    assert not is_identified_process_loop_session("coordinator", _CLAIM)

    claim_process_loop_session("coordinator", _CLAIM)
    claim_process_loop_session("nested", _CLAIM)

    assert is_process_loop_session("coordinator", _CLAIM)
    assert is_identified_process_loop_session("coordinator", _CLAIM)
    assert not is_process_loop_session("nested", _CLAIM)
    assert not is_identified_process_loop_session("nested", _CLAIM)
    assert not is_identified_process_loop_session("coordinator", {**_CLAIM, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "0"})


@pytest.mark.parametrize(
    "environ",
    [
        {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"},
        {**_CLAIM, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "parent"},
        {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "launch-1"},
    ],
)
def test_claim_is_ignored_without_claim_token_or_with_passed_id(environ: dict[str, str]) -> None:
    """起動側が会話IDを渡した場合と確保の目印が無い場合は、SessionStartが統括の会話IDを変えない。"""
    claim_process_loop_session("other", environ)
    assert not is_identified_process_loop_session("other", environ)


def test_identified_requires_passed_or_claimed_id() -> None:
    """環境印だけの会話は、既存の判定では統括とみなしても、会話IDで確定した統括とはみなさない。"""
    environ = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}
    assert is_process_loop_session("any", environ)
    assert not is_identified_process_loop_session("any", environ)
    assert is_identified_process_loop_session("parent", {**environ, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "parent"})
