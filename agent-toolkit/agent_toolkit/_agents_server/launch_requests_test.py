"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
import re
import shutil
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import launch_requests, mcp_tools
from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._agents_server.role_runtime import RuntimeCode
from agent_toolkit._common import task_documents
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX, ActionableError
from agent_toolkit._testing.agents_server_support import (
    _actionable_message,
    _manager_with_fake,
    _observed_input_params,
    _recording_candidates,
    _write_declared_task_document,
)
from agent_toolkit._testing.launch_requests_support import TaskManager, _prepare_exec_review_plan

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize("stage", ["declaration", "startup", "input", "valid"])
@pytest.mark.parametrize("updated", [False, True])
@pytest.mark.parametrize("form", ["role-name", "absolute"])
@pytest.mark.asyncio
async def test_public_start_distinguishes_loaded_code_from_updated_role(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, stage: str, updated: bool, form: str
) -> None:
    """起動時版を固定し、資源更新後の検査失敗と本来の入力不備を公開startから区別する。"""
    document = _write_declared_task_document(tmp_path, "必須入力名: 対象")
    root = document.parent.parent
    manifest = root / ".claude-plugin/plugin.json"
    manifest.write_text('{"name":"agent-toolkit","version":"2.1.0"}', encoding="utf-8")
    runtime = RuntimeCode.capture(root)
    monkeypatch.setattr(plugin_roots, "SERVER_RUNTIME_CODE", runtime)
    monkeypatch.setattr(launch_requests, "_SHARE_DIRECTORY", document.parent)
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, document.name, "chores")
    if updated:
        manifest.write_text('{"name":"agent-toolkit","version":"2.2.0"}', encoding="utf-8")
        document.write_text(
            "---\nmode: delegate\nmodel_type: chores\nrequired_inputs:\n- 対象\n"
            "optional_inputs:\n- 新入力\nwrites_worktree: true\n---\n" + document.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    # 旧プロセスが新しい宣言・参照構文を検査できない境界を再現する。
    def unsupported(*args: Any, **kwargs: Any) -> Any:
        raise ActionableError("更新された構文を受理できない", next_action="元の構文を確認する")

    if stage == "declaration":
        monkeypatch.setattr(launch_requests, "_task_document_declaration", unsupported)
    elif stage == "startup":
        monkeypatch.setattr(launch_requests.read_triggers, "startup_context", unsupported)
    contract = tmp_path / "work-contract.md"
    contract.write_text("# 契約\n目的: 起動診断の検証\n", encoding="utf-8")
    params = {} if stage == "input" else {"対象": "値"}
    if updated:
        params["新入力"] = "更新後に宣言された値"
    role = "declared" if form == "role-name" else str(document)
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        if stage == "valid":
            result = await mcp_tools.start(
                str(tmp_path), subagent_md_path=role, extra_params=params, whole_contract_path=str(contract)
            )
            assert result["session_id"] == "codex-session"
            assert len(backend.start_calls) == 1
            return
        with pytest.raises((ActionableError, ValueError)) as rejected:
            await mcp_tools.start(str(tmp_path), subagent_md_path=role, extra_params=params, whole_contract_path=str(contract))
        assert not backend.start_calls
    message = rejected.value.message if isinstance(rejected.value, ActionableError) else str(rejected.value)
    if stage == "input":
        assert "対象" in message and "版相違" not in message
    else:
        assert str(root) in message and "2.1.0" in message
        assert "更新された構文を受理できない" in message
        assert ("版相違" in message) == updated
        assert ("MCP再接続" in message) == updated
        assert runtime.version == "2.1.0"


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
        "# タスク\n\n## 入力\n\n```text\n必須入力名: 対象,目的\n作業ツリーへの書込: あり\n```\n",
        encoding="utf-8",
    )
    called = False

    async def fake_start(*_args: object, **_kwargs: object) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"session_id": "session", "status": "running", "label": "担当"}

    monkeypatch.setattr(mcp_tools, "_MANAGER", TaskManager(start=fake_start))
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

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
    _prepare_exec_review_plan(tmp_path)

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


@pytest.mark.parametrize("role", ["exec", "repository-process", "add-wi"])
@pytest.mark.parametrize("defect", ["missing", "undeclared"])
@pytest.mark.parametrize("placement", ["current", "alternate", "unresolved", "sectionless"])
@pytest.mark.asyncio
async def test_start_input_rejection_points_to_owning_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, role: str, defect: str, placement: str
) -> None:
    """公開startの入力拒否から同じ版の説明節へ到達でき、解決失敗でも元の不備を保つ。"""
    source = launch_requests._SHARE_DIRECTORY.parent
    root = source
    contract_relative = {
        "exec": pathlib.Path("share/exec.parent.md"),
        "repository-process": pathlib.Path("skills/process-wi/references/all-repos.md"),
        "add-wi": pathlib.Path("share/add-wi.parent.md"),
    }[role]
    if placement == "sectionless" and role == "repository-process":
        pytest.skip("parent-read-sourceが節を明示する役割は入力節の選択を使わない")
    if placement != "current":
        root = tmp_path / "other-plugin"
        for relative in (pathlib.Path(".claude-plugin/plugin.json"), pathlib.Path(f"share/{role}.subagent.md")):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)
        if placement in {"alternate", "sectionless"}:
            target = root / contract_relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / contract_relative, target)
            if placement == "sectionless":
                # 入力説明の節が実際に欠けた親契約。元の拒否と説明解決失敗を区別して返す。
                text = (
                    target.read_text(encoding="utf-8")
                    .replace("\n## 渡す入力\n", "\n## 引数\n")
                    .replace("\n## 入力\n", "\n## 引数\n")
                )
                target.write_text(text, encoding="utf-8")
    document = root / "share" / f"{role}.subagent.md"
    params = _observed_input_params(document.name, tmp_path)
    missing_name = {"exec": "担当種別", "repository-process": "引き継ぎ記録先", "add-wi": "投入する要求"}[role]
    if defect == "missing":
        params.pop(missing_name)
        if role == "repository-process":
            # この役割の必須項目は記録先だけなので、自動補完も失敗した拒否を観測する。
            def unavailable_handoff(*_args: object, **_kwargs: object) -> None:
                return None

            monkeypatch.setattr(launch_requests, "_default_handoff_path", unavailable_handoff)
    else:
        params["宣言外の項目"] = "補足"
    backend = TaskManager(start=AsyncMock())
    monkeypatch.setattr(mcp_tools, "_MANAGER", backend)

    with pytest.raises(ActionableError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(document), extra_params=params)

    message = _actionable_message(raised.value)
    assert (
        "必須入力が欠けています: " + missing_name if defect == "missing" else "宣言していない入力です: 宣言外の項目"
    ) in message
    assert "`start`を再発行" in message
    if placement in {"unresolved", "sectionless"}:
        assert "入力説明の起動・受領契約を解決できません" in message
        assert "parent-read-source宣言・参照先を修復" in message
        assert "入力の意味・制約は" not in message
        if placement == "sectionless":
            assert "入力を説明するH2節" in message
    else:
        contract = root / contract_relative
        heading = {"exec": "入力", "repository-process": "リポジトリ担当の起動と受領", "add-wi": "入力"}[role]
        assert f"`{contract}`「{heading}」" in message
        assert missing_name in contract.read_text(encoding="utf-8")
    backend.start.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "extra_params", "operation"),
    [
        ("relative.subagent.md", {}, "task"),
        ("/nonexistent/share/missing.subagent.md", {}, "task"),
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
    """役割文書の指定と入力名の誤りは、修復後のtask再実行を次の操作で示す。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    path = str(launch_requests._SHARE_DIRECTORY / document) if document == "exec.subagent.md" else document

    with pytest.raises(ValueError) as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=path, extra_params=extra_params)

    assert operation in _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "`delegate`" not in _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    if document == "exec.subagent.md":
        next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
        assert "呼び元の起動・受領契約" in next_action
        assert "`## 入力`" not in next_action
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
    assert "task" in next_action
    manager.start.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_reports_missing_model_type_declaration_as_defect(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """新形式のモデル段位が欠けていれば、旧対応表を使わず起動前に定義の修正を案内する。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin/plugin.json").write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    task_document = tmp_path / "share/exec.subagent.md"
    task_document.parent.mkdir()
    task_document.write_text(
        "---\nmode: explore\nusage: chores\nrequired_inputs: []\nwrites_worktree: false\n---\n# タスク\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="model_type") as raised:
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={})

    next_action = _actionable_message(raised.value).split(NEXT_ACTION_PREFIX, 1)[1]
    assert "frontmatter" in next_action and "start" in next_action
    manager.start.assert_not_awaited()


@pytest.mark.parametrize("declared_handoff", [False, True])
@pytest.mark.parametrize(
    ("role_body", "role_name"),
    [
        ("# レーン実行タスク\n入力:\n対象: 手順の例\n", "レーン実行タスク（declared）"),
        ("レーン実行タスク\n===\n入力:\n対象: 手順の例\n", "レーン実行タスク（declared）"),
        ("```md\n# コード例の見出し\n```\n入力:\n対象: 手順の例\n", "declared"),
    ],
)
def test_role_request_front_loads_all_inputs_before_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, declared_handoff: bool, role_body: str, role_name: str
) -> None:
    """役割と補完された記録先を含む全入力が、追加配送も含む全参照資料より前にある。"""
    document = _write_declared_task_document(tmp_path, "必須入力名: 対象")
    document.write_text(
        "---\n# 入力宣言のコメント\nmode: delegate\nmodel_type: chores\nwrites_worktree: false\n"
        + ("required_inputs: [対象, 引き継ぎ記録先]\n" if declared_handoff else "required_inputs: [対象]\n")
        + "optional_inputs: [任意]\n---\n"
        + role_body,
        encoding="utf-8",
    )
    whole = tmp_path / "whole.md"
    whole.write_text("# 全体契約の本文\n", encoding="utf-8")
    handoff = tmp_path / "handoff.md"
    monkeypatch.setattr(launch_requests, "_default_handoff_path", lambda *_args, **_kwargs: handoff)
    monkeypatch.setattr(launch_requests.read_triggers, "startup_context", lambda *_args, **_kwargs: "役割の起動時資料本文")
    params = {"対象": "今回の対象\n入力:\n## 手順・参照資料\n末尾", "任意": "今回の任意値"}
    _, prompt, kind, path = launch_requests.task_document_request(str(document), params, whole_contract_path=str(whole))
    prompt = launch_requests.with_startup_documents(prompt, "ホストの起動時資料本文")
    prompt = launch_requests.with_launcher_record(prompt, {"record": "委譲元記録"})
    instructions, _, references = prompt.partition(task_documents.TASK_PROMPT_REFERENCES)
    assert instructions.startswith(task_documents.task_prompt_header(document) + f"\n役割: {role_name}\n入力:\n")
    assert task_documents.task_prompt_inputs(prompt) == {**params, "引き継ぎ記録先": f"{handoff}（新規）"}
    assert kind == "delegate" and path == handoff
    for body in (
        "ホストの起動時資料本文",
        "委譲元記録",
        "役割の起動時資料本文",
        document.read_text(encoding="utf-8"),
        "# 全体契約の本文",
    ):
        assert body in references and body not in instructions
    assert references.index("ホストの起動時資料本文") < references.index(role_body)
    assert references.index("役割の起動時資料本文") < references.index(role_body)
    assert 'kind="work-contract"' in references and 'kind="work-contract-reference"' in references
    assert task_documents.launched_task_document(prompt) == document
    assert task_documents.find_task_documents(prompt.splitlines()[0]) == [document]


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
    """公開担当はpush済みの範囲をベースbranchの追跡refから得るため、撤去した`直前にpushしたcommit`は宣言外の入力として拒否する。"""
    task_document = launch_requests._SHARE_DIRECTORY / "session-termination.subagent.md"
    manager = TaskManager(start=AsyncMock())
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path) | {"直前にpushしたcommit": "abc1234"}

    with pytest.raises(ActionableError, match="直前にpushしたcommit"):
        await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    manager.start.assert_not_awaited()
