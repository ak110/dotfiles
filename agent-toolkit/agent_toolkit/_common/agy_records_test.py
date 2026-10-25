"""Antigravityの実機記録形式から、逐語本文・ツール・使用量を取得する。"""

import json
from pathlib import Path

import pytest

from agent_toolkit._common import agy_records, runtime_identity, session_cost, session_record_paths, transcript


@pytest.mark.parametrize("text_field", ["text", "text_delta"])
def test_stream_agent_response_preserves_text_and_original(text_field: str) -> None:
    """実機streamの応答本文も、元recordを保持した会話項目へ変換する。"""
    entry = {
        "event": "step_update",
        "step_update": {
            "conversation_id": "agy-1",
            "step_index": 1,
            "step_type": "agent_response",
            "state": "DONE",
            text_field: "調査中",
        },
    }
    normalized = agy_records.normalize_entries([entry])[0]
    assert normalized["_agy_original"] is entry
    assert normalized["message"]["content"] == [{"type": "text", "text": "調査中"}]


def test_native_records_preserve_original_and_hook_view(tmp_path: Path) -> None:
    """物理行を保って日本語本文とJSON文字列の引数をhook用に変換する。"""
    entries = [
        {
            "step_index": 0,
            "source": "USER_EXPLICIT",
            "type": "USER_INPUT",
            "created_at": "2026-10-22T01:00:00Z",
            "content": "<USER_REQUEST>\n日本語で直して\n</USER_REQUEST>\n<ADDITIONAL_METADATA>日時</ADDITIONAL_METADATA>",
        },
        {
            "step_index": 1,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-22T01:00:01Z",
            "input_tokens": 12,
            "cache_read_tokens": 7,
            "output_tokens": 3,
            "tool_calls": [{"name": "run_command", "args": {"CommandLine": json.dumps("pytest -q")}}],
        },
        {
            "step_index": 2,
            "source": "MODEL",
            "type": "GENERIC",
            "created_at": "2026-10-22T01:00:04Z",
            "content": "Command exited with code 0.",
        },
        {
            "step_index": 3,
            "source": "MODEL",
            "type": "PLANNER_RESPONSE",
            "created_at": "2026-10-22T01:00:05Z",
            "content": "修正を完了した",
            "input_tokens": 2,
            "output_tokens": 4,
        },
    ]
    path = tmp_path / "transcript_full.jsonl"
    raw = "\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries)
    path.write_text(raw, encoding="utf-8")
    normalized = agy_records.normalize_entries(entries)
    assert len(normalized) == len(entries)
    assert normalized[0]["_agy_original"] is entries[0]
    assert normalized[0]["message"]["content"][0]["text"] == "日本語で直して"
    assert normalized[1]["message"]["content"][0]["input"]["CommandLine"] == "pytest -q"
    assert normalized[2]["message"]["content"][0]["tool_use_id"] == "agy-1-0"
    canonical = agy_records.normalize_entries(entries, hook_view=True)
    call = canonical[1]["message"]["content"][0]
    assert call["name"] == "Bash" and call["input"]["command"] == "pytest -q"
    assert transcript.read_transcript_bytes(str(path)) == raw.encode()
    assistant = next(transcript.iter_latest_assistant_text_messages(str(path)))
    assert transcript.visible_assistant_text(assistant) == "修正を完了した"


def test_native_discovery_prefers_complete_record(tmp_path: Path) -> None:
    """同じ会話の2種類のログを二重計上しない。"""
    logs = tmp_path / "antigravity-cli/brain/conversation/.system_generated/logs"
    logs.mkdir(parents=True)
    shortened = logs / "transcript.jsonl"
    complete = logs / "transcript_full.jsonl"
    shortened.write_text("", encoding="utf-8")
    complete.write_text("", encoding="utf-8")
    assert session_record_paths.agy_session_records(tmp_path) == (complete,)
    assert session_record_paths.agy_session_id(complete) == "conversation"
    complete.unlink()
    assert session_record_paths.agy_session_records(tmp_path) == (shortened,)


def test_usage_and_identity_only_use_explicit_observations() -> None:
    """単価不明の使用量を残し、effortの無いモデルを帰属へ推測しない。"""
    native = {
        "step_index": 1,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "model": "gemini-4-argon",
        "input_tokens": 100,
        "cache_read_tokens": 20,
        "output_tokens": 10,
    }
    assert runtime_identity.observed_identity(native, "agy") is None
    identity = runtime_identity.observed_identity({**native, "effort": "medium"}, "agy")
    assert identity and identity.machine == "agy:gemini-4-argon/medium"
    assert "noreply@google.com" in runtime_identity.co_author_trailer(identity)
    cost = session_cost.estimate_record(session_cost.CostRecord("agy:test", "agy", [native])).to_json()
    assert cost["unknown_responses"] == 1
    assert cost["unknown_tokens"]["cache_read_input_tokens"] == 20
    assert cost["estimated_usd"] is None


def test_hook_tool_arguments_decode_once() -> None:
    """編集本文のJSON文字列を数値へ再解釈せず、hookの標準引数名へそろえる。"""
    entry = {
        "step_index": 1,
        "source": "MODEL",
        "type": "PLANNER_RESPONSE",
        "tool_calls": [{"name": "write_file", "args": {"Path": json.dumps("/work/result"), "Content": json.dumps("1")}}],
    }
    call = agy_records.normalize_entries([entry], hook_view=True)[0]["message"]["content"][0]
    assert call["name"] == "Write"
    assert call["input"] == {"file_path": "/work/result", "content": "1"}
