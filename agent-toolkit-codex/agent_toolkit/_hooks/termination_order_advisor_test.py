"""agent-toolkit/agent_toolkit/_hooks/termination_order_advisor.py のテスト。

多段終了手順の不足・順序違反の検出と、非対象・委譲先・記録読取不能・非同期作業継続中の
各非遮断条件を検証する。
"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import stop_gate as _stop_gate
from agent_toolkit._hooks import termination_order_advisor
from agent_toolkit._testing.helpers import _write_transcript


def _set_state_directory(monkeypatch: pytest.MonkeyPatch, directory: pathlib.Path) -> None:
    """常時ログの出力先を固定する。"""
    monkeypatch.setenv("TMPDIR", str(directory))
    monkeypatch.setenv("TEMP", str(directory))
    monkeypatch.setenv("TMP", str(directory))


def _skill_entry(skill: str, *, tool_use_id: str = "toolu_skill") -> dict:
    """Skillツール起動を含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_skill",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Skill", "input": {"skill": skill}}],
            "stop_reason": "end_turn",
        },
    }


def _exit_entry(*, tool_use_id: str = "toolu_exit") -> dict:
    """終了CLIのBash起動を含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_exit",
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "Bash",
                    "input": {"command": "atk agents-exit-session"},
                }
            ],
            "stop_reason": "end_turn",
        },
    }


def _async_wait_entry(*, tool_use_id: str = "toolu_async") -> dict:
    """非同期待機系ツール（Agent）の起動を含むアシスタントエントリを生成する。"""
    return {
        "type": "assistant",
        "message": {
            "id": "msg_async",
            "role": "assistant",
            "content": [{"type": "tool_use", "id": tool_use_id, "name": "Agent", "input": {}}],
            "stop_reason": "end_turn",
        },
    }


def _payload(session_id: str, transcript_path: str, *, stop_hook_active: bool = True) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "transcript_path": transcript_path,
            "stop_hook_active": stop_hook_active,
            "background_tasks": [],
        }
    )


def _clear_caches() -> None:
    _stop_gate._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access
    _stop_gate._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access


def test_approves_when_not_reentrant(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`stop_hook_active`が偽の場合は、終了手順が不足していても遮断しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(tmp_path, [_skill_entry("agent-toolkit:process-wi")])

    decision, body = termination_order_advisor.evaluate(
        _payload("sess-not-reentrant", str(transcript), stop_hook_active=False),
    )

    assert (decision, body) == ("approve", "")


def test_blocks_when_termination_skill_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """対象スキル起動後に終了スキルが1つも起動されていない場合は遮断する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(tmp_path, [_skill_entry("agent-toolkit:process-wi")])

    decision, body = termination_order_advisor.evaluate(_payload("sess-missing", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:process-wi" in body
    assert "agent-toolkit:completion-report" in body
    assert "atk agents-exit-session" in body


def test_blocks_when_termination_order_reversed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """終了スキルの起動順が逆の場合も未充足として遮断する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _exit_entry(tool_use_id="toolu_2"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_3"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-reversed", str(transcript)))

    assert decision == "block"
    assert "atk agents-exit-session" in body


def test_approves_when_termination_order_satisfied(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """対象スキルの最新起動以後に終了スキルが要求順で起動済みなら許可する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
            _exit_entry(tool_use_id="toolu_3"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-satisfied", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_blocks_when_second_invocation_lacks_new_termination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """1回目の充足を2回目の対象スキル起動の充足へ流用しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
            _exit_entry(tool_use_id="toolu_3"),
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_4"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-second", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:completion-report" in body


def test_approves_when_target_skill_never_invoked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """対象スキルを一度も起動していないセッションは検査対象外とする。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(tmp_path, [_skill_entry("agent-toolkit:writing-standards")])

    decision, body = termination_order_advisor.evaluate(_payload("sess-unrelated", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_approves_delegated_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`AGENT_TOOLKIT_DELEGATED_SESSION`が`1`の委譲先では終了手順を検査しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    _clear_caches()
    transcript = _write_transcript(tmp_path, [_skill_entry("agent-toolkit:process-wi")])

    decision, body = termination_order_advisor.evaluate(_payload("sess-delegated", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_approves_and_logs_when_transcript_unreadable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """transcriptが存在しない場合は遮断せず、Stop判定ログへ検査不能を記録する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    session_id = "sess-unreadable"
    missing_path = str(tmp_path / "does-not-exist.jsonl")

    decision, body = termination_order_advisor.evaluate(_payload(session_id, missing_path))

    assert (decision, body) == ("approve", "")
    log_text = (tmp_path / f"claude-agent-toolkit-stop-{session_id}.log").read_text(encoding="utf-8")
    assert "検査不能" in log_text


def test_approves_when_pending_async_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """継続中の非同期作業がある場合は既存の判定を維持し遮断しない。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:process-wi", tool_use_id="toolu_1"),
            _async_wait_entry(),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-pending-async", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_add_awi_requires_only_completion_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`agent-toolkit:add-awi`は`agent-toolkit:completion-report`だけを要求する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(
        tmp_path,
        [
            _skill_entry("agent-toolkit:add-awi", tool_use_id="toolu_1"),
            _skill_entry("agent-toolkit:completion-report", tool_use_id="toolu_2"),
        ],
    )

    decision, body = termination_order_advisor.evaluate(_payload("sess-add-awi", str(transcript)))

    assert (decision, body) == ("approve", "")


def test_add_awi_blocks_without_completion_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`agent-toolkit:add-awi`起動後に終了スキルが無ければ遮断する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _clear_caches()
    transcript = _write_transcript(tmp_path, [_skill_entry("agent-toolkit:add-awi")])

    decision, body = termination_order_advisor.evaluate(_payload("sess-add-awi-missing", str(transcript)))

    assert decision == "block"
    assert "agent-toolkit:add-awi" in body
    assert "agent-toolkit:completion-report" in body
