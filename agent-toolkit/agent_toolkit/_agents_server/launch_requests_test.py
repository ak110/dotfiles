"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

import importlib.util

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import logging
import pathlib
import re
import shutil
from types import SimpleNamespace as _SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

import agent_toolkit.agents_server_mcp as entry_script
from agent_toolkit import _agents_server as server_package
from agent_toolkit._agents_server import (
    launch_requests,
    mcp_tools,
    resume_waits,
    tool_descriptions,
)
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError
from agent_toolkit._testing.agents_server_support import (
    _REAL_PLUGIN_PREFLIGHT,
    FakeBackend,
    _actionable_message,
    _manager_with_fake,
    _observed_input_lines,
    _observed_input_params,
    _recording_candidates,
    _use_real_plugin_preflight,
    _write_declared_task_document,
    _write_failing_uv,
    install_backend,
)
from agent_toolkit._testing.git_repository import commit_all, git_output, init_repository
from agent_toolkit._testing.helpers import delivery_payload
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_explore_role_and_free_text_share_investigation_capability(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """保存を強制せずGit識別子で同じ版を比較し、両入口が共通の探索経路へ届く。"""
    repository = init_repository(tmp_path / "repo", files={"文章 空白.md": "比較元\n"}, commit_message="比較元")
    base = git_output(repository, "rev-parse", "HEAD")
    artifact = repository / "文章 空白.md"
    artifact.write_text("今回の本文\n", encoding="utf-8")
    head = commit_all(repository, "本文を更新")
    comparison = json.dumps(
        {"repository": str(repository), "base": base, "head": head, "paths": [artifact.name]}, ensure_ascii=False
    )
    _recording_candidates(monkeypatch)
    manager, backend = _manager_with_fake("codex")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = {"成果物": str(artifact), "種別": "文書", "読者像": "読者", "修正範囲": comparison, "比較資料": comparison}
    try:
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
    finally:
        await manager.close()


class TaskManager(_SimpleNamespace):
    """起動入力を記録するテスト用の代替にも、taskが読む直接の委譲元の情報を返す`launcher_record_context`を持たせる。"""

    def launcher_record_context(self) -> dict[str, str]:
        """起動入力を比較するテストでは、委譲元の記録が未解決であることを明示する。"""
        return {"status": "unresolved"}


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
        "# 担当\n\n## 入力\n\n```text\n必須入力名: 対象\n```\n\n${CLAUDE_PLUGIN_ROOT}/skills/sample/SKILL.md\n",
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
    monkeypatch.setitem(requests._TASK_MODEL_TYPES, task.name, "high_tier")
    stable = retained.SERVER_PLUGIN_ROOT
    if placement == "checkout":
        assert stable == source
    else:
        assert stable != source
        shutil.rmtree(source)
    model_type, _usage, prompt, _kind, _handoff = requests.task_document_request(
        "declared" if form == "role-name" else str(task), {"対象": "値"}
    )
    assert model_type == "high_tier"
    assert str(stable / "share" / task.name) in prompt
    assert str(stable / "skills" / "sample" / "SKILL.md") in prompt
    assert (stable / "skills" / "sample" / "SKILL.md").read_text(encoding="utf-8") == "# この版の手順\n"


@pytest.mark.asyncio
async def test_start_rejects_prompt_missing_required_input(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """startは`<役割名>.subagent.md`の必須入力が欠けた委譲プロンプトをbackendへ渡さない。"""
    task_document = tmp_path / "share" / "task.subagent.md"
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text(
        "# タスク\n\n## 入力\n\n```text\n必須入力名: 対象,目的\n```\n",
        encoding="utf-8",
    )
    called = False

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    with pytest.raises(ValueError, match=rf"目的.*{re.escape(str(task_document))}"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値"})

    assert called is False


@pytest.mark.asyncio
async def test_start_accepts_exec_review_prompt_with_documented_input_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """startは実行レビュー文書が列挙する通常の入力名を受理する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
    called = False

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    extra_params = _observed_input_params(task_document.name, tmp_path)

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    assert called is True


@pytest.mark.asyncio
async def test_start_rejects_undeclared_input_name(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """宣言外の入力名を渡した`start`は委譲先を起動せず、宣言外の項目名と受理する項目名の一覧を返す。

    受理すると、`<役割名>.subagent.md`が定める手順を委譲元が委譲プロンプトへ書き足して起動できてしまう。
    """
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path) | {"追加指示": "検証はpytestで行う"}

    with pytest.raises(ValueError, match="`<役割名>.subagent.md`が宣言していない入力です: 追加指示") as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    assert "受理する入力名:" in str(raised.value)
    assert "環境構築" in str(raised.value)
    assert str(task_document) in str(raised.value)
    assert "宣言した入力へ収める" in _actionable_message(raised.value)
    manager.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "extra_params", "operation"),
    [
        ("relative.subagent.md", {}, "`delegate`"),
        ("/nonexistent/share/missing.subagent.md", {}, "`delegate`"),
        ("exec.subagent.md", {"不正 な名前": "値"}, "空白"),
    ],
    ids=["relative-path", "missing-file", "invalid-input-name"],
)
async def test_start_rejects_invalid_task_document_request_with_next_action(
    document: str,
    extra_params: dict[str, str],
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """`<役割名>.subagent.md`の指定と入力名の誤りは、正しい渡し方か自由本文の`delegate`への切替を次の操作で示す。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    path = str(launch_requests._SHARE_DIRECTORY / document) if document == "exec.subagent.md" else document

    with pytest.raises(ValueError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=path, extra_params=extra_params)

    assert operation in _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    manager.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("form", ["role-name", "absolute-path"])
async def test_start_resolves_role_name_to_own_plugin_root(
    form: str, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """役割名はサーバー自身のplugin rootの`share/<役割名>.subagent.md`へ解決し、絶対パスと同じ起動になる。

    役割名を受理しないと、委譲元はplugin rootを探してから起動する必要がある。
    委譲プロンプトの1行目の出所が別のrootを指すと、委譲元と委譲先が別の版の文書を使う。
    """
    task_document = (launch_requests._SHARE_DIRECTORY / "add-wi.subagent.md").resolve()
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    subagent_md_path = "add-wi" if form == "role-name" else str(task_document)

    response = await mcp_tools.start(
        str(tmp_path), subagent_md_path=subagent_md_path, extra_params=_observed_input_params(task_document.name, tmp_path)
    )

    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    manager.start.assert_awaited_once()
    task_prompt = manager.start.await_args.args[1]
    assert task_prompt.splitlines()[0] == f"次の文書の手順を実行せよ（出所: {task_document}）。"
    assert manager.start.await_args.kwargs["label"] == "add-wi"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subagent_md_path", "reason"),
    [
        ("missing-role", "対応する文書が無い"),
        ("share/add-wi", "区切り文字"),
        ("share\\add-wi", "区切り文字"),
        ("add-wi.subagent.md", "区切り文字"),
        ("", "対応する文書が無い"),
    ],
    ids=["unknown-role", "slash", "backslash", "suffix", "empty"],
)
async def test_start_rejects_unresolvable_role_name_with_accepted_roles(
    subagent_md_path: str,
    reason: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """解決先の無い役割名と区切り文字か接尾辞を含む相対の値は起動せず、受理する役割名の一覧と次の操作を返す。

    受理すると、`share/`直下の外の文書や作業ディレクトリ相対の文書を委譲先へ渡せてしまう。
    """
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ValueError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=subagent_md_path, extra_params={})

    message = _actionable_message(raised.value)
    body, next_action = message.split(NEXT_ACTION_PREFIX, 1)
    assert reason in body
    accepted = body.split("受理する役割名: ", 1)[1]
    assert "add-wi" in accepted
    assert "exec-review" in accepted
    assert "役割名" in next_action
    assert "`delegate`" in next_action
    manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_reports_missing_model_type_mapping_as_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """工程別設定の対応が無い`<役割名>.subagent.md`は、欠陥の報告と自由本文の`delegate`への切替を次の操作で示す。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.delitem(launch_requests._TASK_MODEL_TYPES, "exec.subagent.md")

    with pytest.raises(ValueError, match="no model_type mapping") as raised:
        await mcp_tools.start(
            str(tmp_path), subagent_md_path=str(launch_requests._SHARE_DIRECTORY / "exec.subagent.md"), extra_params={}
        )

    next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "`delegate`" in next_action
    assert "報告" in next_action
    manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_accepts_declared_optional_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """必須・任意入力名だけの`start`は起動し、委譲プロンプトは`入力:`見出しを持ち`追加指示:`を持たない。"""
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path) | {"環境構築": "完了済み"}

    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    prompt = manager.start.await_args.args[1]
    assert "\n入力:\n" in prompt
    assert "追加指示:" not in prompt
    assert "環境構築: 完了済み" in prompt
    assert manager.start.await_args.kwargs["launch_kind"] == "delegate"


@pytest.mark.asyncio
async def test_start_rejects_removed_wait_exception_input(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """待機と再開の方針はサーバーが伝えるため、委譲元が渡す`待機表明の例外`は宣言外の入力として拒否する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    manager = TaskManager(start=AsyncMock())
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path) | {"待機表明の例外": "適用する"}

    with pytest.raises(ActionableError, match="`<役割名>.subagent.md`が宣言していない入力です: 待機表明の例外"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_rejects_removed_last_pushed_commit_input(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """終端担当はpush済みの範囲をベースbranchの追跡refから得るため、撤去した`直前にpushしたcommit`は宣言外の入力として拒否する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "session-termination.subagent.md"
    manager = TaskManager(start=AsyncMock())
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path) | {"直前にpushしたcommit": "abc1234"}

    with pytest.raises(ActionableError, match="直前にpushしたcommit"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_uses_declared_launch_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`mode: explore`を宣言した`<役割名>.subagent.md`は`start_explore`と同じ軽量な起動条件で起動する。

    起動条件はsession記録の`launch_kind`と、backendへ渡すシステム指示・許可ツールを決める種別で確かめる。
    """
    _recording_candidates(monkeypatch)
    manager, _ = _manager_with_fake("codex")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    explain = launch_requests._SHARE_DIRECTORY / "pick-wi-explain.subagent.md"

    response = await mcp_tools.start(
        str(tmp_path), subagent_md_path=str(explain), extra_params=_observed_input_params(explain.name, tmp_path)
    )

    shown = await manager.show_session(response["session_id"])
    assert shown["launch_kind"] == "explore"
    assert shown["model_type"] == "low_tier"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_name", "launch_kind", "model_type"),
    [
        ("external-write-review.subagent.md", "explore", "low_tier"),
        ("reader-fit-review.subagent.md", "explore", "low_tier"),
        ("bulk-replace-review.subagent.md", "explore", "low_tier"),
        ("copilot-review-audit.subagent.md", "delegate", "high_tier"),
    ],
)
async def test_standard_review_task_documents_launch_with_declared_kinds(
    task_name: str,
    launch_kind: str,
    model_type: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """定型の委譲を移した`<役割名>.subagent.md`は、`<役割名>.parent.md`が定める入力だけで`start`から起動し、宣言した起動条件と段位で動く。

    起動条件が通常委譲へ戻ると読み取り専用の探索が規範とプロジェクト規範を読み込み、段位を誤ると監査を下位モデルで行う。
    """
    _recording_candidates(monkeypatch)
    manager, _ = _manager_with_fake("codex")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    task_document = launch_requests._SHARE_DIRECTORY / task_name

    response = await mcp_tools.start(
        str(tmp_path), subagent_md_path=str(task_document), extra_params=_observed_input_params(task_name, tmp_path)
    )

    shown = await manager.show_session(response["session_id"])
    assert shown["launch_kind"] == launch_kind
    assert shown["model_type"] == model_type


@pytest.mark.asyncio
async def test_start_without_launch_kind_uses_delegate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`mode:`行の無い`<役割名>.subagent.md`は通常委譲で起動し、不正な値は宣言を読めない扱いで通常委譲とする。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    task_document = _write_declared_task_document(tmp_path, "必須入力名: 対象\n任意入力名: 補足")
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値", "補足": "値"})
    task_document.write_text(
        task_document.read_text(encoding="utf-8").replace("任意入力名: 補足", "mode: batch"), encoding="utf-8"
    )
    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値", "未宣言": "値"})

    assert [call.kwargs["launch_kind"] for call in manager.start.await_args_list] == ["delegate", "delegate"]


@pytest.mark.asyncio
async def test_start_reads_legacy_launch_kind_line(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`mode:`へ改める前の`起動種別:`行を持つ旧形式の`<役割名>.subagent.md`も同じ値で読む。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    current = _write_declared_task_document(tmp_path / "current", "必須入力名: 対象\nmode: explore")
    legacy = _write_declared_task_document(tmp_path / "legacy", "必須入力名: 対象\n起動種別: explore")
    for task_document in (current, legacy):
        monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "low_tier")
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値"})

    assert [call.kwargs["launch_kind"] for call in manager.start.await_args_list] == ["explore", "explore"]


@pytest.mark.parametrize(
    "task_name",
    [
        "add-wi.subagent.md",
        "bulk-replace-review.subagent.md",
        "copilot-review-audit.subagent.md",
        "defect-investigation.subagent.md",
        "exec-review.subagent.md",
        "exec.subagent.md",
        "external-write-review.subagent.md",
        "lane-integration.subagent.md",
        "pick-wi-explain.subagent.md",
        "reader-fit-review.subagent.md",
        "session-termination.subagent.md",
    ],
)
def test_observed_delegation_prompts_include_required_inputs(task_name: str, tmp_path: pathlib.Path) -> None:
    """実運用で観測した最小の委譲プロンプトが必須入力の確認処理を通過する。"""
    task_document = launch_requests._SHARE_DIRECTORY / task_name
    extra_params = _observed_input_params(task_name, tmp_path)

    assert launch_requests._validate_required_prompt_inputs(task_document, extra_params) is None


@pytest.mark.parametrize("rereview", [False, True])
@pytest.mark.asyncio
async def test_reader_fit_public_start_accepts_declared_review_inputs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, rereview: bool
) -> None:
    """親が渡す初回・再レビューの入力をstartで受理し、宣言外入力だけを拒否する。"""
    task = launch_requests._SHARE_DIRECTORY / "reader-fit-review.subagent.md"
    manager = TaskManager(
        start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader-fit-review"})
    )
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = dict(line.split(": ", 1) for line in _observed_input_lines(task.name, tmp_path, rereview=rereview))
    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task), extra_params=params)
    assert response["session_id"] == "reader"
    manager.start.assert_awaited_once()
    prompt = manager.start.await_args.args[1]
    assert all(f"{key}: {value}" in prompt for key, value in params.items())
    manager.start.reset_mock()
    missing_scope = {key: value for key, value in params.items() if key != "修正範囲"}
    with pytest.raises(ActionableError, match="修正範囲"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task), extra_params=missing_scope)
    manager.start.assert_not_awaited()
    with pytest.raises(ActionableError, match="宣言"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task), extra_params={**params, "追加説明": "全体を再走査"})
    manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_exec_review_public_start_accepts_previous_revision(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """引き継ぎ再レビューの比較元を宣言済み入力として委譲プロンプトへ配送する。"""
    task = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "review", "status": "running", "label": "exec-review"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = {
        **_observed_input_params(task.name, tmp_path),
        "レビュー種別": "引き継ぎ再レビュー",
        "round": "2",
        "前回確認版": str(tmp_path / "previous.md"),
    }
    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task), extra_params=params)
    assert response["session_id"] == "review"
    assert f"前回確認版: {params['前回確認版']}" in manager.start.await_args.args[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("task_name", ["defect-investigation.subagent.md", "exec.subagent.md"])
async def test_public_start_uses_high_tier_model(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, task_name: str
) -> None:
    """調査担当とレーン担当は段位省略の`start`から`high_tier`で起動し、出所と入力を載せる。

    工程別モデルの対応が欠けると`start`が起動を拒否し、段位を誤ると調査を上位モデルで行えない。
    """
    task_document = launch_requests._SHARE_DIRECTORY / task_name
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path)

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    manager.start.assert_awaited_once()
    model_type, prompt, cwd = manager.start.await_args.args
    assert model_type == "high_tier"
    assert cwd == str(tmp_path)
    assert str(task_document) in prompt
    assert all(f"{key}: {value}" in prompt for key, value in extra_params.items())


@pytest.mark.asyncio
async def test_public_lane_resolves_high_tier_candidates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """段位省略のレーン担当が保存設定の上位候補を解決し、backendへ渡す。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_ENGINE", "claude")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_HIGH_TIER_MODEL", "opus/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_MEDIUM_TIER_MODEL", "sonnet/medium")
    manager, _backend = _manager_with_fake("claude")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    try:
        response = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=str(task_document),
            extra_params=_observed_input_params(task_document.name, tmp_path),
        )
        session = manager.sessions[response["session_id"]]
        assert (session.engine, session.model, session.effort) == ("claude", "opus", "high")
        assert session.model_type == "high_tier"
    finally:
        await manager.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex"])
async def test_dedicated_wi_owner_uses_orchestrate_candidates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str
) -> None:
    """要求別担当の明示orchestrate指定は、役割のbase用途と独立した上位候補を両engineで使う。"""
    other = "codex" if engine == "claude" else "claude"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_BASE_ENGINE", other)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_ENGINE", engine)
    for candidate in (engine, other):
        monkeypatch.setenv(f"AGENT_TOOLKIT_CONFIG_{candidate.upper()}_HIGH_TIER_MODEL", "orchestrator/high")
    current, _backend = _manager_with_fake(engine)
    install_backend(current, other, FakeBackend(current.sessions, other))
    monkeypatch.setattr(mcp_tools, "_MANAGER", current)
    task = launch_requests._SHARE_DIRECTORY / "add-wi.subagent.md"
    params = {**_observed_input_params(task.name, tmp_path), "投入経路": "専念型"}
    try:
        response = await mcp_tools.start(
            cwd=str(tmp_path), subagent_md_path=str(task), extra_params=params, model_type="orchestrate"
        )
        session = current.sessions[response["session_id"]]
        assert (session.engine, session.model, session.effort) == (engine, "orchestrator", "high")
        assert session.model_type == "orchestrate"
        assert session.launch_kind == "delegate"
    finally:
        await current.close()


def test_required_inputs_ignore_heading_inside_code_fence(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_document = tmp_path / "task.subagent.md"
    task_document.write_text("````text\n## 入力\n````\n\n## 入力\n\n```text\n必須入力名: 対象\n```\n", encoding="utf-8")
    monkeypatch.setattr(launch_requests, "_is_agent_toolkit_task_document", lambda _path: True)

    assert launch_requests._validate_required_prompt_inputs(task_document, {"対象": "value"}) is None


@pytest.mark.asyncio
async def test_start_prepares_handoff_path_when_omitted(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`引き継ぎ記録先`を省略した`start`は拒否されず、委譲元のセッションのmanaged-temp直下の`（新規）`記録先で起動する。

    渡し忘れの拒否と再発行の往復を除くため、サーバーが用意した絶対パスを委譲プロンプトと応答の双方へ載せる。
    応答の値が委譲プロンプトと一致しないと、委譲元は`（継続）`で渡す記録先を誤る。
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-state")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "caller-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    session_area = _managed_temp.create_managed_temp("session", session_id="caller-session")
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    input_lines = [
        line for line in _observed_input_lines(task_document.name, tmp_path) if not line.startswith("引き継ぎ記録先:")
    ]
    extra_params = dict(line.split(": ", 1) for line in input_lines)
    prompts: list[str] = []

    async def fake_start(_model_type: str, prompt: str, *_args: object, **_kwargs: object) -> dict[str, Any]:
        prompts.append(prompt)
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", None, raising=False)
    mcp_tools._prepare_child_environment()
    assert "CLAUDE_CODE_SESSION_ID" not in launch_requests.os.environ

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    handoff = pathlib.Path(response["handoff_record_path"])
    assert handoff.is_absolute()
    assert handoff.parent == session_area
    assert not handoff.exists()
    assert f"引き継ぎ記録先: {handoff}（新規）" in prompts[0].splitlines()


@pytest.mark.asyncio
async def test_start_keeps_given_handoff_path_and_rejects_other_missing_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """委譲元が渡した`引き継ぎ記録先`はそのまま委譲プロンプトへ載せ、それ以外の必須入力の欠落は従来どおり拒否する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"
    extra_params = dict(line.split(": ", 1) for line in _observed_input_lines(task_document.name, tmp_path))
    prompts: list[str] = []

    async def fake_start(_model_type: str, prompt: str, *_args: object, **_kwargs: object) -> dict[str, Any]:
        prompts.append(prompt)
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    assert "handoff_record_path" not in response
    assert f"引き継ぎ記録先: {extra_params['引き継ぎ記録先']}" in prompts[0].splitlines()

    without_kind = {name: value for name, value in extra_params.items() if name != "担当種別"}
    with pytest.raises(ValueError, match=rf"担当種別.*{re.escape(str(task_document))}"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=without_kind)
    assert len(prompts) == 1


@pytest.mark.asyncio
async def test_start_warns_and_continues_without_required_input_marker(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """必須入力名を取得できない`<役割名>.subagent.md`は警告してbackendを起動する。"""
    task_document = tmp_path / "share" / "task.subagent.md"
    (tmp_path / ".claude-plugin").mkdir(parents=True)
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("# タスク\n\n## 入力\n\n- 対象\n", encoding="utf-8")

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    with caplog.at_level(logging.WARNING, logger="agent-toolkit.agents-server.mcp"):
        response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})

    handoff = pathlib.Path(response.pop("handoff_record_path"))
    assert handoff.is_absolute()
    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    assert "必須入力を確認できません" in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ("codex", "claude"))
async def test_every_delivery_path_wraps_body_with_sender_label(
    engine: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """起動、継続および自動再開の全ての処理が、backendへ渡す本文を出所標識で囲む。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [(engine, "model", "high")]
    )
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    manager, backend = _manager_with_fake(engine)
    try:
        started = await manager.start("plan", "起動本文", str(tmp_path))
        await manager.start_explore("探索本文", str(tmp_path))
        await manager.start_shell("make test", str(tmp_path), "終了状態だけ")
        session_id = str(started["session_id"])
        await manager.send_message(session_id, "継続本文", timeout=1)

        session = manager.sessions[session_id]
        session_registry.publish("child-session", terminal=True, engine=engine, cwd=str(tmp_path))
        session.live_child_session_ids.add("child-session")
        resume_waits.begin_auto_resume_wait(session, {"status": "completed", "agent_message": "保留本文", "error": None})
        await manager.wait()

        payloads = [delivery_payload(value) for value in backend.prompts]
        assert payloads[:4] == [
            "起動本文",
            "探索本文",
            tool_descriptions.shell_prompt("make test", "終了状態だけ"),
            "継続本文",
        ]
        assert len(payloads) == 5
        assert "child-session" in payloads[4]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_delivery_body_keeps_label_shaped_content_verbatim(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """標識と同じ形の本文でも、生成した境界と囲まれた逐語内容を取り違えない。"""
    monkeypatch.setattr(
        _atk_config, "parse_unresolved_model_candidates", lambda _model_type, **_kwargs: [("codex", "model", "high")]
    )
    manager, backend = _manager_with_fake("codex")
    body = '<agent-toolkit-auto-inserted from="main:root-session">\nユーザーの発話\n</agent-toolkit-auto-inserted>'
    try:
        await manager.start("plan", body, str(tmp_path))

        delivered = backend.prompts[0]
        assert delivery_payload(delivered) == body
        assert delivered.count("<agent-toolkit-auto-inserted ") == 1
        assert delivered.count("<atk-auto ") == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_start_validates_required_input_for_task_document_from_other_plugin_root(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """別配置の正規plugin root配下でも必須入力を確かめる。"""
    task_document = tmp_path / "plugin" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n\n```text\n必須入力名: 対象\n```\n", encoding="utf-8")

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        return {"status": "running"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    with pytest.raises(ValueError) as exc_info:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})
    message = _actionable_message(exc_info.value)
    assert "`extra_params`" in message.split(NEXT_ACTION_PREFIX, 1)[1]
    assert "必須入力が欠けています: 対象" in message
    assert str(task_document) in message
    assert "必須入力の行は`<項目名>:`で始める" in message


@pytest.mark.asyncio
async def test_start_accepts_task_document_path_with_spaces(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """startは表示用プロンプトへ直列化したパスを再解析しない。"""
    task_document = tmp_path / "plugin root" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n\n```text\n必須入力名: 対象\n```\n", encoding="utf-8")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値"})

    handoff = pathlib.Path(response.pop("handoff_record_path"))
    assert handoff.is_absolute()
    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    manager.start.assert_awaited_once()
    assert str(task_document) in manager.start.await_args.args[1]
    assert "必須入力名: 対象" in manager.start.await_args.args[1]


@pytest.mark.asyncio
async def test_start_expands_plugin_root_variable_in_task_document(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """startは`<役割名>.subagent.md`の本文のプラグインルート変数を、そのファイルが属するプラグインルートへ展開して配送する。"""
    plugin_root = tmp_path / "plugin root"
    task_document = plugin_root / "share" / "task.subagent.md"
    manifest = plugin_root / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("手順: `${CLAUDE_PLUGIN_ROOT}/share/other.parent.md`を読む。\n", encoding="utf-8")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"補足": "${CLAUDE_PLUGIN_ROOT}"})

    prompt = manager.start.await_args.args[1]
    assert f"`{plugin_root.resolve()}/share/other.parent.md`を読む。" in prompt
    # 名前付き入力は委譲元の値のまま配送する
    assert prompt.endswith("補足: ${CLAUDE_PLUGIN_ROOT}")


@pytest.mark.asyncio
async def test_start_rejects_task_document_that_cannot_be_read(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """実在確認後に読取不能となった文書ではbackendを起動しない。"""
    task_document = tmp_path / "plugin" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n", encoding="utf-8")
    original_read_text = pathlib.Path.read_text

    def read_text(
        path: pathlib.Path,
        encoding: str | None = None,
        errors: str | None = None,
    ) -> str:
        if path == task_document:
            raise OSError("read failed")
        return original_read_text(path, encoding=encoding, errors=errors)

    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "high_tier")

    with pytest.raises(ValueError, match="`<役割名>.subagent.md`をUTF-8で読めません"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})

    manager.start.assert_not_awaited()


def test_start_rejects_task_document_under_share_without_plugin_manifest(tmp_path: pathlib.Path) -> None:
    """manifestを持たないshare配下の文書は入力を確認する対象から除く。"""
    task_document = tmp_path / "share" / "task.subagent.md"
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n", encoding="utf-8")

    warning = launch_requests._validate_required_prompt_inputs(task_document, {})
    assert warning is not None
    assert "`<役割名>.subagent.md`がshare配下ではありません" in warning


def test_start_rejects_task_document_under_share_for_other_plugin_manifest(tmp_path: pathlib.Path) -> None:
    """別名pluginのshare配下の文書は入力を確認する対象から除く。"""
    task_document = tmp_path / "plugin" / "share" / "task.subagent.md"
    manifest = task_document.parent.parent / ".claude-plugin" / "plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"other-plugin"}', encoding="utf-8")
    task_document.parent.mkdir()
    task_document.write_text("## 入力\n", encoding="utf-8")

    warning = launch_requests._validate_required_prompt_inputs(task_document, {})
    assert warning is not None
    assert "`<役割名>.subagent.md`がshare配下ではありません" in warning


@pytest.mark.asyncio
async def test_start_accepts_independent_wi_draft_review(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """専念型の投入前レビューが宣言入力だけで中位・review用途として起動する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "wi-draft-review.subagent.md"
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "review", "status": "running", "label": "review"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = {key: str(tmp_path / f"{key}.md") for key in ("原稿", "要求と出所", "成立性の証拠", "引き継ぎ記録先")}
    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=params)
    assert manager.start.await_args.args[0] == "medium_tier"
    assert manager.start.await_args.kwargs["usage"] == "review"


@pytest.mark.parametrize("model_type", [None, "low_tier"])
@pytest.mark.asyncio
async def test_recurrence_self_check_public_start_keeps_review_usage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, model_type: str | None
) -> None:
    """セルフチェックが宣言入力からexplore・中位で起動し、明示段位でもreview用途を保つ。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "check", "status": "running", "label": "check"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = {
        "観点ファイル": json.dumps([str(tmp_path / "items.md")]),
        "今回差分": str(tmp_path / "change.patch"),
        "変更ファイル一覧": "example.py",
        "対象worktree": str(tmp_path),
        "未解決指摘": "なし",
    }
    await mcp_tools.start(str(tmp_path), subagent_md_path="recurrence-self-check", extra_params=params, model_type=model_type)
    call = manager.start.await_args
    assert call.args[0] == (model_type or "medium_tier")
    assert call.kwargs["usage"] == "review"
    assert call.kwargs["launch_kind"] == "explore"
    assert all(f"{key}: {value}" in call.args[1] for key, value in params.items())
    manager.start.reset_mock()
    with pytest.raises(ActionableError, match="未解決指摘"):
        await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="recurrence-self-check",
            extra_params={key: value for key, value in params.items() if key != "未解決指摘"},
        )
    manager.start.assert_not_awaited()


def test_every_task_role_has_usage() -> None:
    """段位を持つ全役割に用途の対応があり、用途は段位名の候補合成が受理する値である。

    用途の対応が欠けた役割は段位ごとの用途（上位はbase、中位はreview、軽量はchores）で起動し、役割表と異なるengine順を使う。
    """
    assert set(launch_requests._TASK_USAGES) == set(launch_requests._TASK_MODEL_TYPES)
    assert set(launch_requests._TASK_USAGES.values()) <= {"base", "execute", "review", "chores"}


def test_preflight_covers_plugin_launch_commands() -> None:
    """事前確認は、プラグインのMCP設定とhookが委譲先で起動するコマンドの先頭語を全て含む。

    起動コマンドを追加した変更で事前確認を追加しないと、未trustの設定などで起動が失敗する状態を
    子の起動前に検出できず、ホスト共通の接続失敗記録を生じさせる。
    """
    plugin_root = pathlib.Path(entry_script.__file__).resolve().parent.parent
    launch_words: set[str] = set()

    def collect(node: Any) -> None:
        if isinstance(node, dict):
            command = node.get("command")
            if isinstance(command, str) and command.strip():
                launch_words.add(command.split()[0])
            for value in node.values():
                collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    for name in (".mcp.json", ".mcp.codex.json", "mcp.json", "hooks/hooks.json"):
        collect(json.loads((plugin_root / name).read_text(encoding="utf-8")))

    assert launch_words
    assert launch_words <= {command[0] for command in launch_requests.PREFLIGHT_COMMANDS}


@pytest.mark.asyncio
async def test_start_rejects_cwd_where_plugin_commands_fail(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """起動コマンドが失敗する作業ディレクトリでは子を起動せず、cwdと標準エラーを含む例外を返す。"""
    _use_real_plugin_preflight(monkeypatch)
    _recording_candidates(monkeypatch)
    bin_dir = tmp_path / "bin"
    _write_failing_uv(bin_dir)
    monkeypatch.setenv("PATH", str(bin_dir))
    workdir = tmp_path / "work"
    workdir.mkdir()
    manager, backend = _manager_with_fake("codex")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ValueError) as raised:
        await mcp_tools.start(str(workdir), mode="explore", prompt="調査")

    message = _actionable_message(raised.value)
    assert f"cwd={workdir}" in message
    assert "command=uv --version" in message
    assert "exit_code=1" in message
    assert "not trusted" in message
    assert "`mise trust`" in message
    assert not backend.start_calls
    assert not manager.sessions


@pytest.mark.asyncio
async def test_start_reports_path_check_when_plugin_commands_are_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """起動コマンドの実行ファイルが無い場合は、mise以外の原因としてPATHの確認を次の操作に示す。

    miseの案内だけを返すと、委譲元は無関係な`mise trust`を試して同じ失敗を繰り返す。
    """
    _use_real_plugin_preflight(monkeypatch)
    _recording_candidates(monkeypatch)
    empty_bin = tmp_path / "bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))
    manager, backend = _manager_with_fake("codex")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ValueError) as raised:
        await mcp_tools.start(str(tmp_path), mode="explore", prompt="調査")

    message = _actionable_message(raised.value)
    assert "実行ファイルが見つからない" in message
    assert "PATH" in message.split(NEXT_ACTION_PREFIX, 1)[1]
    assert not backend.start_calls


def test_preflight_passes_where_plugin_commands_succeed(tmp_path: pathlib.Path) -> None:
    """起動コマンドが成功する作業ディレクトリでは事前確認を通過する。"""
    if shutil.which("uv") is None or shutil.which("uvx") is None:
        pytest.skip("uvまたはuvxが未導入")
    _REAL_PLUGIN_PREFLIGHT(str(tmp_path))


@pytest.mark.parametrize("review_kind", ["初回レビュー", "再レビュー", "引き継ぎ再レビュー"])
@pytest.mark.parametrize("external", [False, True])
@pytest.mark.asyncio
async def test_start_accepts_saved_reader_materials(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, review_kind: str, external: bool
) -> None:
    """管理内外の本文・差分・前回指摘を、同じ起動境界で受理する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    current = tmp_path / ("回答 下書き.md" if external else "guide.md")
    current.write_text("現行本文\n", encoding="utf-8")
    findings = tmp_path / "findings.md"
    findings.write_text("前回指摘と応答・検証証拠\n", encoding="utf-8")
    params.update(
        {
            "成果物": str(current),
            "レビュー種別": review_kind,
            "比較資料": json.dumps(
                {
                    "比較元本文": str(tmp_path / "before.md"),
                    "今回差分": str(tmp_path / "diff.patch"),
                    "現行本文": str(current),
                },
                ensure_ascii=False,
            ),
            "未解決事項": str(findings),
        }
    )
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    response = await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    assert response["session_id"] == "reader"
    manager.start.assert_awaited_once()


@pytest.mark.parametrize(
    "defect", [None, "missing", "extra", "duplicate", "unreadable", "mismatch", "new-rereview", "no-array"]
)
@pytest.mark.asyncio
async def test_public_start_reader_material_collection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str | None
) -> None:
    """複数成果物の1対1の資料対応を公開startで確認し、不備を起動前に拒否する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    paths = [tmp_path / "guide.md", tmp_path / "second.md"]
    paths[1].write_text("第二の成果物\n", encoding="utf-8")
    materials = [
        {"比較元本文": str(tmp_path / "before.md"), "今回差分": str(tmp_path / "diff.patch"), "現行本文": str(path)}
        for path in paths
    ]
    params["成果物"] = json.dumps([str(path) for path in paths])
    if defect == "missing":
        materials.pop()
    elif defect == "extra":
        extra = tmp_path / "extra.md"
        extra.write_text("対象外\n", encoding="utf-8")
        materials.append({**materials[0], "現行本文": str(extra)})
    elif defect == "duplicate":
        materials[1] = materials[0]
    elif defect == "unreadable":
        materials[1]["今回差分"] = str(tmp_path / "missing.patch")
    elif defect == "mismatch":
        materials[1]["現行本文"] = str(tmp_path / "before.md")
    elif defect == "new-rereview":
        params.update({"レビュー種別": "再レビュー", "未解決事項": "なし"})
        materials[1].update({"比較元本文": "新規", "今回差分": "全文"})
    params["比較資料"] = json.dumps(materials[0] if defect == "no-array" else materials, ensure_ascii=False)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    if defect:
        with pytest.raises(ActionableError) as raised:
            await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)
        assert "再発行" in _actionable_message(raised.value)
        manager.start.assert_not_awaited()
    else:
        assert (await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params))[
            "session_id"
        ] == "reader"
        manager.start.assert_awaited_once()


@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.asyncio
async def test_start_accepts_new_reader_artifact_full_text(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    """初回の新規成果物は全文差分を明示すれば旧本文を要しない。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    params["修正範囲"] = "新規文書。全文が差分"
    if not legacy:
        params["比較資料"] = json.dumps(
            {"比較元本文": "新規", "今回差分": "全文", "現行本文": str(tmp_path / "guide.md")}, ensure_ascii=False
        )
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "reader", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    manager.start.assert_awaited_once()


@pytest.mark.parametrize(
    "defect",
    [
        "commit-only",
        "description-only",
        "missing-findings",
        "command-findings",
        "missing-file",
        "directory",
        "invalid-utf8",
        "missing-current",
        "new-rereview",
        "missing-key",
    ],
)
@pytest.mark.asyncio
async def test_start_rejects_reader_material_defects_before_launch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, defect: str
) -> None:
    """比較元・差分・証拠の欠落と参照不能は探索を起動せず補完を案内する。"""
    params = _observed_input_params("reader-fit-review.subagent.md", tmp_path)
    params["レビュー種別"] = "再レビュー"
    params["未解決事項"] = "なし"
    materials = {
        "比較元本文": str(tmp_path / "before.md"),
        "今回差分": str(tmp_path / "diff.patch"),
        "現行本文": str(tmp_path / "guide.md"),
    }
    if defect == "commit-only":
        materials["比較元本文"] = "abc1234"
    elif defect == "description-only":
        materials["今回差分"] = "設定保存節を修正した"
    elif defect == "missing-findings":
        del params["未解決事項"]
    elif defect == "command-findings":
        params["未解決事項"] = "git show HEAD"
    elif defect == "missing-file":
        materials["今回差分"] = str(tmp_path / "missing.patch")
    elif defect == "directory":
        materials["比較元本文"] = str(tmp_path)
    elif defect == "invalid-utf8":
        (tmp_path / "before.md").write_bytes(b"\xff")
    elif defect == "missing-current":
        params["成果物"] = str(tmp_path / "missing.md")
    elif defect == "new-rereview":
        materials.update({"比較元本文": "新規", "今回差分": "全文"})
    elif defect == "missing-key":
        del materials["比較元本文"]
    params["比較資料"] = json.dumps(materials, ensure_ascii=False)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "unexpected", "status": "running", "label": "reader"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)

    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path="reader-fit-review", extra_params=params)

    message = _actionable_message(raised.value)
    assert "保存" in message and "再発行" in message
    manager.start.assert_not_awaited()


@pytest.mark.parametrize(
    ("role", "field"),
    [
        ("external-write-review", "文面ファイル"),
        ("external-write-review", "根拠の所在"),
        ("bulk-replace-review", "差分ファイル"),
        ("pick-wi-explain", "説明対象の選定結果の出力先ファイル"),
        ("pick-wi-explain", "activeなWI本文の保存先"),
    ],
)
@pytest.mark.asyncio
async def test_start_rejects_missing_explore_materials_and_accepts_repair(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, role: str, field: str
) -> None:
    """他の探索役割も保存物を欠く起動を拒否し、補完後の再発行を受理する。"""
    params = _observed_input_params(f"{role}.subagent.md", tmp_path)
    params[field] = str(tmp_path / "saved material.md")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "explorer", "status": "running", "label": "explorer"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=role, extra_params=params)
    assert field in str(raised.value)
    assert "再発行" in _actionable_message(raised.value)
    manager.start.assert_not_awaited()

    pathlib.Path(params[field]).write_text("保存済み資料\n", encoding="utf-8")
    await mcp_tools.start(str(tmp_path), subagent_md_path=role, extra_params=params)
    manager.start.assert_awaited_once()


@pytest.mark.parametrize("json_array", [False, True])
@pytest.mark.asyncio
async def test_start_checks_all_external_evidence_files(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, json_array: bool
) -> None:
    """複数の根拠は全ファイルを検査し、識別子の注記を保存物と取り違えない。"""
    params = _observed_input_params("external-write-review.subagent.md", tmp_path)
    first = tmp_path / "diff.patch"
    second = tmp_path / "CI result.txt"
    params["根拠の所在"] = (
        json.dumps([str(first), str(second)]) if json_array else f"`{first}`（commit `abc123`）\n`{second}`（run ID `123`）"
    )
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "review", "status": "running", "label": "review"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    with pytest.raises(ActionableError):
        await mcp_tools.start(str(tmp_path), subagent_md_path="external-write-review", extra_params=params)
    manager.start.assert_not_awaited()

    second.write_text("成功したCIの保存結果\n", encoding="utf-8")
    await mcp_tools.start(str(tmp_path), subagent_md_path="external-write-review", extra_params=params)
    manager.start.assert_awaited_once()
