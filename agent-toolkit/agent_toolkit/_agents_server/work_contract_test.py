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

from agent_toolkit._agents_server import launch_requests, mcp_tools, plugin_root, session_handoff
from agent_toolkit._agents_server.state import SessionState
from agent_toolkit._common.next_action import ActionableError

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("handoff_input", [None, "auto", "新規", "継続"])
def test_normal_task_gets_one_record_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    handoff_input: str | None,
) -> None:
    """別rootの資料を入力の宣言・指定方法によらず1回配送し、記録先と新規・継続を保つ。"""
    root = tmp_path / "retained-plugin"
    document = root / "share/task.subagent.md"
    document.parent.mkdir(parents=True)
    references = [root / "share/work-contract.md", root / "skills/delegation/references/handoff-record.md"]
    for reference in references:
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(f"資料本文: {reference.name}\n", encoding="utf-8")
    cwd = tmp_path / "project"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    names = "対象,引き継ぎ記録先" if handoff_input is not None else "対象"
    document.write_text(f"# 担当\n\n## 入力\n\n```text\n必須入力名: {names}\n作業ツリーへの書込: あり\n```\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
    monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
    handoff = tmp_path / "handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda _launcher=None: handoff)
    params = {"対象": "A全体"}
    explicit = handoff_input in {"新規", "継続"}
    if explicit:
        params["引き継ぎ記録先"] = f"{handoff}（{handoff_input}）"
    _model, _usage, prompt, kind, path = launch_requests.task_document_request(str(document), params)
    assert kind == "delegate"
    assert path == (None if explicit else handoff)
    designation = handoff_input if explicit else "新規"
    assert prompt.count(f"引き継ぎ記録先: {handoff}（{designation}）") == 1
    for reference in references:
        assert reference.is_absolute() and reference.is_file()
        assert prompt.count(str(reference)) == 1
        assert reference.read_text(encoding="utf-8") not in prompt
    assert not handoff.exists()


@pytest.mark.asyncio
async def test_delegate_preserves_original_and_returns_same_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """自由本文を原文のまま残し、配送と起動応答を同じ記録先へ接続する。"""
    path = tmp_path / "handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda _launcher=None: path)
    manager = SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "child", "status": "running", "label": "担当"}),
        launcher_session_id=lambda: None,
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    original = "Aを変更する。\n追加指示の後もA全体を検収する。"
    response = await mcp_tools.start(str(tmp_path), mode="delegate", prompt=original, model_type="high_tier")
    delivered = manager.start.await_args.args[1]
    assert delivered.startswith(original + "\n\n")
    assert f"引き継ぎ記録先: {path}（新規）" in delivered
    assert response["handoff_record_path"] == str(path)
    for relative in ("share/work-contract.md", "skills/delegation/references/handoff-record.md"):
        reference = plugin_root.SERVER_PLUGIN_ROOT / relative
        assert reference.is_absolute() and reference.is_file()
        assert reference.parent != tmp_path
        assert delivered.count(str(reference)) == 1
        assert reference.read_text(encoding="utf-8") not in delivered
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


@pytest.mark.parametrize("kind", ["explore", "shell", "write"])
def test_lightweight_task_does_not_deliver_contract_materials(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
) -> None:
    """軽量な役割指定taskは全体契約の確認と専用記録・資料の配送を経由しない。"""
    document = tmp_path / "task.subagent.md"
    document.write_text(
        f"# 担当\n\n## 入力\n\n```text\n必須入力名: 対象\nmode: {kind}\n作業ツリーへの書込: あり\n```\n", encoding="utf-8"
    )
    monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
    monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("whole-session",))
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda *_args, **_kwargs: tmp_path)
    _model, _usage, prompt, actual_kind, path = launch_requests.task_document_request(str(document), {"対象": "調査"})
    assert actual_kind == kind
    assert path is None
    assert "引き継ぎ記録先:" not in prompt
    assert "全体作業契約:" not in prompt
    assert "share/work-contract.md" not in prompt
    assert "skills/delegation/references/handoff-record.md" not in prompt


@pytest.mark.parametrize("target", ["explore", "write", "shell", "lightweight-task", "handoff"])
@pytest.mark.asyncio
async def test_whole_contract_path_rejected_outside_normal_start(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    target: str,
) -> None:
    """軽量起動・軽量な役割指定task・引き継ぎ起動は全体作業契約の明示入力を受理せず、起動前に拒否する。"""
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: A全体\n", encoding="utf-8")
    manager = SimpleNamespace(
        launcher_session_id=lambda: None,
        start=AsyncMock(),
        start_explore=AsyncMock(),
        start_write=AsyncMock(),
        start_shell=AsyncMock(),
        start_handoff=AsyncMock(),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs: dict[str, Any] = {"whole_contract_path": str(whole)}
    if target == "handoff":
        inputs["handoff_from"] = "prior"
    elif target == "lightweight-task":
        document = tmp_path / "task.subagent.md"
        document.write_text(
            "# 担当\n\n## 入力\n\n```text\n必須入力名: 対象\nmode: explore\n作業ツリーへの書込: なし\n```\n", encoding="utf-8"
        )
        monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
        monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
        monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
        inputs |= {"cwd": str(tmp_path), "subagent_md_path": str(document), "extra_params": {"対象": "調査"}}
    elif target == "shell":
        inputs |= {"cwd": str(tmp_path), "mode": "shell", "command": "pwd", "summary_policy": "終了状態"}
    else:
        inputs |= {"cwd": str(tmp_path), "mode": target, "prompt": "自己完結した作業"}
    with pytest.raises(ActionableError) as caught:
        await mcp_tools.start(**inputs)
    assert "whole_contract_path" in caught.value.reason
    assert "委譲先は起動していない" in caught.value.message or "handoff_from" in caught.value.reason
    for operation in (manager.start, manager.start_explore, manager.start_write, manager.start_shell, manager.start_handoff):
        operation.assert_not_awaited()


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
