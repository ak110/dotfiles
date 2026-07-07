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
    """指定カテゴリの最小マッチ入力を1件返す。フィクスチャ不在時は空文字列。"""
    for text, cat in _SCOPE_ESCALATION_INPUTS:
        if cat == category:
            return text
    return ""


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
