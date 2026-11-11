"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

import importlib.util

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib
import shutil
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit import _agents_server as server_package
from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._common import plugin_roots as resource_roots
from agent_toolkit._common import task_documents
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    _manager_with_fake,
    _recording_candidates,
    _write_declared_task_document,
    install_backend,
)
from agent_toolkit._testing.codex_plugin_cli import codex_plugin_cli
from agent_toolkit._testing.git_repository import commit_all, git_output, init_repository
from agent_toolkit._testing.helpers import delivery_payload
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    "host,case",
    [("agy", "missing"), ("opencode", "missing"), ("agy", "physical"), ("opencode", "physical"), ("agy", "sibling")],
)
@pytest.mark.parametrize("entry_available", [True, False])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_public_start_native_recovery_uses_an_available_generator(tmp_path, monkeypatch, host, case, entry_available):
    """native資料の公開start失敗で、検出した生成元の公開コマンドだけを案内する。"""
    document = _write_declared_task_document(tmp_path, "対象: 必須")
    if case == "sibling":
        original_root = document.parent.parent
        native = tmp_path / "agent-toolkit-agy"
        original_root.rename(native)
        document = native / "share" / document.name
        (native / "plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    with document.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n| 条件 | 全文読む資料 | 契機 | 配送操作 |\n| --- | --- | --- | --- |\n"
            "| 起動時 | `agent-basics:writing-standards/SKILL.md` | 起動時 | |\n"
        )
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "execute")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda: home)
    opencode = home / "opencode"
    opencode.mkdir()
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(opencode))
    if case == "physical":
        if host == "opencode":
            (opencode / "agent-toolkit.md").write_text(
                '<atk-auto source="dotfiles" kind="opencode-host">\n'
                f"agent-basics plugin root: `{tmp_path / 'missing-basics'}`。\n",
                encoding="utf-8",
            )
        else:
            registry = home / ".gemini/config/plugins.json"
            registry.parent.mkdir(parents=True)
            registry.write_text(
                json.dumps({"entries": [{"path": str(tmp_path), "include_only": ["agent-basics-agy"]}]}), encoding="utf-8"
            )
    generator = tmp_path / "bin" / ("update-dotfiles.cmd" if sys.platform == "win32" else "update-dotfiles")
    generator.parent.mkdir()
    generator.write_text("確認用の生成元入口", encoding="utf-8")
    generator.chmod(0o755)
    original_which = shutil.which

    def which(name, *args: Any, **kwargs: Any):
        if name == "update-dotfiles":
            return str(generator) if entry_available else None
        return original_which(name, *args, **kwargs)

    # 修正前も公開startを呼び出すため、標準ライブラリ側の検出境界を差し替える。
    monkeypatch.setattr(shutil, "which", which)
    monkeypatch.setattr(_atk_config, "parse_unresolved_model_candidates", lambda *_args, **_kw: [(host, "model", "high")])
    async with _manager_with_fake("claude") as (manager, _backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        with pytest.raises(RuntimeError) as caught:
            await mcp_tools.start(str(tmp_path), subagent_md_path=str(document), extra_params={"対象": "内容"})
    message = str(caught.value)
    if entry_available:
        assert str(generator) in message and "--mode full" in message and "公開コマンド" in message
    else:
        assert "update-dotfilesはPATHに無い" in message
        assert " info" in message
        assert "--mode full" not in message


@pytest.mark.asyncio
async def test_public_task_start_delivers_the_actual_child_host_and_project_materials(tmp_path, monkeypatch):
    """同じサーバーからhost/projectを切り替えても、子が受け取る品質本文を混ぜない。"""
    document = _write_declared_task_document(tmp_path, "対象: 必須")
    with document.open("a", encoding="utf-8") as stream:
        stream.write(
            "\n| 時点または条件 | 全文読む資料 | 契機 | 配送操作 |\n"
            "| --- | --- | --- | --- |\n"
            "| 起動時 | `agent-basics:writing-standards/references/writing.md` | 起動時 | |\n"
        )
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "execute")
    toolkit = document.parent.parent
    project_a, project_b = tmp_path / "project-a", tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    roots = {
        "user": tmp_path / "user-basics",
        "project": tmp_path / "project-basics",
        "local": tmp_path / "local-basics",
        "unrelated": tmp_path / "other-basics",
        "codex": codex_home / "plugins/cache/mine/agent-basics/1.0",
        "agy": tmp_path / "native/agent-basics-agy",
        "opencode": tmp_path / "original/agent-basics",
    }
    for label, root in roots.items():
        path = root / "skills/writing-standards/references/writing.md"
        path.parent.mkdir(parents=True)
        path.write_text(f"# {label}の基準\n{label}だけの品質本文\n", encoding="utf-8")
        manifest = root / ".claude-plugin/plugin.json"
        manifest.parent.mkdir()
        manifest.write_text('{"name":"agent-basics"}', encoding="utf-8")
        if label == "agy":
            (root / "plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(pathlib.Path, "home", lambda: home)
    registry = home / ".gemini/config/plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps({"entries": [{"path": str(tmp_path / "native"), "include_only": ["agent-basics-agy"]}]}),
        encoding="utf-8",
    )
    opencode_config = tmp_path / "opencode-config"
    opencode_config.mkdir()
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(opencode_config))
    (opencode_config / "agent-toolkit.md").write_text(
        '<atk-auto source="dotfiles" kind="opencode-host">\n'
        f"agent-toolkit plugin root: `{toolkit}`。agent-basics plugin root: `{roots['opencode']}`。\n",
        encoding="utf-8",
    )
    claude = [{"id": "agent-toolkit@mine", "scope": "user", "enabled": True, "installPath": str(toolkit)}]
    for scope in ("user", "project", "local"):
        claude.append(
            {
                "id": "agent-basics@mine",
                "scope": scope,
                "enabled": True,
                "installPath": str(roots[scope]),
                "projectPath": str(project_a),
            }
        )
    claude.append(
        {
            "id": "agent-basics@mine",
            "scope": "local",
            "enabled": True,
            "installPath": str(roots["unrelated"]),
            "projectPath": str(tmp_path / "unrelated"),
        }
    )
    codex = {
        "installed": [
            {"name": name, "marketplaceName": "mine", "enabled": True, "installed": True, "version": version}
            for name, version in (("agent-toolkit", "2.0"), ("agent-basics", "1.0"))
        ]
    }
    observed = []

    def claude_inventory(args, **kwargs):
        assert args[1:] == ["plugin", "list", "--json"]
        observed.append((args[0], kwargs["cwd"]))
        return subprocess.CompletedProcess(args, 0, json.dumps(claude), "")

    codex_cli = codex_plugin_cli(codex["installed"], other=claude_inventory)

    def run(args, **kwargs):
        # Codexは設定済みmarketplaceの一覧から照会を始めるため、一覧の取得を1回の導入情報の取得と数える。
        if args[0] == "codex" and args[1:3] == ["plugin", "marketplace"]:
            observed.append((args[0], kwargs["cwd"]))
        return codex_cli(args, **kwargs)

    monkeypatch.setattr(resource_roots.process_tree, "run_with_timeout", run)
    async with _manager_with_fake("claude") as (manager, claude_backend):
        codex_backend = FakeBackend(manager.sessions, "codex")
        install_backend(manager, "codex", codex_backend)
        extra_backends = {engine: FakeBackend(manager.sessions, engine) for engine in ("agy", "opencode")}
        for engine, backend in extra_backends.items():
            install_backend(manager, engine, backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        for engine, project, expected in (
            ("claude", project_a, "local"),
            ("codex", project_a, "codex"),
            ("agy", project_a, "agy"),
            ("opencode", project_a, "opencode"),
            ("claude", project_b, "user"),
        ):
            monkeypatch.setattr(
                _atk_config,
                "parse_unresolved_model_candidates",
                lambda _type, _engine=engine, **_kw: [(_engine, "model", "high")],
            )
            params = {"対象": '内容\n入力:\n<atk-auto source="input" kind="example">任意の入力本文</atk-auto>'}
            response = await mcp_tools.start(str(project), subagent_md_path=str(document), extra_params=params)
            backend = extra_backends.get(engine, codex_backend if engine == "codex" else claude_backend)
            received = backend.prompts[-1]
            assert expected + "だけの品質本文" in received
            assert all(label + "だけの品質本文" not in received for label in roots if label != expected)
            assert task_documents.task_prompt_inputs(delivery_payload(received)) == {
                **params,
                "引き継ぎ記録先": response["handoff_record_path"] + "（新規）",
            }
        # 正式登録を読めない候補は原因付きで除外し、別ホストの候補で配送を再構成する。
        registry.unlink()
        (opencode_config / "agent-toolkit.md").unlink()
        for engine in ("agy", "opencode"):
            monkeypatch.setattr(
                _atk_config,
                "parse_unresolved_model_candidates",
                lambda _type, _engine=engine, **_kw: [(_engine, "model", "high"), ("claude", "model", "high")],
            )
            response = await mcp_tools.start(str(project_b), subagent_md_path=str(document), extra_params={"対象": "内容"})
            assert response["engine"] == "claude"
            assert response["excluded_candidates"][0]["engine"] == engine
            assert "再実行" in response["excluded_candidates"][0]["reason"]
            assert "userだけの品質本文" in claude_backend.prompts[-1]
        monkeypatch.setattr(
            _atk_config,
            "parse_unresolved_model_candidates",
            lambda _type, **_kw: [(engine, "model", "high") for engine in ("agy", "opencode")],
        )
        with pytest.raises(RuntimeError, match="no available model candidates") as observed_failure:
            await mcp_tools.start(str(project_b), subagent_md_path=str(document), extra_params={"対象": "内容"})
        assert "plugins.json" in str(observed_failure.value)
        assert "agent-toolkit.md" in str(observed_failure.value)
    # 同じ要求の多段参照は一覧を共有し、次の要求では実host/projectで取り直す。
    assert observed == [("claude", str(project_a)), ("codex", str(project_a)), *[("claude", str(project_b))] * 3]


@pytest.mark.asyncio
async def test_explore_role_and_free_text_share_investigation_capability(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """役割指定と自由本文の探索は成果保存を要求せず、Git識別子で同じ版を比較する。"""
    repository = init_repository(tmp_path / "repo", files={"文章 空白.md": "比較元\n"}, commit_message="比較元")
    base = git_output(repository, "rev-parse", "HEAD")
    artifact = repository / "文章 空白.md"
    artifact.write_text("今回の本文\n", encoding="utf-8")
    head = commit_all(repository, "本文を更新")
    comparison = json.dumps(
        {"repository": str(repository), "base": base, "head": head, "paths": [artifact.name]}, ensure_ascii=False
    )
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        params = {"成果物": str(artifact), "種別": "文書", "読者像": "読者", "修正範囲": comparison, "比較資料": comparison}
        await mcp_tools.start(str(repository), subagent_md_path="reader-fit-review", extra_params=params)
        await mcp_tools.start(str(repository), mode="explore", prompt="Gitの同じ版から本文と差分を比較する。\n" + comparison)
        assert [call[2] for call in backend.start_calls] == ["explore", "explore"]
        rereview = {**params, "レビュー種別": "再レビュー"}
        with pytest.raises(ValueError, match="未解決事項"):
            await mcp_tools.start(str(repository), subagent_md_path="reader-fit-review", extra_params=rereview)
        await mcp_tools.start(
            str(repository), subagent_md_path="reader-fit-review", extra_params={**rereview, "未解決事項": "なし"}
        )
        artifact.write_text("未commitの異なる版\n", encoding="utf-8")
        with pytest.raises(ValueError, match="同じ版"):
            await mcp_tools.start(str(repository), subagent_md_path="reader-fit-review", extra_params=params)
        assert len(backend.start_calls) == 3


@pytest.mark.parametrize("placement", ["root-version", "ancestor-version", "checkout"])
@pytest.mark.parametrize("form", ["role-name", "absolute-path"])
def test_imported_server_keeps_role_documents_after_distribution_removal(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, placement: str, form: str
) -> None:
    """サーバーのモジュール読込時に保持し、元配布物の消失後も役割とplugin変数を同じ版へ解決する。"""
    roots = {
        "root-version": tmp_path / "cache" / "agent-toolkit" / "2.125.0",
        "ancestor-version": tmp_path / "cache" / "2.125.0" / "agent-toolkit",
        "checkout": tmp_path / "checkout" / "agent-toolkit",
    }
    source = roots[placement]
    manifest = source / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit","version":"2.125.0"}', encoding="utf-8")
    task = source / "share" / "declared.subagent.md"
    task.parent.mkdir()
    task.write_text(
        "# 担当\n\n## 入力\n\n```text\n必須入力名: 対象\n作業ツリーへの書込: あり\n```\n\n"
        "${CLAUDE_PLUGIN_ROOT}/skills/sample/SKILL.md\n",
        encoding="utf-8",
    )
    skill = source / "skills" / "sample" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# この版の手順\n", encoding="utf-8")
    module_dir = source / "agent_toolkit" / "_agents_server"
    module_dir.mkdir(parents=True)
    for module in (plugin_roots, launch_requests):
        assert module.__file__ is not None
        shutil.copyfile(module.__file__, module_dir / pathlib.Path(module.__file__).name)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setenv("TMPDIR", str(tmp_path))

    def load(name: str) -> Any:
        spec = importlib.util.spec_from_file_location(f"retained_{name}", module_dir / f"{name}.py")
        assert spec is not None and spec.loader is not None
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded

    retained = load("plugin_root")
    monkeypatch.setattr(server_package, "plugin_root", retained)
    requests = load("launch_requests")
    monkeypatch.setitem(requests._TASK_MODEL_TYPES, task.name, "execute")
    stable = retained.SERVER_PLUGIN_ROOT
    if placement == "checkout":
        assert stable == source
    else:
        assert stable != source
        shutil.rmtree(source)
    # 読み込み直したモジュールは共通fixtureの差し替えを受けないため、全体作業契約を明示する。
    whole = tmp_path / "work-contract.md"
    whole.write_text("目的: 配布物の消失後の解決を確かめる\n", encoding="utf-8")
    model_type, prompt, _kind, _handoff = requests.task_document_request(
        "declared" if form == "role-name" else str(task), {"対象": "値"}, whole_contract_path=str(whole)
    )
    assert model_type == "execute"
    assert str(stable / "share" / task.name) in prompt
    assert str(stable / "skills" / "sample" / "SKILL.md") in prompt
    assert (stable / "skills" / "sample" / "SKILL.md").read_text(encoding="utf-8") == "# この版の手順\n"
