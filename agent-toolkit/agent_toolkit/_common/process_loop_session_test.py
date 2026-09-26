"""process-loopの会話ID判定を、環境印とhook入力の組合せで検証する。"""

import pytest

from agent_toolkit._common.process_loop_session import is_process_loop_session


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
