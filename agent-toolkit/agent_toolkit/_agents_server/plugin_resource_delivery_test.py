"""公開起動・追送・保存再開から、正式解決した品質資料の配送を検証する。"""

from __future__ import annotations

import json
import pathlib
import shutil
import uuid
from typing import Any

import pytest

from agent_toolkit._agents_server import (
    antigravity,
    backends,
    claude,
    claude_settings,
    cli_turn,
    codex,
    manager,
    manager_resume,
    mcp_tools,
    opencode,
    plugin_root,
    state,
    tool_descriptions,
)
from agent_toolkit._common import plugin_roots, session_registry
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import FakeBackend, install_backend

pytestmark = pytest.mark.usefixtures("agents_server_isolation")

_ENGINES = ("claude", "codex", "agy", "opencode")
_CANDIDATES = {
    "claude": "claude:test/medium",
    "codex": "codex:gpt-6-sol/medium",
    "agy": "agy:test/medium",
    "opencode": "opencode:test/model@medium",
}


class ResourceInventory:
    """各host・projectの公開一覧と、そこに登録された実在資料を用意する。"""

    def __init__(self, root: pathlib.Path, toolkit: pathlib.Path) -> None:
        self.root = root
        self.toolkit = toolkit
        self.generation = 1
        self.defect = ""
        self.calls: list[tuple[str, pathlib.Path]] = []

    def basics(self, host: str, project: pathlib.Path, generation: int | None = None) -> pathlib.Path:
        """toolkitと独立した版・配置の品質資料rootを返す。"""
        version = f"1.{generation or self.generation}.{project.name}"
        if host == "codex":
            return self.root / "codex/plugins/cache/mine/agent-basics" / version
        parent = self.root / "installed" / host / project.name / version
        return parent / ("agent-basics-agy" if host == "agy" else "agent-basics")

    def inventory(self, host: str, project: pathlib.Path) -> object:
        """ホスト別の正式一覧形式で、欠落・無効・重複も再現する。"""
        self.calls.append((host, project))
        root = self.basics(host, project)
        paths = [] if self.defect == "missing" else [root]
        if self.defect == "ambiguous":
            paths.append(self.basics(host, project, 2))
        enabled = self.defect != "disabled"
        if host == "codex":
            return {
                "installed": [
                    {
                        "name": "agent-toolkit",
                        "marketplaceName": "mine",
                        "version": "2.248.0",
                        "installed": True,
                        "enabled": True,
                    },
                    *[
                        {
                            "name": "agent-basics",
                            "marketplaceName": "mine",
                            "version": path.name,
                            "installed": True,
                            "enabled": enabled,
                        }
                        for path in paths
                    ],
                ]
            }
        if host == "claude":
            return [
                {"id": "agent-toolkit@mine", "installPath": str(self.toolkit), "scope": "user", "enabled": True},
                *[
                    {
                        "id": "agent-basics@mine",
                        "installPath": str(path),
                        "scope": "project",
                        "projectPath": str(project),
                        "enabled": enabled,
                    }
                    for path in paths
                ],
            ]
        if host == "opencode":
            return [("agent-toolkit", str(self.toolkit)), *[("agent-basics", str(path)) for path in paths]]
        return (
            [{"path": str(path.parent), "include_only": ["agent-basics-agy"]} for path in paths],
            {"agent-basics-agy": {"enabled": enabled}},
        )


@pytest.fixture(name="resources")
def _resources(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> ResourceInventory:
    """保持toolkitと導入basicsを分離し、解決処理の外部一覧だけを差し替える。"""
    source = plugin_root.SERVER_PLUGIN_ROOT
    toolkit = tmp_path / "held/toolkit-2.248.0"
    for directory in ("skills", "share", "rules", "scripts", ".claude-plugin"):
        (toolkit / directory).mkdir(parents=True, exist_ok=True)
    for relative in (".claude-plugin/plugin.json", ".mcp.json", "share/rules-subagent.md", "scripts/atk", "scripts/atk.cmd"):
        shutil.copyfile(source / relative, toolkit / relative)
    for path in (source / "rules").glob("*.md"):
        shutil.copyfile(path, toolkit / "rules" / path.name)
    task = toolkit / "share/resource-probe.subagent.md"
    task.write_text(
        "---\nmode: delegate\nmodel_type: chores\nrequired_inputs: []\noptional_inputs: []\nwrites_worktree: true\n---\n"
        "# 資料配送確認担当\n\n受領した品質資料を参照する。\n\n"
        "| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
        "| --- | --- | --- | --- |\n"
        "| 起動時 | `agent-basics:writing-standards/references/writing.md` | 起動時 | |\n",
        encoding="utf-8",
    )
    inventory = ResourceInventory(tmp_path, toolkit)
    for project_name in ("project-a", "project-b"):
        project = tmp_path / project_name
        project.mkdir()
        for host in _ENGINES:
            for generation in (1, 2):
                root = inventory.basics(host, project, generation)
                material = root / "skills/writing-standards/SKILL.md"
                material.parent.mkdir(parents=True)
                material.write_text(f"# 品質基準\n{host}/{project.name}/{generation}\n", encoding="utf-8")
                required = material.parent / "references/writing.md"
                required.parent.mkdir()
                required.write_text(f"# 必読資料\n{host}/{project.name}/{generation}の必読本文\n", encoding="utf-8")
                manifest = root / ("plugin.json" if host == "agy" else ".claude-plugin/plugin.json")
                manifest.parent.mkdir(parents=True, exist_ok=True)
                manifest.write_text(json.dumps({"name": "agent-basics"}), encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setattr(plugin_root, "SERVER_PLUGIN_ROOT", toolkit)
    monkeypatch.setattr(manager, "SERVER_PLUGIN_ROOT", toolkit)
    monkeypatch.setattr(manager_resume, "SERVER_PLUGIN_ROOT", toolkit)
    monkeypatch.setattr(plugin_roots, "_plugin_inventory", inventory.inventory)
    return inventory


class ResourceBackend(FakeBackend):
    """外部接続を省き、実際のbackend指示生成と配送本文を記録する。"""

    def __init__(self, sessions: dict[str, state.SessionState], engine: str) -> None:
        super().__init__(sessions, engine)
        self.delivered: list[str] = []
        self.publish_registry = True
        self.reject_start = False

    def capture(self, prompt: str, cwd: str, launch_kind: state.LaunchKind) -> None:
        """実backendが用いる指示生成を通し、品質資料への到達を観測可能にする。"""
        if self.engine == "codex":
            system = codex._developer_instructions(launch_kind)  # noqa: SLF001  # pylint: disable=protected-access
        elif self.engine == "claude":
            options = claude._build_options(cwd, "test", "medium", launch_kind=launch_kind)  # noqa: SLF001  # pylint: disable=protected-access
            try:
                system = options.system_prompt
            finally:
                claude_settings.cleanup_settings(options)
        elif self.engine == "agy":
            system = antigravity.AntigravityManager.system_prompt(launch_kind)
        else:
            system = opencode.system_prompt(launch_kind)
        assert isinstance(system, str)
        self.delivered.append(system + "\n" + prompt)

    async def start(self, prompt: str, cwd: str, *args: Any, **kwargs: Any) -> state.SessionState:
        self.capture(prompt, cwd, kwargs["launch_kind"])
        if self.reject_start:
            raise backends.StartCandidateUnavailable("試験用のengine起動拒否", scope="launch_engine")
        session = await super().start(prompt, cwd, *args, **kwargs)
        self.sessions.pop(session.session_id)
        session.session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{self.engine}:{cwd}:{len(self.start_calls)}"))
        self.sessions[session.session_id] = session
        session.publish_registry = True
        session.model_output_observed = True
        return session

    async def send_message(self, session: state.SessionState, prompt: str) -> dict[str, Any]:
        self.capture(prompt, session.cwd, session.launch_kind)
        return await super().send_message(session, prompt)

    async def resume(
        self, session_id: str, prompt: state.ResumePrompt, cwd: str, model: str | None, effort: str | None, **kwargs: Any
    ) -> state.SessionState:
        self.resume_calls.append(session_id)
        session = state.SessionState(
            session_id,
            cwd,
            engine=self.engine,
            model=model,
            effort=effort,
            model_type=kwargs.get("model_type"),
            launch_kind=kwargs.get("launch_kind", "delegate"),
            turn_seq=kwargs.get("turn_seq", 0) + 1,
            publish_registry=True,
        )
        self.sessions[session_id] = session
        state.initialize_turn(session)

        async def deliver(value: str) -> None:
            self.capture(value, cwd, session.launch_kind)

        await prompt.deliver(deliver)
        session.model_output_observed = True
        return session


def _assert_resources(body: str, resources: ResourceInventory, host: str, project: pathlib.Path) -> None:
    toolkit = resources.toolkit
    expected = resources.basics(host, project)
    notice = next(line for line in body.splitlines() if line.startswith("agent-basics plugin root: "))
    basics = pathlib.Path(notice.removeprefix("agent-basics plugin root: "))
    assert basics == expected
    assert body.count(f"agent-toolkit plugin root: {toolkit}\n") == 1
    assert body.count(f"agent-basics plugin root: {basics}\n") == 1
    assert "`agent-basics:`" in body and "`agent-toolkit:`" in body
    assert (basics / "skills/writing-standards/SKILL.md").read_text(encoding="utf-8") == (
        f"# 品質基準\n{host}/{project.name}/{resources.generation}\n"
    )
    assert (host, project) in resources.calls


async def _start(
    mode: tool_descriptions.StartMode, resources: ResourceInventory, project: pathlib.Path, candidates: str
) -> dict[str, Any]:
    whole = resources.root / "work-contract.md"
    whole.write_text("# 作業契約\n目的: 品質資料の配送確認\n", encoding="utf-8")
    if mode == "task":
        return await mcp_tools.start(
            cwd=str(project),
            subagent_md_path=str(resources.toolkit / "share/resource-probe.subagent.md"),
            extra_params={},
            model_type=candidates,
            whole_contract_path=str(whole),
        )
    if mode == "shell":
        return await mcp_tools.start(
            cwd=str(project), mode=mode, command="pwd", summary_policy="終了状態", model_type=candidates
        )
    return await mcp_tools.start(
        cwd=str(project),
        mode=mode,
        prompt="品質資料を参照する",
        model_type=candidates,
        whole_contract_path=str(whole) if mode == "delegate" else None,
    )


@pytest.mark.parametrize("host", _ENGINES)
@pytest.mark.parametrize("mode", ["task", "delegate", "explore", "shell", "write"])
@pytest.mark.asyncio
async def test_public_start_delivers_both_resource_roots(
    resources: ResourceInventory, monkeypatch: pytest.MonkeyPatch, host: str, mode: tool_descriptions.StartMode
) -> None:
    """全engine・起動形で、実配送したrootから現在projectの品質資料を読める。"""
    project = resources.root / "project-b"
    async with manager.AgentsServerManager(None) as current:
        backend = ResourceBackend(current.sessions, host)
        install_backend(current, host, backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        response = await _start(mode, resources, project, _CANDIDATES[host])
        assert response["launch_identity"]["engine"] == host and response["status"] == "running"
        _assert_resources(backend.delivered[-1], resources, host, project)
        if mode == "task":
            assert "資料配送確認担当" in backend.delivered[-1]
            assert f"{host}/{project.name}/1の必読本文" in backend.delivered[-1]
        elif mode == "shell":
            assert "pwd" in backend.delivered[-1]
        else:
            assert "品質資料を参照する" in backend.delivered[-1]


@pytest.mark.parametrize("failure", ["inventory", "backend"])
@pytest.mark.asyncio
async def test_public_start_resolves_resources_again_after_engine_fallback(
    resources: ResourceInventory, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """先頭engineの資料未導入を候補選択へ渡し、次engineの実在資料だけを配送する。"""
    original = resources.inventory

    def inventory(host: str, project: pathlib.Path) -> object:
        resources.defect = "missing" if host == "codex" and failure == "inventory" else ""
        return original(host, project)

    monkeypatch.setattr(plugin_roots, "_plugin_inventory", inventory)
    project = resources.root / "project-a"
    async with manager.AgentsServerManager(None) as current:
        first = ResourceBackend(current.sessions, "codex")
        first.reject_start = failure == "backend"
        second = ResourceBackend(current.sessions, "claude")
        install_backend(current, "codex", first)
        install_backend(current, "claude", second)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        response = await _start("explore", resources, project, _CANDIDATES["codex"] + "," + _CANDIDATES["claude"])
        assert response["launch_identity"]["engine"] == "claude" and response["excluded_candidates"]
        assert not first.start_calls
        if failure == "backend":
            _assert_resources(first.delivered[-1], resources, "codex", project)
        _assert_resources(second.delivered[-1], resources, "claude", project)
        assert ("codex", project) in resources.calls
        assert str(resources.basics("codex", project)) not in second.delivered[-1]


@pytest.mark.parametrize("host", _ENGINES)
@pytest.mark.parametrize("saved", [False, True])
@pytest.mark.asyncio
async def test_public_send_message_refreshes_resource_roots(
    resources: ResourceInventory, monkeypatch: pytest.MonkeyPatch, host: str, saved: bool
) -> None:
    """通常追送と保存済み再開で、現在のhost/cwdの更新済み導入rootを再解決する。"""
    project = resources.root / "project-b"
    async with manager.AgentsServerManager(None) as first:
        backend = ResourceBackend(first.sessions, host)
        install_backend(first, host, backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", first)
        started = await _start("explore", resources, project, _CANDIDATES[host])
        identifier = started["session_id"]
        if saved:
            session = first.sessions[identifier]
            cli_turn.finalize_turn(session, {"status": "completed", "agent_message": "状態: completed", "error": None})
            await session.drain_registration()
        resources.generation = 2
        resources.calls.clear()
        if not saved:
            response = await mcp_tools.send_message(identifier, prompt="追加入力")
            assert response["delivery"] == "steered"
            _assert_resources(backend.delivered[-1], resources, host, project)
            return
    assert session_registry.resolve(identifier).resume_info is not None
    async with manager.AgentsServerManager(None) as restored:
        backend = ResourceBackend(restored.sessions, host)
        install_backend(restored, host, backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", restored)
        response = await mcp_tools.send_message(identifier, prompt="保存済みの仕事を再開")
        assert response["delivery"] == "reply_started" and backend.resume_calls == [identifier]
        _assert_resources(backend.delivered[-1], resources, host, project)


@pytest.mark.parametrize(
    ("host", "defect"),
    [(host, defect) for host in _ENGINES for defect in ("missing", "ambiguous")]
    + [(host, "disabled") for host in ("claude", "codex", "agy")],
)
@pytest.mark.asyncio
async def test_public_start_does_not_succeed_without_unique_enabled_basics(
    resources: ResourceInventory, monkeypatch: pytest.MonkeyPatch, host: str, defect: str
) -> None:
    """正式解決できない資料は起動前の診断とし、backendの起動成功へ変換しない。"""
    # OpenCodeの正式一覧には有効・無効の項目が無く、未登録と重複で検査する。
    resources.defect = defect
    async with manager.AgentsServerManager(None) as current:
        backend = ResourceBackend(current.sessions, host)
        install_backend(current, host, backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        with pytest.raises(ActionableError) as raised:
            await _start("explore", resources, resources.root / "project-a", _CANDIDATES[host])
        assert "agent-basics" in str(raised.value)
        assert not backend.start_calls and not current.sessions
