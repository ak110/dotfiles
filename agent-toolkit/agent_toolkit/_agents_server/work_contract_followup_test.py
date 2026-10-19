# pylint: disable=protected-access
"""全体契約の参照を公開startで配送する。

起動時に保持する会話識別子、Managerが解決する委譲元、session登録簿と保存領域を組み合わせ、
明示入力、委譲元の受領済み参照、所有会話の記録の解決順と、解決できない起動の拒否を検証する。
"""

from __future__ import annotations

import pathlib
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, Mock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


def _manager(launcher: str | None = None) -> SimpleNamespace:
    """起動入力を記録し、指定した委譲元を解決するManagerの代替を返す。"""
    return SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "child", "status": "running", "label": "担当"}),
        launcher_session_id=lambda: launcher,
        launcher_record_context=lambda: {"status": "unresolved"},
    )


def _start_inputs(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, mode: Literal["task", "delegate"]) -> dict[str, Any]:
    """通常起動の公開入力を返す。taskは記録先を宣言しない役割文書を使う。"""
    if mode == "delegate":
        return {"prompt": "Aを実装・検収する", "model_type": "high_tier"}
    document = tmp_path / "task.subagent.md"
    document.write_text("# 担当\n\n## 入力\n\n```text\n必須入力名: 対象\n作業ツリーへの書込: あり\n```\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "resolve_task_document_path", lambda *_args: document)
    monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda *_args: True)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "high_tier")
    return {"subagent_md_path": str(document), "extra_params": {"対象": "A全体"}}


def _session_areas(monkeypatch: pytest.MonkeyPatch, areas: dict[str, pathlib.Path]) -> None:
    """会話ごとの既存保存領域の解決を、指定した対応表へ差し替える。"""
    monkeypatch.setattr(launch_requests._managed_temp, "find_session_temp", lambda session_id, **_kwargs: areas.get(session_id))


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
    _session_areas(monkeypatch, {"whole-session": tmp_path})
    allocate = Mock(return_value=own)
    monkeypatch.setattr(launch_requests, "_default_handoff_path", allocate)
    manager = _manager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs: dict[str, Any]
    if mode == "task":
        document = tmp_path / "task.subagent.md"
        names = "対象,引き継ぎ記録先" if handoff_input is not None else "対象"
        document.write_text(
            f"# 担当\n\n## 入力\n\n```text\n必須入力名: {names}\n作業ツリーへの書込: あり\n```\n", encoding="utf-8"
        )
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
        allocate.assert_called_once_with(None)
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
    _session_areas(monkeypatch, {"whole-session": tmp_path})
    launch_requests.capture_handoff_session()
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert launch_requests._HANDOFF_SESSION == ("whole-session",)
    assert f"全体作業契約: {whole}" in launch_requests.whole_contract_reference()


@pytest.mark.parametrize("session_id", [None, "mcp-generated", "unresolved-session"])
@pytest.mark.parametrize("mode", ["task", "delegate"])
@pytest.mark.asyncio
async def test_unresolved_whole_contract_requires_explicit_path(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str | None,
    mode: Literal["task", "delegate"],
) -> None:
    """会話も保存領域も解決できない通常起動は、担当と専用記録先を作成せずに明示入力での再発行を返す。"""
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", (session_id,))
    _session_areas(monkeypatch, {})
    allocate = Mock(side_effect=AssertionError("拒否する起動で記録先を生成した"))
    monkeypatch.setattr(launch_requests, "_default_handoff_path", allocate)
    manager = _manager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs = _start_inputs(tmp_path, monkeypatch, mode)
    with pytest.raises(ActionableError) as caught:
        await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    assert "委譲先は起動していない" in caught.value.reason
    assert "whole_contract_path" in caught.value.next_action
    assert "再発行" in caught.value.next_action
    manager.start.assert_not_awaited()
    allocate.assert_not_called()


@pytest.mark.parametrize("auto_resolvable", [False, True])
@pytest.mark.parametrize("session_id", [None, "mcp-generated"])
@pytest.mark.parametrize("mode", ["task", "delegate"])
@pytest.mark.asyncio
async def test_explicit_whole_contract_path_is_delivered(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    session_id: str | None,
    mode: Literal["task", "delegate"],
    auto_resolvable: bool,
) -> None:
    """明示した全体記録を自動解決より優先して配送し、担当専用の記録先と区別する。"""
    explicit = tmp_path / "explicit" / "work-contract.md"
    explicit.parent.mkdir()
    explicit.write_text("目的: 明示した全体要求\n", encoding="utf-8")
    other = tmp_path / "other-session"
    other.mkdir()
    (other / "work-contract.md").write_text("目的: 別の会話の全体要求\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("other-session",) if auto_resolvable else (session_id,))
    _session_areas(monkeypatch, {"other-session": other})
    own = tmp_path / "child-handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda _launcher=None: own)
    manager = _manager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs = _start_inputs(tmp_path, monkeypatch, mode)
    response = await mcp_tools.start(str(tmp_path), mode=mode, whole_contract_path=str(explicit), **inputs)
    prompt = manager.start.await_args.args[1]
    assert prompt.count(f"全体作業契約: {explicit}") == 1
    assert str(other / "work-contract.md") not in prompt
    assert prompt.count(f"引き継ぎ記録先: {own}（新規）") == 1
    assert pathlib.Path(response["handoff_record_path"]) == own != explicit


@pytest.mark.parametrize("mode", ["task", "delegate"])
@pytest.mark.asyncio
async def test_explicit_whole_contract_path_rejects_invalid_target(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["task", "delegate"],
) -> None:
    """明示した記録の不在・ディレクトリ・UTF-8で読めない内容・相対パスを起動前に拒否し、用意後の再発行で起動する。"""
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("mcp-generated",))
    allocate = Mock(return_value=tmp_path / "child-handoff.md")
    monkeypatch.setattr(launch_requests, "_default_handoff_path", allocate)
    manager = _manager()
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs = _start_inputs(tmp_path, monkeypatch, mode)
    missing = tmp_path / "missing" / "work-contract.md"
    directory = tmp_path / "directory"
    directory.mkdir()
    undecodable = tmp_path / "undecodable.md"
    undecodable.write_bytes(b"\xff\xfe\x00")
    cases = {str(missing): "存在しない", str(directory): "通常ファイルではない", str(undecodable): "UTF-8で読めない"}
    cases["relative/work-contract.md"] = "絶対パスではない"
    for value, reason in cases.items():
        with pytest.raises(ActionableError) as caught:
            await mcp_tools.start(str(tmp_path), mode=mode, whole_contract_path=value, **inputs)
        assert value in caught.value.reason and reason in caught.value.reason
        assert "委譲先は起動していない" in caught.value.reason
        assert "再発行" in caught.value.next_action
    manager.start.assert_not_awaited()
    allocate.assert_not_called()
    missing.parent.mkdir()
    missing.write_text("目的: 用意した全体要求\n", encoding="utf-8")
    undecodable.write_text("目的: 読める全体要求\n", encoding="utf-8")
    for prepared in (missing, undecodable):
        await mcp_tools.start(str(tmp_path), mode=mode, whole_contract_path=str(prepared), **inputs)
        assert f"全体作業契約: {prepared}" in manager.start.await_args.args[1]


@pytest.mark.parametrize("mode", ["task", "delegate"])
@pytest.mark.asyncio
async def test_launcher_conversation_resolves_whole_contract(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["task", "delegate"],
) -> None:
    """共有ルートが会話を表さない起動は、Managerが解決した委譲元会話の全体記録と保存領域を使う。"""
    launcher_area = tmp_path / "launcher-area"
    launcher_area.mkdir()
    whole = launcher_area / "work-contract.md"
    whole.write_text("目的: 対象リポジトリ担当の全体要求\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("mcp-process-root",))
    _session_areas(monkeypatch, {"launcher-conversation": launcher_area})
    monkeypatch.setattr(
        launch_requests._managed_temp,
        "resolve_session_temp",
        lambda session_id, **_kwargs: launcher_area if session_id == "launcher-conversation" else None,
    )
    manager = _manager("launcher-conversation")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs = _start_inputs(tmp_path, monkeypatch, mode)
    response = await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    prompt = manager.start.await_args.args[1]
    assert f"全体作業契約: {whole}" in prompt
    handoff = pathlib.Path(response["handoff_record_path"])
    assert handoff.parent == launcher_area
    assert handoff != whole
    assert f"引き継ぎ記録先: {handoff}（新規）" in prompt


@pytest.mark.parametrize("released", [False, True])
@pytest.mark.parametrize("mode", ["task", "delegate"])
@pytest.mark.asyncio
async def test_registered_launcher_reference_is_inherited(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: Literal["task", "delegate"],
    released: bool,
) -> None:
    """委譲元が受領済みの全体参照を孫へ継承し、委譲元自身の保存領域を全体記録として扱わない。

    委譲元の登録簿は実行中と資源解放後の両方を扱う。旧形式の未解決の参照は所有会話の解決へ進む。
    """
    whole = tmp_path / "root-area" / "work-contract.md"
    whole.parent.mkdir()
    whole.write_text("目的: メインの全体要求\n", encoding="utf-8")
    launcher_area = tmp_path / "launcher-area"
    launcher_area.mkdir()
    delivered = "依頼本文の例示: 全体作業契約: /not/the/reference.md\n\n" + launch_requests.whole_contract_reference(
        whole_contract_path=str(whole)
    )
    session_registry.publish(
        "lane-session",
        terminal=True,
        status="completed",
        engine="claude",
        cwd=str(tmp_path),
        launch_info=session_registry.LaunchInfo(prompt=delivered),
    )
    if released:
        session_registry.release("lane-session", reason="retention_expired")
    legacy = "全体作業契約: 未解決。担当の成果が全体の要求を満たすことを確認する工程では、委譲元へ記録の所在を求める。"
    session_registry.publish(
        "legacy-session",
        terminal=True,
        status="completed",
        engine="claude",
        cwd=str(tmp_path),
        launch_info=session_registry.LaunchInfo(
            prompt=launch_requests.auto_message(legacy, source="agents-server", kind="work-contract-reference")
        ),
    )
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("mcp-process-root",))
    _session_areas(monkeypatch, {"lane-session": launcher_area, "legacy-session": launcher_area})
    own = tmp_path / "grandchild-handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda _launcher=None: own)
    manager = _manager("lane-session")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs = _start_inputs(tmp_path, monkeypatch, mode)
    await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    prompt = manager.start.await_args.args[1]
    assert f"全体作業契約: {whole}" in prompt
    assert "/not/the/reference.md\n担当工程" not in prompt
    assert str(launcher_area / "work-contract.md") not in prompt
    assert f"引き継ぎ記録先: {own}（新規）" in prompt
    monkeypatch.setattr(mcp_tools, "_MANAGER", _manager("legacy-session"))
    with pytest.raises(ActionableError) as caught:
        await mcp_tools.start(str(tmp_path), mode=mode, **inputs)
    assert f"全体作業契約が未作成です: {launcher_area / 'work-contract.md'}" in caught.value.reason
