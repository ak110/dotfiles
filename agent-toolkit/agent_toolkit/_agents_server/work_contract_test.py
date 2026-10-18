# pylint: disable=protected-access
"""通常委譲の現行記録先の生成と軽量起動の除外を検証する。

起動モデルの登録を模擬して、記録先の宣言の有無を同じ公開起動条件で比べる。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools, session_handoff
from agent_toolkit._agents_server.state import SessionState

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("declares_handoff", [False, True])
def test_normal_task_gets_one_record_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    declares_handoff: bool,
) -> None:
    """入力宣言の有無によらず通常taskへ専用パスを注入し、宣言済みでは既存補完と同じ値を使う。"""
    document = tmp_path / "task.subagent.md"
    names = "対象,引き継ぎ記録先" if declares_handoff else "対象"
    document.write_text(f"# 担当\n\n## 入力\n\n```text\n必須入力名: {names}\n```\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
    monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
    handoff = tmp_path / "handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda: handoff)
    _model, _usage, prompt, kind, path = launch_requests.task_document_request(str(document), {"対象": "A全体"})
    assert kind == "delegate"
    assert path == handoff
    assert prompt.count(f"引き継ぎ記録先: {handoff}（新規）") == 1
    assert not handoff.exists()


@pytest.mark.asyncio
async def test_delegate_preserves_original_and_returns_same_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自由本文を原文のまま残し、配送と起動応答を同じ記録先へ接続する。"""
    path = tmp_path / "handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda: path)
    manager = SimpleNamespace(start=AsyncMock(return_value={"session_id": "child", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    original = "Aを変更する。\n追加指示の後もA全体を検収する。"
    response = await mcp_tools.start(str(tmp_path), mode="delegate", prompt=original, model_type="high_tier")
    delivered = manager.start.await_args.args[1]
    assert delivered.startswith(original + "\n\n")
    assert f"引き継ぎ記録先: {path}（新規）" in delivered
    assert response["handoff_record_path"] == str(path)
    assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["explore", "shell", "write"])
async def test_lightweight_start_does_not_allocate_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["explore", "shell", "write"],
) -> None:
    """軽量起動は専用記録の生成を経由しない。"""
    allocate = AsyncMock(side_effect=AssertionError("軽量起動で記録先を生成した"))
    monkeypatch.setattr(launch_requests, "delegate_request", allocate)
    result = {"session_id": "child", "status": "running", "label": "軽量"}
    manager = SimpleNamespace(
        start_explore=AsyncMock(return_value=result),
        start_write=AsyncMock(return_value=result),
        start_shell=AsyncMock(return_value=result),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs: dict[str, Any] = (
        {"command": "pwd", "summary_policy": "終了状態"} if mode == "shell" else {"prompt": "自己完結した作業"}
    )
    response = await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    assert "handoff_record_path" not in response
    allocate.assert_not_called()


def test_successor_reads_same_record_after_followup(tmp_path: pathlib.Path) -> None:
    """追送を持つ前任の記録先を後任へ継続として渡し、原文の追送を保持する。"""
    path = tmp_path / "handoff.md"
    session = SessionState(
        session_id="prior",
        cwd=str(tmp_path),
        engine="codex",
        prompt=f"引き継ぎ記録先: {path}（新規）",
        followup_prompts=("A-bをc'へ",),
    )
    prompt = session_handoff.compose_prompt(session, tmp_path / "conversation.md", ())
    assert f"引き継ぎ記録先: {path}（継続）" in prompt
    assert "A-bをc'へ" in prompt
