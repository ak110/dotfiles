"""subagent_stop_advisorのテスト。

scope-escalation検出テストの入力フレーズは
`agent-toolkit/skills/agent-standards/references/_scope_escalation_test_inputs.txt`
から動的に読み込む（`agent-toolkit:agent-standards`「コンテキスト汚染の回避」節。
検出語そのものをテストコード本文へ転記しない）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from _scope_escalation_test_helpers import load_scope_escalation_inputs

_SCRIPT = Path(__file__).parent / "subagent_stop_advisor.py"

_SCOPE_ESCALATION_INPUTS = load_scope_escalation_inputs()


def _pick_scope_escalation_text(category: str) -> str:
    """指定カテゴリの最小マッチ入力を1件返す。フィクスチャ不在時は空文字列。

    フィクスチャ内の最後の該当行を返す。新規追記した最小マッチ入力を
    優先的にE2Eテストへ供給するため（末尾追記が既定の追記位置のため）。
    """
    picked = ""
    for text, cat in _SCOPE_ESCALATION_INPUTS:
        if cat == category:
            picked = text
    return picked


def _run(payload: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def test_no_message_passes() -> None:
    result = _run({})
    assert result.stdout == ""
    assert result.returncode == 0


def test_normal_message_passes() -> None:
    result = _run({"last_assistant_message": "工程4完了。次工程へ移行する。"})
    assert result.stdout == ""


def _make_transcript(tmp_path: Path, tool_uses: list[dict]) -> str:
    """指定したtool_useブロック列を含むassistant messageを1件出力したtranscriptパスを返す。"""
    path = tmp_path / "transcript.jsonl"
    entry = {
        "type": "assistant",
        "message": {"content": tool_uses},
    }
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")
    return str(path)


def test_process_omission_blocks() -> None:
    text = _pick_scope_escalation_text("process-omission")
    if not text:
        pytest.skip("scope-escalation fixture for process-omission not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_single_session_blocks() -> None:
    text = _pick_scope_escalation_text("single-session")
    if not text:
        pytest.skip("scope-escalation fixture for single-session not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_blocks_async_wait_new_phrases() -> None:
    """`async-wait`カテゴリの新規追記フレーズもblockする。"""
    text = _pick_scope_escalation_text("async-wait")
    if not text:
        pytest.skip("scope-escalation fixture for async-wait not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "async-wait" in body["reason"]


def test_blocks_overhead_tradeoff_phrases() -> None:
    """`overhead-tradeoff`カテゴリのフレーズもblockする。"""
    text = _pick_scope_escalation_text("overhead-tradeoff")
    if not text:
        pytest.skip("scope-escalation fixture for overhead-tradeoff not available")
    result = _run({"last_assistant_message": text})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "overhead-tradeoff" in body["reason"]


def test_stop_hook_active_bypasses_check() -> None:
    """`stop_hook_active`真は判定処理をせず無条件approveを返す。

    通常なら縮退表明としてblockされる本文であっても、再呼び出し時は
    連続ブロック上限による強制終了を避けるため無条件approveを返す。
    """
    text = _pick_scope_escalation_text("single-session")
    if not text:
        pytest.skip("scope-escalation fixture for single-session not available")
    result = _run({"last_assistant_message": text, "stop_hook_active": True})
    body = json.loads(result.stdout)
    assert body.get("decision") == "approve"


def test_empty_message_blocks_as_empty_result() -> None:
    """空文字列の完了報告は`is_empty_completion_report`でblockする。"""
    result = _run({"last_assistant_message": ""})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_whitespace_only_message_blocks_as_empty_result() -> None:
    """trim後空の完了報告は`is_empty_completion_report`でblockする。"""
    result = _run({"last_assistant_message": "   \n  \t  "})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "empty" in body["reason"]


def test_skill_invocation_only_blocks_as_empty_result() -> None:
    """`Skill`呼び出し単独の完了報告はblockする。"""
    result = _run({"last_assistant_message": "Skill(skill='foo')"})
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "Skill" in body["reason"]


def test_named_subagent_without_main_send_blocks(tmp_path: Path) -> None:
    """named subagentが閾値以上のtool_useを実行しSendMessage(to='main')が無い場合blockする。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "Bash", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "実装が完了した。差分は3ファイル。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    body = json.loads(result.stdout)
    assert body["decision"] == "block"
    assert "SendMessage" in body["reason"]


def test_named_subagent_with_main_send_passes(tmp_path: Path) -> None:
    """SendMessage(to='main')送付済みnamed subagentは通過する。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "SendMessage", "input": {"to": "main", "message": "done"}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "完了報告を送付した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_short_lived_named_subagent_passes(tmp_path: Path) -> None:
    """tool_use数が閾値未満のnamed subagentは検査対象外。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "対象ファイルを1件確認した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_unnamed_subagent_missing_send_passes(tmp_path: Path) -> None:
    """`agent_name`未指定（匿名subagent）はSendMessage検査対象外。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "Bash", "input": {}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "作業が完了した。",
            "agent_name": "",
            "transcript_path": transcript,
        }
    )
    assert result.stdout == ""
    assert result.returncode == 0


def test_named_subagent_send_to_other_target_blocks(tmp_path: Path) -> None:
    """SendMessage送付先が`main`以外の場合はメイン報告未送とみなしblockする。"""
    tool_uses = [
        {"type": "tool_use", "name": "Read", "input": {}},
        {"type": "tool_use", "name": "Edit", "input": {}},
        {"type": "tool_use", "name": "SendMessage", "input": {"to": "plan-impl-2", "message": "hi"}},
    ]
    transcript = _make_transcript(tmp_path, tool_uses)
    result = _run(
        {
            "last_assistant_message": "実装が完了した。",
            "agent_name": "plan-impl-1",
            "transcript_path": transcript,
        }
    )
    body = json.loads(result.stdout)
    assert body["decision"] == "block"


def test_skill_invocation_with_body_passes() -> None:
    """`Skill`呼び出し後に完了本文が続く正常報告はblockされない。"""
    text = "Skill(skill='foo')\n\n点検実施済。指摘なし。次工程へ移行する。"
    result = _run({"last_assistant_message": text})
    assert result.stdout == ""
    assert result.returncode == 0


def test_non_string_message_passes() -> None:
    """非文字列型の`last_assistant_message`は判定を通過する。"""
    result = _run({"last_assistant_message": None})
    assert result.stdout == ""
    assert result.returncode == 0
