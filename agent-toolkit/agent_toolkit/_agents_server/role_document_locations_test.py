# pylint: disable=protected-access
"""役割文書を置き場所（`skills/atk-mode/roles/`）から解決し、古い置き場所`share/`の文書を役割文書として扱わないことを確かめる。"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest

from agent_toolkit._agents_server import launch_requests, launch_requests_inputs, mcp_tools, result_contract, state
from agent_toolkit._common import task_documents
from agent_toolkit._testing.launch_requests_support import TaskManager

_ROLE = "located"
_DOCUMENT = (
    "---\nmode: delegate\nmodel_type: review\nresult_heads:\n- '判定:'\n"
    "required_inputs:\n- 対象\n- 目的\noptional_inputs:\n- 補足\nwrites_worktree: false\n---\n\n"
    "# 担当\n\n## 出力\n\n以下の形式のみを返す。\n\n```text\n判定: 合格 | 修正要\n```\n"
)


@pytest.fixture(name="plugin")
def _plugin(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """サーバー自身のplugin rootを、役割文書を持たない一時のagent-toolkitへ向ける。"""
    root = tmp_path / "plugin"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin" / "plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    monkeypatch.setattr(launch_requests.plugin_root, "SERVER_PLUGIN_ROOT", root)
    return root


def _place(
    plugin: pathlib.Path,
    directory: str = task_documents.ROLE_DOCUMENT_DIRECTORY,
    name: str = f"{_ROLE}.subagent.md",
    text: str = _DOCUMENT,
) -> pathlib.Path:
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


def _contract(tmp_path: pathlib.Path) -> pathlib.Path:
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 契約\n目的: 置き場所の検証\n", encoding="utf-8")
    return contract


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["role-name", "absolute-path"])
async def test_start_launches_role_from_role_directory(
    form: str, plugin: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """役割名と絶対パスのどちらでも、置き場所の文書を出所にして起動する。"""
    document = _place(plugin)
    prompts = _manager(monkeypatch)

    await mcp_tools.start(
        str(tmp_path),
        subagent_md_path=_ROLE if form == "role-name" else str(document),
        extra_params={"対象": "値", "目的": "値"},
        whole_contract_path=str(_contract(tmp_path)),
    )

    assert prompts and prompts[0].splitlines()[0] == task_documents.task_prompt_header(document)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("form", "expected"),
    [("role-name", "役割名に対応する文書が無い"), ("absolute-path", "not an agent-toolkit task document")],
)
async def test_start_rejects_role_left_in_share(
    form: str, expected: str, plugin: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """古い置き場所`share/`に残った文書は、役割名でも絶対パスでも起動しない。"""
    document = _place(plugin, "share")
    prompts = _manager(monkeypatch)

    with pytest.raises(ValueError, match=expected):
        await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=_ROLE if form == "role-name" else str(document),
            extra_params={"対象": "値", "目的": "値"},
            whole_contract_path=str(_contract(tmp_path)),
        )

    assert not prompts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("params", "expected"),
    [({"対象": "値"}, "目的"), ({"対象": "値", "目的": "値", "宣言外": "値"}, "宣言外")],
    ids=["missing-required", "undeclared"],
)
async def test_declared_input_check_rejects_before_launch(
    params: dict[str, str],
    expected: str,
    plugin: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """必須入力の欠けと宣言外の入力は、起動前に拒否する。"""
    _place(plugin)
    prompts = _manager(monkeypatch)

    with pytest.raises(ValueError, match=expected):
        await mcp_tools.start(str(tmp_path), subagent_md_path=_ROLE, extra_params=params)

    assert not prompts


def test_result_form_check_uses_declared_heads(plugin: pathlib.Path) -> None:
    """返却形式の検査は、宣言した先頭行を受理し、形式外の返却を拒否する。"""
    document = _place(plugin)
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


def test_unknown_role_lists_only_roles_in_role_directory(plugin: pathlib.Path) -> None:
    """解決できない役割名の診断は、置き場所の役割名だけを示し、`share/`に残った文書を含めない。"""
    _place(plugin)
    _place(plugin, name="other.subagent.md")
    _place(plugin, "share", "stale.subagent.md")

    with pytest.raises(ValueError) as rejected:
        launch_requests.resolve_task_document_path("missing")

    assert "受理する役割名: located, other。" in str(rejected.value)


def test_parent_contract_resolves_in_role_directory(plugin: pathlib.Path) -> None:
    """入力の拒否が案内する起動・受領契約は、役割文書と同じ置き場所から解決する。"""
    document = _place(plugin)
    contract = _place(
        plugin,
        name=f"{_ROLE}.parent.md",
        text=f"# 起動契約\n\n```text\n起動対象: {_ROLE}.subagent.md\n```\n\n## 入力\n\n対象と目的を渡す。\n",
    )
    assert f"`{contract}`「入力」" in launch_requests_inputs._input_contract_guidance(document)


@pytest.mark.parametrize("outside", ["share", "skills/other"])
def test_documents_outside_role_directory_are_rejected(outside: str, plugin: pathlib.Path) -> None:
    """置き場所の外の文書と、agent-toolkit以外のpluginの文書は役割文書として扱わない。"""
    assert task_documents.is_agent_toolkit_task_document(_place(plugin))
    assert not task_documents.is_agent_toolkit_task_document(_place(plugin, outside))
    (plugin / ".claude-plugin" / "plugin.json").write_text('{"name":"other"}', encoding="utf-8")
    assert not task_documents.is_agent_toolkit_task_document(_place(plugin))
