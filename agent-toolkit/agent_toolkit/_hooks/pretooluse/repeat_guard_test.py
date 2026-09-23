"""PreToolUseの同一ツール呼び出し反復ガードを検証する。"""

from __future__ import annotations

import pytest

from agent_toolkit._hooks.pretooluse import repeat_guard


@pytest.fixture(name="state")
def _state(monkeypatch: pytest.MonkeyPatch) -> dict:
    current: dict = {}

    def update_state(_session_id: str, mutator):
        updated = mutator(current.copy())
        if updated is None:
            return False
        current.clear()
        current.update(updated)
        return True

    monkeypatch.setattr(repeat_guard, "update_state", update_state)
    return current


@pytest.mark.parametrize("tool_input", [{"path": "private-input-value"}, {"command": "private-input-value"}])
def test_blocks_tenth_identical_call(state: dict, capsys: pytest.CaptureFixture[str], tool_input: dict[str, str]) -> None:
    """10回目の遮断でツール名と対処を示し、入力本文を再掲しない。"""
    del state
    results = [repeat_guard.check_repeated_tool_call("session", "Read", tool_input) for _ in range(10)]
    assert results == [False] * 9 + [True]
    notice = capsys.readouterr().err
    assert "Readの同一呼び出しが10回連続" in notice
    assert "状態を進める異なる操作" in notice
    assert "private-input-value" not in notice


def test_different_call_resets_count(state: dict) -> None:
    """ツール名又は入力が変わった場合は連続数を1へ戻す。"""
    for _ in range(9):
        assert not repeat_guard.check_repeated_tool_call("session", "Read", {"path": "a"})
    assert not repeat_guard.check_repeated_tool_call("session", "Read", {"path": "b"})
    assert state["pretool_last_call_count"] == 1


def test_missing_session_and_non_json_input_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """状態を特定できない入力とJSON化不能な入力はツール実行を妨げない。"""
    monkeypatch.setattr(repeat_guard, "update_state", lambda *_args: pytest.fail("状態を更新してはならない"))
    assert not repeat_guard.check_repeated_tool_call("", "Read", {})
    assert not repeat_guard.check_repeated_tool_call("session", "Read", {"bad": {object()}})
