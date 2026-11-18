# pylint: disable=protected-access
"""役割文書を移行前（`share/`）と移行先（`skills/atk-mode/roles/`）のどちらに置いても、同じ起動と検査になることを確かめる。"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools, result_contract, state
from agent_toolkit._common import read_triggers, task_documents
from agent_toolkit._testing.launch_requests_support import TaskManager

_ROLE = "located"
_DOCUMENT = (
    "---\nmode: delegate\nmodel_type: review\nresult_heads:\n- '判定:'\n"
    "required_inputs:\n- 対象\n- 目的\noptional_inputs:\n- 補足\nwrites_worktree: false\n---\n\n"
    "# 担当\n\n## 出力\n\n以下の形式のみを返す。\n\n```text\n判定: 合格 | 修正要\n```\n"
)

pytestmark = pytest.mark.parametrize("directory", task_documents.ROLE_DOCUMENT_DIRECTORIES)


@pytest.fixture(name="plugin")
def _plugin(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """サーバー自身のplugin rootを、役割文書を持たない一時のagent-toolkitへ向ける。"""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    monkeypatch.setattr(launch_requests.plugin_root, "SERVER_PLUGIN_ROOT", root)
    return root


def _place(plugin: pathlib.Path, directory: str, name: str = f"{_ROLE}.subagent.md", text: str = _DOCUMENT) -> pathlib.Path:
    path = plugin.joinpath(*directory.split("/")) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _manager(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    prompts: list[str] = []

    async def fake_start(*args: Any, **_kwargs: object) -> dict[str, Any]:
        prompts.append(args[1])
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    return prompts


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["role-name", "absolute-path"])
async def test_start_launches_role_from_either_location(
    directory: str, form: str, plugin: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """役割名と絶対パスのどちらでも、置き場所によらず同じ文書を出所にして起動する。"""
    document = _place(plugin, directory)
    prompts = _manager(monkeypatch)
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 契約\n目的: 置き場所の検証\n", encoding="utf-8")

    await mcp_tools.start(
        str(tmp_path),
        subagent_md_path=_ROLE if form == "role-name" else str(document),
        extra_params={"対象": "値", "目的": "値"},
        whole_contract_path=str(contract),
    )

    assert prompts and prompts[0].splitlines()[0] == task_documents.task_prompt_header(document)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    [({"対象": "値"}, "目的"), ({"対象": "値", "目的": "値", "宣言外": "値"}, "宣言外")],
    ids=["missing-required", "undeclared"],
)
async def test_declared_input_check_is_the_same_in_either_location(
    directory: str,
    params: dict[str, str],
    expected: str,
    plugin: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """必須入力の欠けと宣言外の入力は、置き場所によらず起動前に拒否する。"""
    _place(plugin, directory)
    prompts = _manager(monkeypatch)

    with pytest.raises(ValueError, match=expected):
        await mcp_tools.start(str(tmp_path), subagent_md_path=_ROLE, extra_params=params)

    assert not prompts


def test_result_form_check_is_the_same_in_either_location(directory: str, plugin: pathlib.Path) -> None:
    """返却形式の検査は、置き場所によらず宣言した先頭行を受理し、形式外の返却を拒否する。"""
    document = _place(plugin, directory)
    declaration = task_documents.read_declaration(document)
    assert isinstance(declaration, task_documents.TaskDocumentDeclaration)
    assert declaration.result_heads == ("判定:",) and declaration.required == ("対象", "目的")
    session = state.SessionState(
        "role-form",
        str(document.parent),
        prompt=task_documents.task_prompt_header(document),
        launch_kind=declaration.launch_kind,
    )
    assert result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": "判定: 合格"}) is None
    assert result_contract.invalid_result_reason(session, {"status": "completed", "agent_message": "作業を終えた。"})


def test_unknown_role_lists_roles_from_both_locations(directory: str, plugin: pathlib.Path) -> None:
    """解決できない役割名の診断は、両方の置き場所の役割名を示す。"""
    _place(plugin, directory)
    other = next(value for value in task_documents.ROLE_DOCUMENT_DIRECTORIES if value != directory)
    _place(plugin, other, "other.subagent.md")

    with pytest.raises(ValueError) as rejected:
        launch_requests.resolve_task_document_path("missing")

    assert "受理する役割名: located, other。" in str(rejected.value)


def test_parent_contract_resolves_across_locations(directory: str, plugin: pathlib.Path) -> None:
    """起動・受領契約は、役割文書と同じ置き場所でも別の置き場所でも解決する。"""
    document = _place(plugin, directory)
    for contract_directory in task_documents.ROLE_DOCUMENT_DIRECTORIES:
        contract = _place(plugin, contract_directory, f"{_ROLE}.parent.md", "# 起動契約\n")
        for role in (_ROLE, str(document)):
            assert [target.path for target in read_triggers.parent_contract_targets(role, plugin)] == [contract]
        contract.unlink()


def test_documents_outside_role_locations_are_rejected(directory: str, plugin: pathlib.Path) -> None:
    """置き場所の外の文書と、agent-toolkit以外のpluginの文書は役割文書として扱わない。"""
    assert task_documents.is_agent_toolkit_task_document(_place(plugin, directory))
    assert not task_documents.is_agent_toolkit_task_document(_place(plugin, "skills/other"))
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"other"}', encoding="utf-8")
    assert not task_documents.is_agent_toolkit_task_document(_place(plugin, directory))
