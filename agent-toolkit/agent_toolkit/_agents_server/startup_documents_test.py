"""委譲先の起動の本文へ資料の全文が入ることを、公開の`start`から確かめる。"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess

import pytest

from agent_toolkit._agents_server import mcp_tools
from agent_toolkit._agents_server.plugin_root import SERVER_PLUGIN_ROOT
from agent_toolkit._common import plugin_roots, startup_documents, task_documents
from agent_toolkit._testing.agents_server_support import _manager_with_fake, _observed_input_params, _recording_candidates
from agent_toolkit._testing.launch_requests_support import _prepare_exec_review_plan

pytestmark = pytest.mark.usefixtures("agents_server_isolation")

_BASICS = SERVER_PLUGIN_ROOT.parent / "agent-basics" / "skills"
_ATK_MODE = SERVER_PLUGIN_ROOT / "skills" / "atk-mode"
_COMMON_BODIES = (
    _BASICS / "writing-standards/references/quality-principles.md",
    _BASICS / "writing-standards/references/quality-principles-facts.md",
    _ATK_MODE / "references/work-contract.md",
)


def _body(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8").rstrip("\n")


async def _delivered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, role: str, params: dict[str, str] | None = None
) -> str:
    """役割を公開の`start`で起動し、backendへ渡った起動の本文を返す。"""
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 全体作業契約\n目的: 起動時の配送の検証\n", encoding="utf-8")
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=role,
            extra_params=params if params is not None else _observed_input_params(f"{role}.subagent.md", tmp_path),
            whole_contract_path=str(contract),
        )
        return backend.prompts[-1]


@pytest.mark.asyncio
async def test_role_launch_delivers_common_and_declared_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """通常の委譲の役割には、共通の資料と役割が宣言した手順書の全文が届く。"""
    _prepare_exec_review_plan(tmp_path)
    prompt = await _delivered(monkeypatch, tmp_path, "exec-review")
    for path in (*_COMMON_BODIES, _ATK_MODE / "playbooks/exec-review.md"):
        assert _body(path) in prompt, path


@pytest.mark.parametrize(("kind", "delivered"), [("レーン担当", True), ("CI修正担当", False)])
@pytest.mark.asyncio
async def test_conditional_document_follows_launch_input(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, kind: str, delivered: bool
) -> None:
    """`when`の入力が一致した起動だけに条件付きの資料が届き、共通の資料は条件によらず届く。"""
    params = _observed_input_params("exec.subagent.md", tmp_path) | {"担当種別": kind}
    prompt = await _delivered(monkeypatch, tmp_path, "exec", params)
    assert (_body(_ATK_MODE / "playbooks/process-wi-execution.md") in prompt) is delivered
    assert all(_body(path) in prompt for path in _COMMON_BODIES)


@pytest.mark.asyncio
async def test_lightweight_role_receives_only_its_declared_documents(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """軽量の起動の役割には、役割が宣言した資料だけが届き、通常の委譲の共通の資料は入らない。"""
    params = {"判定規則": "固定文字列の一致", "母集団": "agent-toolkit/skills配下の.md", "判定基準": "同じ誤りの再発"}
    prompt = await _delivered(monkeypatch, tmp_path, "similar-review", params)
    assert _body(_BASICS / "bugfix/references/spread.md") in prompt
    assert not any(_body(path) in prompt for path in _COMMON_BODIES)


def _role_plugin(root: pathlib.Path, startup: str) -> pathlib.Path:
    """`startup_documents`を宣言した役割文書と手順書を持つ、agent-toolkitと同じ構造の配布物を作る。"""
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    role = root / "skills/atk-mode/roles/probe.subagent.md"
    role.parent.mkdir(parents=True)
    role.write_text(
        "---\nmode: delegate\nmodel_type: chores\nrequired_inputs:\n- 対象\noptional_inputs: []\n"
        f"writes_worktree: false\n{startup}---\n\n# 確認担当\n\n## 入力\n\n対象を受け取る。\n",
        encoding="utf-8",
    )
    return role


@pytest.mark.asyncio
async def test_revised_document_is_delivered_on_next_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """資料を改訂した後の起動には、改訂後の本文と新しい本文識別子が届く。"""
    role = _role_plugin(tmp_path / "plugin", "startup_documents:\n- agent-toolkit:atk-mode/playbooks/probe.md\n")
    playbook = role.parents[1] / "playbooks/probe.md"
    playbook.parent.mkdir()
    playbook.write_text("# 確認の手順\n\n改訂前の手順。\n", encoding="utf-8")
    first = await _delivered(monkeypatch, tmp_path, str(role), {"対象": "値"})
    playbook.write_text("# 確認の手順\n\n改訂後の手順。\n", encoding="utf-8")
    second = await _delivered(monkeypatch, tmp_path, str(role), {"対象": "値"})
    assert "改訂前の手順。" in first and "改訂前の手順。" not in second
    assert "改訂後の手順。" in second
    assert startup_documents.delivered_versions(first)[playbook] != startup_documents.delivered_versions(second)[playbook]


@pytest.mark.asyncio
async def test_missing_declared_document_rejects_launch(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """宣言した資料が実在しない役割は、委譲先を起動せずに失敗する。"""
    role = _role_plugin(tmp_path / "plugin", "startup_documents:\n- agent-toolkit:atk-mode/playbooks/absent.md\n")
    with pytest.raises(Exception, match="absent.md"):
        await _delivered(monkeypatch, tmp_path, str(role), {"対象": "値"})


def test_missing_document_in_selected_installation_names_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """Claude Codeの導入一覧から選んだ導入先に資料が欠けていれば、同じscopeの導入を修復する操作を示して失敗する。"""
    toolkit = tmp_path / "cache/mine/agent-toolkit/9"
    basics = tmp_path / "basics"
    (basics / "skills/writing-standards/references").mkdir(parents=True)
    executable = tmp_path / "claude"
    executable.touch()
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: str(executable))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "config"))
    listing = json.dumps(
        [
            {"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)},
            {"id": "agent-basics@mine", "scope": "user", "enabled": True, "installPath": str(basics)},
        ]
    )
    monkeypatch.setattr(
        plugin_roots.process_tree,
        "run_with_timeout",
        lambda args, **_kwargs: subprocess.CompletedProcess(args, 0, listing, ""),
    )
    with (
        plugin_roots.plugin_resolution_scope(),
        pytest.raises(plugin_roots.PluginRootError, match="claude plugin update agent-basics@mine --scope user"),
    ):
        startup_documents.resolve("agent-basics:writing-standards/references/quality-principles.md", toolkit, project=tmp_path)


@pytest.mark.parametrize(
    "startup",
    [
        "startup_documents: agent-toolkit:atk-mode/playbooks/probe.md\n",
        "startup_documents:\n- playbooks/probe.md\n",
        "startup_documents:\n- other-plugin:atk-mode/playbooks/probe.md\n",
        "startup_documents:\n- document: agent-toolkit:atk-mode/playbooks/probe.md\n  when:\n    宣言外: 値\n",
        "startup_documents:\n- document: agent-toolkit:atk-mode/playbooks/probe.md\n  when: 対象\n",
        "startup_documents:\n- document: agent-toolkit:atk-mode/playbooks/probe.md\n  extra: 値\n",
    ],
)
def test_malformed_startup_documents_are_rejected(tmp_path: pathlib.Path, startup: str) -> None:
    """形式の誤り（配列でない、参照の形、未知のplugin、宣言外の入力名、whenの形、未知の項目）を宣言の解析が拒否する。"""
    declaration = task_documents.read_declaration_unchecked(_role_plugin(tmp_path / "plugin", startup))
    assert isinstance(declaration, str) and "startup_documents" in declaration
