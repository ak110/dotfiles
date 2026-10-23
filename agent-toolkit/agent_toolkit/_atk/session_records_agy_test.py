"""Antigravityのメイン会話復元とイベント射影を、消費側の_atk層で検証する。"""

import json
from pathlib import Path

import pytest

from agent_toolkit._atk import session_record_format, session_records
from agent_toolkit._atk.wi.constants import PROCESS_WI_GOAL_BODY


def test_native_records_project_user_messages_and_events(tmp_path: Path) -> None:
    """共通解析を消費する列挙・本文抽出・イベント射影の契約を保つ。"""
    entries = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "content": "<USER_REQUEST>\n日本語で直して\n</USER_REQUEST>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("pytest -q")}}],
        },
        {"step_index": 2, "source": "MODEL", "type": "GENERIC", "content": "Command exited with code 0."},
        {"step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE", "content": "修正を完了した"},
    ]
    path = tmp_path / "transcript_full.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    assert session_records.first_user_input(path, "agy") == "日本語で直して"
    assert session_records.last_agent_message(path, "agy") == "修正を完了した"
    assert [event.kind for event in session_record_format.agy_events(entries)] == [
        "user",
        "tool_call",
        "tool_result",
        "assistant",
    ]


def test_stream_tool_updates_keep_one_call() -> None:
    """ACTIVEとDONEの更新を同じ呼び出しとして数え、完了結果を保持する。"""
    entries = [
        {
            "event": "step_update",
            "step_update": {
                "step_index": 4,
                "step_type": "tool",
                "state": state,
                "tool_name": "read_file",
                "tool_info": {"parameters": {"Path": json.dumps("/work/file")}, "result": "本文"},
            },
        }
        for state in ("ACTIVE", "DONE")
    ]
    events = session_record_format.agy_events(entries)
    assert [event.kind for event in events] == ["tool_call", "tool_result"]
    assert events[0].call_id == events[1].call_id


def test_native_workflow_recovery_uses_recorded_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """メイン候補を会話IDで列挙し、hook観測cwd・起動標識・終了応答を読む。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    path = tmp_path / ".gemini/antigravity-cli/brain/main/.system_generated/logs/transcript_full.jsonl"
    path.parent.mkdir(parents=True)
    entries = [
        {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT", "content": PROCESS_WI_GOAL_BODY},
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("atk agents-exit-session")}}],
        },
        {"step_index": 2, "source": "MODEL", "type": "GENERIC", "content": '{"exit_session_invoked":true}'},
    ]
    path.write_text("\n".join(json.dumps(entry) for entry in entries), encoding="utf-8")
    state_path = session_records.session_state.state_path("main")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps({"agy_cwd": str(tmp_path / "work")}), encoding="utf-8")
    assert (path, "agy", "main") in list(session_records.candidate_paths())
    assert session_records.session_cwd(path, "agy") == str(tmp_path / "work")
    assert session_records.invoked_process_wi(path, "agy")
    assert session_records.exit_session_reached(path, "agy") is True
