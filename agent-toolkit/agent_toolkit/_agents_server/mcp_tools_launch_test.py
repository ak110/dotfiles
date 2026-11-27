"""mcp_toolsのlaunchの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
from types import SimpleNamespace
from typing import Any, Literal, TypedDict
from unittest.mock import AsyncMock, Mock

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from agent_toolkit._agents_server import launch_requests, mcp_tools, model_override, tool_descriptions
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError
from agent_toolkit._testing.agents_server_support import (
    _MODE_INPUT_CASES,
    _manager_with_fake,
    _observed_input_params,
    _recording_candidates,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


class _OverrideInputs(TypedDict):
    model_override: Literal["user_instruction"]
    model_override_evidence: str


_OVERRIDE: _OverrideInputs = {"model_override": "user_instruction", "model_override_evidence": "ユーザーが検証用のモデルを指定"}


@pytest.mark.asyncio
async def test_start_observation_is_bound_before_returning_to_mcp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """公開startは応答より先に、実際に起動したsessionへ起動識別子を渡す。"""
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        token = "0123456789abcdef" * 2
        response = await mcp_tools.start(
            str(tmp_path), mode="explore", prompt="調査", model_type="codex:model/high", start_observation=token, **_OVERRIDE
        )
        assert manager.sessions[response["session_id"]].start_observation == token
        assert "start_observation" not in response


@pytest.mark.asyncio
async def test_start_tools_accept_model_type_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        override = "codex:model/high"
        task_document = launch_requests.resolve_task_document_path("exec")

        responses = {
            "delegate": await mcp_tools.start(
                str(tmp_path),
                subagent_md_path=str(task_document),
                extra_params=_observed_input_params(task_document.name, tmp_path),
                model_type=override,
                **_OVERRIDE,
            ),
            "explore": await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査", model_type=override, **_OVERRIDE),
            "shell": await mcp_tools.start(
                str(tmp_path), mode="shell", command="make test", summary_policy="終了状態", model_type=override, **_OVERRIDE
            ),
            "write": await mcp_tools.start(str(tmp_path), mode="write", prompt="起草", model_type=override, **_OVERRIDE),
        }

        assert requested == [override] * 4
        for launch_kind, response in responses.items():
            assert any(item["scope"] == "model-override" for item in response["warnings"])
            shown = await manager.show_session(response["session_id"])
            assert shown["model_type"] == override
            assert "user_instruction" in shown["model_override_record"]
            assert shown["launch_kind"] == launch_kind


@pytest.mark.asyncio
async def test_start_tools_use_task_settings_without_override(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

        default = await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査")
        named = await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査", model_type="review")
        assert not default.get("warnings")
        assert not named.get("warnings")
        await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態")
        await mcp_tools.start(str(tmp_path), mode="write", prompt="起草", **_OVERRIDE)

        assert requested == ["chores_fast", "review", "chores_fast", "write"]

        for selected in (None, ""):
            task = await mcp_tools.start(
                str(tmp_path),
                subagent_md_path="exec",
                extra_params=_observed_input_params("exec.subagent.md", tmp_path),
                model_type=selected,
            )
            assert not task.get("warnings")
            assert manager.sessions[task["session_id"]].explicit_role_model is False
        assert requested[-2:] == ["execute", "execute"]


@pytest.mark.parametrize("role", ["add-wi", "exec"])
@pytest.mark.parametrize("override", [None, "execute", "chores", "codex:model/high"])
@pytest.mark.asyncio
async def test_role_override_warning_keeps_requested_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, role: str, override: str | None
) -> None:
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    requested = _recording_candidates(monkeypatch)
    document = launch_requests.resolve_task_document_path(role)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        response = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=str(document),
            extra_params=_observed_input_params(document.name, tmp_path),
            model_type=override,
            **_OVERRIDE,
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
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    requested = _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        response = await mcp_tools.start(
            str(tmp_path), mode="delegate", prompt="確定済み作業", model_type="codex:model/high", **_OVERRIDE
        )
        assert requested == ["codex:model/high"]
        assert any(item["scope"] == "model-override" for item in response["warnings"])


@pytest.mark.asyncio
async def test_start_label_defaults(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        exec_document = launch_requests.resolve_task_document_path("exec")
        exec_params = {**_observed_input_params(exec_document.name, tmp_path), "レーン識別子": "lane-01"}
        explain_document = launch_requests.resolve_task_document_path("external-write-review")
        explain_params = _observed_input_params(explain_document.name, tmp_path)

        labels = {
            "lane": await mcp_tools.start(str(tmp_path), subagent_md_path=str(exec_document), extra_params=exec_params),
            "explain": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params
            ),
            "named": await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(explain_document), extra_params=explain_params, label="投稿前の確認"
            ),
            "shell": await mcp_tools.start(str(tmp_path), mode="shell", command="make test", summary_policy="終了状態"),
            "shell_path": await mcp_tools.start(
                str(tmp_path), mode="shell", command="/usr/bin/git status", summary_policy="終了状態"
            ),
            "shell_fallback": await mcp_tools.start(
                str(tmp_path), mode="shell", command="Get-Item file", summary_policy="終了状態"
            ),
            "explore": await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査\n詳細"),
            "write": await mcp_tools.start(str(tmp_path), mode="write", prompt="起草\n詳細", label="あ" * 32, **_OVERRIDE),
        }

        shown = {key: (await manager.show_session(response["session_id"]))["label"] for key, response in labels.items()}
        assert shown == {
            "lane": "lane-01-exec",
            "explain": "external-write-review",
            "named": "投稿前の確認",
            "shell": "shell-make",
            "shell_path": "shell-git",
            "shell_fallback": "shell",
            "explore": "explore",
            "write": "あ" * 32,
        }


@pytest.mark.parametrize("mode", ["task", "delegate", "explore", "write", "shell"])
@pytest.mark.parametrize("label", ["あ" * 33, "依頼" * 48 + "する", "", "   ", "設計\nレビュー", "a--b", "a\u200bb"])
@pytest.mark.asyncio
async def test_start_rejects_invalid_label_before_preparing_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, mode: str, label: str
) -> None:
    def unexpected_manager() -> None:
        pytest.fail("形式外の担当名で起動処理に到達してはいけない")

    monkeypatch.setattr(mcp_tools, "_manager", unexpected_manager)
    inputs = {
        "task": {"subagent_md_path": "exec"},
        "delegate": {"prompt": "監査", "model_type": "chores"},
        "explore": {"prompt": "調査"},
        "write": {"prompt": "起草"},
        "shell": {"command": "make test", "summary_policy": "終了状態"},
    }
    with pytest.raises(ToolError, match="(?s)32文字.*省略"):
        await mcp_tools.mcp.call_tool("start", {"cwd": str(tmp_path), "mode": mode, "label": label, **inputs[mode]})


@pytest.mark.parametrize(("mode", "inputs", "named"), _MODE_INPUT_CASES)
@pytest.mark.asyncio
async def test_start_rejects_missing_and_mixed_mode_inputs_before_creating_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    mode: str,
    inputs: dict[str, Any],
    named: str,
) -> None:
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


@pytest.mark.parametrize(
    ("mode", "model_type"),
    [
        ("delegate", "orchestrate"),
        *((mode, model) for mode in ("explore", "write", "shell") for model in ("execute", " execute ", "orchestrate")),
    ],
)
@pytest.mark.asyncio
async def test_start_rejects_role_only_model_types_for_freeform_modes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, model_type: str, mode: str
) -> None:
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
    for name in (
        "`review`",
        "`review_fast`",
        "`chores`",
        "`chores_fast`",
        "`mode=delegate, model_type=execute`",
        "`exec`",
        "subagent_md_path",
    ):
        assert name in body
    assert "`write`" not in body
    for method in (manager.start, manager.start_explore, manager.start_write, manager.start_shell):
        method.assert_not_awaited()


@pytest.mark.parametrize("mode", ["delegate", "explore", "write", "shell"])
@pytest.mark.asyncio
async def test_start_freeform_modes_keep_lightweight_stages_and_explicit_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, mode: tool_descriptions.StartMode
) -> None:
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    requested = _recording_candidates(monkeypatch)
    models = ["review", "review_fast", "chores", "chores_fast", "write", "codex:model/high"]
    async with _manager_with_fake("codex") as (manager, _backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        for model_type in models:
            if mode == "shell":
                response = await mcp_tools.start(
                    str(tmp_path),
                    mode=mode,
                    model_type=model_type,
                    **_OVERRIDE,
                    command="git status",
                    summary_policy="終了状態",
                )
            else:
                response = await mcp_tools.start(str(tmp_path), mode=mode, model_type=model_type, **_OVERRIDE, prompt="調査")
            assert (await manager.show_session(response["session_id"]))["model_type"] == model_type
        assert requested == models


@pytest.mark.asyncio
async def test_start_launches_repository_process_task_with_execute(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    manager = SimpleNamespace(
        start=AsyncMock(return_value={"session_id": "repo", "status": "running", "label": "repository-process"}),
        launcher_session_id=lambda: None,
        launcher_record_context=lambda: {"status": "unresolved"},
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = _observed_input_params("repository-process.subagent.md", tmp_path)
    for removed in ("ownership_record", "ownership_token"):
        params.pop(removed, None)
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 作業契約\n", encoding="utf-8")

    response = await mcp_tools.start(
        str(tmp_path), subagent_md_path="repository-process", extra_params=params, whole_contract_path=str(contract)
    )

    assert response["session_id"] == "repo"
    assert manager.start.await_args.args[0] == "execute"
    assert manager.start.await_args.kwargs["launch_kind"] == "delegate"


@pytest.mark.asyncio
async def test_start_routes_each_mode_to_launch_kind_model_and_label(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setattr(model_override, "require", lambda *_args, **_kwargs: None)
    requested: list[str] = []

    def candidates(model_type: str, **_kwargs: Any) -> list[tuple[str, str, str]]:
        requested.append(model_type)
        return [("codex", "model", "high")]

    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", candidates)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        task_document = launch_requests.resolve_task_document_path("exec")
        contract = tmp_path / "work-contract.md"
        contract.write_text("目的: 各リポジトリの履歴を読み取って照合する。", encoding="utf-8")
        calls: dict[str, dict[str, Any]] = {
            "task": {
                "subagent_md_path": str(task_document),
                "extra_params": _observed_input_params(task_document.name, tmp_path),
            },
            "delegate": {"prompt": "監査\n詳細", "model_type": "chores", "whole_contract_path": str(contract)},
            "explore": {"mode": "explore", "prompt": "調査", "label": "explore-対象"},
            "write": {"mode": "write", "prompt": "起草", **_OVERRIDE},
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
            "delegate": "delegate",
            "explore": "explore-対象",
            "write": "write",
            "shell": "shell-make",
        }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "reason", "evidence"),
    [
        ("codex:luna/low", None, None),
        ("codex:luna/low", "observation", " "),
        ("claude:haiku,codex:sol", "observation", "対照実験"),
    ],
)
async def test_direct_override_refused_before_backend_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    model: str,
    reason: Literal["observation"] | None,
    evidence: str | None,
) -> None:
    manager = SimpleNamespace(start_explore=AsyncMock())
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    with pytest.raises(ActionableError, match="model_override|haiku"):
        await mcp_tools.start(
            str(tmp_path),
            mode="explore",
            prompt="観測",
            model_type=model,
            model_override=reason,
            model_override_evidence=evidence,
        )
    manager.start_explore.assert_not_awaited()


@pytest.mark.parametrize("route", ["delegate", "write", "task"])
@pytest.mark.asyncio
async def test_start_uses_configured_write_without_model_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    route: str,
) -> None:
    require = Mock(side_effect=AssertionError("設定済みの工程を本人発話と照合した"))
    monkeypatch.setattr(model_override, "require", require)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", "codex:drafting/medium")
    contract = tmp_path / "work-contract.md"
    contract.write_text("目的: 文案を起草する。", encoding="utf-8")
    manifest = tmp_path / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name": "agent-toolkit"}', encoding="utf-8")
    task = tmp_path / "skills" / "atk-mode" / "roles" / "draft.subagent.md"
    task.parent.mkdir(parents=True)
    task.write_text(
        "---\nmode: write\nmodel_type: write\nusage: chores\n"
        "required_inputs: []\nwrites_worktree: false\n---\n\n# 文案の起草\n",
        encoding="utf-8",
    )
    inputs = {
        "delegate": {"prompt": "見出しの文案を作る", "model_type": "write", "whole_contract_path": str(contract)},
        "write": {"prompt": "起草"},
        "task": {"subagent_md_path": str(task)},
    }
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        result = await mcp_tools.mcp.call_tool("start", {"cwd": str(tmp_path), "mode": route, **inputs[route]})
        assert isinstance(result, CallToolResult)
        assert result.structured_content is not None
        assert manager.sessions[result.structured_content["session_id"]].model_type == "write"
        assert "warnings" not in result.structured_content
        assert backend.start_calls == [("drafting", "medium", "delegate" if route == "delegate" else "write")]
        require.assert_not_called()


@pytest.mark.asyncio
async def test_freeform_execute_and_exec_use_execute_model_not_write_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "codex:implementation/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_WRITE_MODEL", "codex:drafting/medium")
    contract = tmp_path / "work-contract.md"
    contract.write_text("目的: 割り当て済みの範囲を実装する。", encoding="utf-8")
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        delegated = await mcp_tools.start(
            str(tmp_path),
            mode="delegate",
            prompt="割り当て済み範囲を実装する",
            model_type="execute",
            whole_contract_path=str(contract),
        )
        role = await mcp_tools.start(
            str(tmp_path), subagent_md_path="exec", extra_params=_observed_input_params("exec.subagent.md", tmp_path)
        )
        write = await mcp_tools.start(str(tmp_path), mode="write", prompt="見出しの文案を作る")
        assert [manager.sessions[item["session_id"]].model_type for item in (delegated, role, write)] == [
            "execute",
            "execute",
            "write",
        ]
        assert backend.start_calls == [
            ("implementation", "high", "delegate"),
            ("implementation", "high", "delegate"),
            ("drafting", "medium", "write"),
        ]
