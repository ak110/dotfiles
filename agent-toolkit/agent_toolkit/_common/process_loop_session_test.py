import pytest

from agent_toolkit._common.process_loop_session import (
    claim_process_loop_session,
    is_identified_process_loop_session,
    is_process_loop_session,
    is_report_opening,
)


@pytest.mark.parametrize(
    ("opening", "expected"),
    [
        ("2026年11月23日のdotfilesの作業結果について、この対応で問題ありませんか？", True),
        # 実例: 実行日をISO形式で書いた報告で終了判定が働かず、巡回が止まった。  # pyfltr: allow-comment
        (
            "\n2026-11-24のdotfilesほか6リポジトリの巡回の作業結果について、この対応で問題ありませんか？\n本文",
            True,
        ),
        ("設定を変更しました。この対応で問題ありませんか？", False),
        ("本文\n2026-11-24のdotfilesの作業結果について、この対応で問題ありませんか？", False),
        ("", False),
    ],
)
def test_is_report_opening(opening: str, expected: bool) -> None:
    assert is_report_opening(opening) is expected


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
    assert is_process_loop_session(session_id, environ) is expected


_CLAIM = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1", "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_CLAIM": "launch-1"}


def test_first_session_start_claims_coordinator_id() -> None:
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
    claim_process_loop_session("other", environ)
    assert not is_identified_process_loop_session("other", environ)


def test_identified_requires_passed_or_claimed_id() -> None:
    environ = {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}
    assert is_process_loop_session("any", environ)
    assert not is_identified_process_loop_session("any", environ)
    assert is_identified_process_loop_session("parent", {**environ, "AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID": "parent"})
