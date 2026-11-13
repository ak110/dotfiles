"""mcp_toolsのlaunchの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import launch_requests, mcp_tools, tool_descriptions
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_support import (
    _MODE_INPUT_CASES,
    _manager_with_fake,
    _observed_input_params,
    _recording_candidates,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_start_tools_accept_model_type_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`model_type`を渡した起動は工程別設定の代わりにその値を使い、各ツール固有の起動区分を保つ。"""
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        override = "codex:model/high"
        task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"

        responses = {
            "delegate": await mcp_tools.start(
                str(tmp_path),
                subagent_md_path=str(task_document),
                extra_params=_observed_input_params(task_document.name, tmp_path),
                model_type=override,
            ),
            "explore": await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査", model_type=override),
            "shell": await mcp_tools.start(
                str(tmp_path), mode="shell", command="make test", summary_policy="終了状態", model_type=override
            ),
            "write": await mcp_tools.start(str(tmp_path), mode="write", prompt="起草", model_type=override),
        }

        assert requested == [override] * 4
        for launch_kind, response in responses.items():
            assert any(item["scope"] == "model-override" for item in response["warnings"])
            shown = await manager.show_session(response["session_id"])
            assert shown["model_type"] == override
            assert shown["launch_kind"] == launch_kind


@pytest.mark.asyncio
async def test_start_tools_use_task_settings_without_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`model_type`を省略した起動は各ツールの工程別設定を使う。"""
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

        default = await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査")
        named = await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査", model_type="review")
        assert not default.get("warnings")
        assert not named.get("warnings")
        await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態")
        await mcp_tools.start(str(tmp_path), mode="write", prompt="起草")

        assert requested == ["chores_fast", "review", "chores_fast", "write"]


@pytest.mark.parametrize("role", ["add-wi", "exec"])
@pytest.mark.parametrize("override", [None, "execute", "chores", "codex:model/high"])
@pytest.mark.asyncio
async def test_role_override_warning_keeps_requested_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, role: str, override: str | None
) -> None:
    """役割文書の工程との差だけを警告し、個別に認可された上書きも受理する。"""
    requested = _recording_candidates(monkeypatch)
    document = launch_requests._SHARE_DIRECTORY / f"{role}.subagent.md"
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        response = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=str(document),
            extra_params=_observed_input_params(document.name, tmp_path),
            model_type=override,
        )
        assert requested == [override or "execute"]
        warnings = [item for item in response.get("warnings", []) if item["scope"] == "model-override"]
        assert bool(warnings) == (override not in (None, "execute"))
        if warnings:
            assert override is not None
            assert "execute" in warnings[0]["message"]
            assert override in warnings[0]["message"]
            assert "省略" in warnings[0]["message"]


@pytest.mark.asyncio
async def test_free_prompt_direct_candidates_warn_without_rejection(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """自由本文でも候補直接指定は設定を参照しない旨を公開する。"""
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        response = await mcp_tools.start(str(tmp_path), mode="delegate", prompt="確定済み作業", model_type="codex:model/high")
        assert requested == ["codex:model/high"]
        assert any(item["scope"] == "model-override" for item in response["warnings"])


@pytest.mark.asyncio
async def test_start_label_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """label省略時は凡例どおりの識別名を生成し、明示したlabelはその値を使う。"""
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        exec_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
        exec_params = {**_observed_input_params(exec_document.name, tmp_path), "レーン識別子": "lane-01"}
        explain_document = launch_requests._SHARE_DIRECTORY / "external-write-review.subagent.md"
        explain_params = _observed_input_params(explain_document.name, tmp_path)

        labels = {
            "lane": await mcp_tools.start(str(tmp_path), subagent_md_path=str(exec_document), extra_params=exec_params),
            "explain": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params
            ),
            "named": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params, label="投稿前の確認"
            ),
            "blank": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params, label="   "
            ),
            "shell": await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態"),
            "shell_path": await mcp_tools.start(
                str(tmp_path), mode="shell", command="/usr/bin/git status", summary_policy="終了状態"
            ),
            "explore": await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査\n詳細"),
            "write": await mcp_tools.start(str(tmp_path), mode="write", prompt="起草\n詳細"),
        }

        shown = {key: (await manager.show_session(response["session_id"]))["label"] for key, response in labels.items()}
        assert shown == {
            "lane": "lane-01-exec",
            "explain": "external-write-review",
            "named": "投稿前の確認",
            "blank": "external-write-review",
            "shell": "shell-make",
            "shell_path": "shell-git",
            "explore": "explore",
            "write": "write",
        }


@pytest.mark.parametrize(("mode", "inputs", "named"), _MODE_INPUT_CASES)
@pytest.mark.asyncio
async def test_start_rejects_missing_and_mixed_mode_inputs_before_creating_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    mode: str,
    inputs: dict[str, Any],
    named: str,
) -> None:
    """modeが必要とする入力の欠落と受理しない入力の混在を、委譲先の起動前に修正可能な診断で拒否する。"""
    manager = SimpleNamespace(
        start=AsyncMock(),
        start_explore=AsyncMock(),
        start_write=AsyncMock(),
        start_shell=AsyncMock(),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool("start", {"cwd": str(tmp_path), "mode": mode, **inputs})

    body = str(raised.value)
    assert named in body
    assert "委譲先は起動していない" in body
    assert NEXT_ACTION_PREFIX in body
    assert "最小の呼び出し例" in body
    for method in (manager.start, manager.start_explore, manager.start_write, manager.start_shell):
        method.assert_not_awaited()


@pytest.mark.parametrize("model_type", ["execute", "orchestrate", " execute "])
@pytest.mark.parametrize("mode", ["delegate", "explore", "write", "shell"])
@pytest.mark.asyncio
async def test_start_rejects_role_only_model_types_for_freeform_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, model_type: str, mode: str
) -> None:
    """自由本文4modeの上位工程指定を、起動前に代わりの工程とtaskを案内して拒否する。"""
    response = {"session_id": "unexpected", "status": "running", "label": "担当"}
    manager = SimpleNamespace(
        start=AsyncMock(return_value=response),
        start_explore=AsyncMock(return_value=response),
        start_write=AsyncMock(return_value=response),
        start_shell=AsyncMock(return_value=response),
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    inputs = {"command": "git status", "summary_policy": "終了状態"} if mode == "shell" else {"prompt": "調査"}

    with pytest.raises(ToolError) as raised:
        await mcp_tools.mcp.call_tool("start", {"cwd": str(tmp_path), "mode": mode, **inputs, "model_type": model_type})

    body = str(raised.value)
    assert f"mode={mode}" in body
    assert f"model_type={model_type.strip()}" in body
    assert "委譲先は起動していない" in body
    for name in ("`review`", "`review_fast`", "`chores`", "`chores_fast`", "`write`", "subagent_md_path"):
        assert name in body
    for method in (manager.start, manager.start_explore, manager.start_write, manager.start_shell):
        method.assert_not_awaited()


@pytest.mark.parametrize("mode", ["delegate", "explore", "write", "shell"])
@pytest.mark.asyncio
async def test_start_freeform_modes_keep_lightweight_stages_and_explicit_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, mode: tool_descriptions.StartMode
) -> None:
    """自由本文は5工程とユーザー明示の候補列を受理し、指定値を起動へ渡す。"""
    requested = _recording_candidates(monkeypatch)
    models = ["review", "review_fast", "chores", "chores_fast", "write", "codex:model/high"]
    async with _manager_with_fake("codex") as (manager, _backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        for model_type in models:
            if mode == "shell":
                response = await mcp_tools.start(
                    str(tmp_path), mode=mode, model_type=model_type, command="git status", summary_policy="終了状態"
                )
            else:
                response = await mcp_tools.start(str(tmp_path), mode=mode, model_type=model_type, prompt="調査")
            assert (await manager.show_session(response["session_id"]))["model_type"] == model_type
        assert requested == models


@pytest.mark.asyncio
async def test_start_launches_repository_process_task_with_orchestrate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """all-reposの統括がtaskで起動するリポジトリ担当は、役割文書が定めるorchestrateで起動条件の検査を通過する。"""
    manager = SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "repo", "status": "running", "label": "repository-process"}),
        launcher_session_id=lambda: None,
        launcher_record_context=lambda: {"status": "unresolved"},
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = _observed_input_params("repository-process.subagent.md", tmp_path)
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 作業契約\n", encoding="utf-8")

    response = await mcp_tools.start(
        str(tmp_path), subagent_md_path="repository-process", extra_params=params, whole_contract_path=str(contract)
    )

    assert response["session_id"] == "repo"
    assert manager.start.await_args.args[0] == "orchestrate"
    assert manager.start.await_args.kwargs["launch_kind"] == "delegate"


@pytest.mark.asyncio
async def test_start_routes_each_mode_to_launch_kind_model_and_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開境界から各modeの`launch_kind`、省略時のmodel_type、用途およびlabelがManagerへ届く。"""
    requested: list[str] = []

    def candidates(model_type: str) -> list[tuple[str, str, str]]:
        requested.append(model_type)
        return [("codex", "model", "high")]

    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", candidates)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
        calls: dict[str, dict[str, Any]] = {
            "task": {
                "subagent_md_path": str(task_document),
                "extra_params": _observed_input_params(task_document.name, tmp_path),
            },
            "delegate": {"mode": "delegate", "prompt": "監査\n詳細", "model_type": "chores"},
            "explore": {"mode": "explore", "prompt": "調査", "label": "explore-対象"},
            "write": {"mode": "write", "prompt": "起草"},
            "shell": {"mode": "shell", "command": "make test", "summary_policy": "終了状態"},
        }

        shown = {}
        for mode, arguments in calls.items():
            response = await mcp_tools.start(str(tmp_path), **arguments)
            shown[mode] = await manager.show_session(response["session_id"])

        assert requested == [
            launch_requests._TASK_MODEL_TYPES[task_document.name],
            "chores",
            "chores_fast",
            "write",
            "chores_fast",
        ]
        assert [call[2] for call in backend.start_calls] == ["delegate", "delegate", "explore", "write", "shell"]
        assert {mode: item["label"] for mode, item in shown.items()} == {
            "task": "lane-01-exec",
            "delegate": "監査",
            "explore": "explore-対象",
            "write": "write",
            "shell": "shell-make",
        }
