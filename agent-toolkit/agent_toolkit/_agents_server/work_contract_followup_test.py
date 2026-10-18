# pylint: disable=protected-access
"""全体契約の参照を公開startで配送する。

起動時に保持する会話識別子と保存領域を模擬して、除去後の同一性を検証する。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,declared", [("task", False), ("task", True), ("delegate", False)])
async def test_public_start_delivers_whole_contract_reference(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["task", "delegate"],
    declared: bool,
) -> None:
    """入力宣言の有無と自由本文の各公開起動から、実在する全体記録を読める。"""
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: A全体\n保持対象: Aの利点・X・Y・ZZ\n", encoding="utf-8")
    own = tmp_path / "child-handoff.md"
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("whole-session",))
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda: own)
    manager = SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "child", "status": "running", "label": "担当"}),
        launcher_record_context=lambda: {"status": "unresolved"},
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs: dict[str, Any]
    if mode == "task":
        document = tmp_path / "task.subagent.md"
        names = "対象,引き継ぎ記録先" if declared else "対象"
        document.write_text(f"# 担当\n\n## 入力\n\n```text\n必須入力名: {names}\n```\n", encoding="utf-8")
        monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
        monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
        monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
        inputs = {"subagent_md_path": str(document), "extra_params": {"対象": "A全体"}}
    else:
        inputs = {"prompt": "Aを実装・検収する", "model_type": "high_tier"}
    response = await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    prompt = manager.start.await_args.args[1]
    assert f"全体作業契約: {whole}" in prompt
    assert f"引き継ぎ記録先: {own}（新規）" in prompt
    assert pathlib.Path(response["handoff_record_path"]) == own
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


def test_missing_whole_contract_is_explicit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """全体記録を取得できない状態を、委譲先自身の記録先で代用しない。"""
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("whole-session",))
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda *_args, **_kwargs: tmp_path)
    notice = launch_requests.whole_contract_reference()
    assert "全体作業契約: 未解決" in notice
    assert "取得した記録の要求と担当の成果を比べる" in notice
