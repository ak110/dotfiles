# pylint: disable=protected-access
"""全体契約の参照を公開startで配送する。

起動時に保持する会話識別子と保存領域を模擬して、除去後の同一性を検証する。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._common.next_action import ActionableError

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    "mode,handoff_input",
    [("task", None), ("task", "auto"), ("task", "新規"), ("task", "継続"), ("delegate", None)],
)
@pytest.mark.asyncio
async def test_public_start_delivers_whole_contract_reference(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["task", "delegate"],
    handoff_input: str | None,
) -> None:
    """契約不足では記録先を生成せず未起動を返し、作成後の再発行で同じ全体記録を配送する。"""
    whole = tmp_path / "work-contract.md"
    own = tmp_path / "child-handoff.md"
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("whole-session",))
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda *_args, **_kwargs: tmp_path)
    allocate = Mock(return_value=own)
    monkeypatch.setattr(launch_requests, "_default_handoff_path", allocate)
    manager = SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "child", "status": "running", "label": "担当"}),
        launcher_record_context=lambda: {"status": "unresolved"},
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs: dict[str, Any]
    if mode == "task":
        document = tmp_path / "task.subagent.md"
        names = "対象,引き継ぎ記録先" if handoff_input is not None else "対象"
        document.write_text(f"# 担当\n\n## 入力\n\n```text\n必須入力名: {names}\n```\n", encoding="utf-8")
        monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
        monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
        monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
        inputs = {"subagent_md_path": str(document), "extra_params": {"対象": "A全体"}}
        if handoff_input in {"新規", "継続"}:
            inputs["extra_params"]["引き継ぎ記録先"] = f"{own}（{handoff_input}）"
    else:
        inputs = {"prompt": "Aを実装・検収する", "model_type": "high_tier"}
    before = set(tmp_path.iterdir())
    with pytest.raises(ActionableError) as caught:
        await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    diagnostic = caught.value.message
    assert str(whole) in diagnostic
    assert "委譲先は起動していない" in diagnostic
    assert "share/work-contract.md" in diagnostic
    assert "作成" in diagnostic and "再発行" in diagnostic
    manager.start.assert_not_awaited()
    allocate.assert_not_called()
    assert set(tmp_path.iterdir()) == before
    whole.write_text("目的: A全体\n保持対象: Aの利点・X・Y・ZZ\n", encoding="utf-8")
    response = await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    prompt = manager.start.await_args.args[1]
    assert f"全体作業契約: {whole}" in prompt
    explicit = handoff_input in {"新規", "継続"}
    designation = handoff_input if explicit else "新規"
    assert prompt.count(f"引き継ぎ記録先: {own}（{designation}）") == 1
    if explicit:
        assert "handoff_record_path" not in response
        allocate.assert_not_called()
    else:
        assert pathlib.Path(response["handoff_record_path"]) == own
        allocate.assert_called_once_with()
    assert whole.read_text(encoding="utf-8").startswith("目的: A全体")
    assert not own.exists()


def test_codex_contract_reference_survives_environment_stripping(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Codexメインの識別子を起動時に保持し、会話context除去後も同じ全体記録へ届く。"""
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: A全体\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", None)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "whole-session")
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda *_args, **_kwargs: tmp_path)
    launch_requests.capture_handoff_session()
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert launch_requests._HANDOFF_SESSION == ("whole-session",)
    assert f"全体作業契約: {whole}" in launch_requests.whole_contract_reference()


@pytest.mark.parametrize("session_id", [None, "mcp-generated", "unresolved-session"])
@pytest.mark.parametrize("mode", ["task", "delegate"])
@pytest.mark.asyncio
async def test_unresolved_whole_contract_keeps_public_start(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str | None,
    mode: Literal["task", "delegate"],
) -> None:
    """会話または保存領域自体を解決できない通常起動は既存の未解決の案内で起動する。"""
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", (session_id,))
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda: tmp_path / "handoff.md")
    manager = SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "child", "status": "running", "label": "担当"}),
        launcher_record_context=lambda: {"status": "unresolved"},
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs: dict[str, Any]
    if mode == "task":
        document = tmp_path / "task.subagent.md"
        document.write_text("# 担当\n\n## 入力\n\n```text\n必須入力名: 対象\n```\n", encoding="utf-8")
        monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
        monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
        monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
        inputs = {"subagent_md_path": str(document), "extra_params": {"対象": "A全体"}}
    else:
        inputs = {"prompt": "Aを実装する", "model_type": "high_tier"}
    response = await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    assert response["status"] == "running"
    notice = manager.start.await_args.args[1]
    assert "全体作業契約: 未解決" in notice
    assert "取得した記録の要求と担当の成果を比べる" in notice
