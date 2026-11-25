"""`_agents_server/launch_requests.py`の振る舞いを検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import pathlib
import re
from unittest.mock import AsyncMock

import pytest

from agent_toolkit._agents_server import cli_turn, launch_requests, mcp_tools
from agent_toolkit._atk import config as _atk_config
from agent_toolkit._atk import managed_temp as _managed_temp
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    _manager_with_fake,
    _observed_input_lines,
    _observed_input_params,
    _recording_candidates,
    _write_declared_task_document,
    install_backend,
)
from agent_toolkit._testing.launch_requests_support import TaskManager, _prepare_exec_review_plan

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.parametrize(
    ("role", "message"),
    [
        (False, "状態: completed\n結果: 調査済み\n根拠: 対象本文\n気付いた改善点: 改善案"),
        (False, "結果: 読了部分\n続行できない区分: owner_input\n続行できない理由: 未提供資料が必要\n気付いた改善点: 改善案"),
        (True, "状態: completed\n未解決の指摘数: 0\n読んだ変更箇所数: 1\n全件で問題が無い"),
    ],
)
@pytest.mark.asyncio
async def test_start_explore_result_matches_delivered_role(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, role: bool, message: str
) -> None:
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        if role:
            document = launch_requests.resolve_task_document_path("bulk-replace-review")
            response = await mcp_tools.start(
                str(tmp_path), subagent_md_path=str(document), extra_params=_observed_input_params(document.name, tmp_path)
            )
        else:
            response = await mcp_tools.start(str(tmp_path), mode="explore", prompt="対象の調査", model_type="chores")
        session = manager.sessions[response["session_id"]]
        assert backend.start_calls[0][2] == "explore" and backend.prompts
        cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})
        if "続行できない理由:" in message:
            assert session.result_contract_recovery_pending and not session.result_available
            session.result_contract_recovery_consumed = True
            cli_turn.finalize_turn(session, {"status": "completed", "agent_message": message, "error": None})
        shown = await mcp_tools.show_session(session.session_id)
        assert shown["status"] == "completed" and shown["result_available"]
        assert session.agent_message == message and not session.result_contract_recovery_pending


@pytest.mark.asyncio
async def test_start_uses_declared_launch_kind(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`mode: explore`を宣言した`<役割名>.subagent.md`は`start_explore`と同じ軽量な起動条件で起動する。

    起動条件はsession記録の`launch_kind`と、backendへ渡すシステム指示・許可ツールを決める種別で確かめる。
    """
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        explore_role = launch_requests.resolve_task_document_path("bulk-replace-review")

        response = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=str(explore_role),
            extra_params=_observed_input_params(explore_role.name, tmp_path),
        )

        shown = await manager.show_session(response["session_id"])
        assert shown["launch_kind"] == "explore"
        assert shown["model_type"] == "review_fast"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("task_name", "launch_kind", "model_type"),
    [
        ("external-write-review.subagent.md", "explore", "review_fast"),
        ("reader-fit-review.subagent.md", "explore", "review_fast"),
        ("bulk-replace-review.subagent.md", "explore", "review_fast"),
        ("copilot-review-audit.subagent.md", "delegate", "execute"),
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
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        task_document = launch_requests.resolve_task_document_path(task_name.removesuffix(".subagent.md"))

        response = await mcp_tools.start(
            str(tmp_path), subagent_md_path=str(task_document), extra_params=_observed_input_params(task_name, tmp_path)
        )

        shown = await manager.show_session(response["session_id"])
        assert shown["launch_kind"] == launch_kind
        assert shown["model_type"] == model_type


@pytest.mark.asyncio
async def test_start_without_launch_kind_uses_delegate(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`mode:`行の無い`<役割名>.subagent.md`は通常委譲で起動し、不正な値は宣言を読めない扱いで通常委譲とする。"""
    monkeypatch.setattr(launch_requests, "_HANDOFF_SESSION", ("caller-session",))
    session_area = _managed_temp.resolve_session_temp("caller-session")
    (session_area / "work-contract.md").write_text("目的: 宣言に応じた`mode`を確かめる\n", encoding="utf-8")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    task_document = _write_declared_task_document(tmp_path, "必須入力名: 対象\n任意入力名: 補足")
    monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "execute")

    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値", "補足": "値"})
    task_document.write_text(
        task_document.read_text(encoding="utf-8").replace("任意入力名: 補足", "mode: batch"), encoding="utf-8"
    )
    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params={"対象": "値", "未宣言": "値"})

    assert [call.kwargs["launch_kind"] for call in manager.start.await_args_list] == ["delegate", "delegate"]


@pytest.mark.asyncio
async def test_start_reads_legacy_launch_kind_line(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`mode:`へ改める前の宣言行を持つ旧形式の`<役割名>.subagent.md`も同じ値で読む。"""
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    current = _write_declared_task_document(tmp_path / "current", "必須入力名: 対象\nmode: explore")
    legacy = _write_declared_task_document(tmp_path / "legacy", "必須入力名: 対象\n起動種別: explore")
    for task_document in (current, legacy):
        monkeypatch.setitem(launch_requests._TASK_MODEL_TYPES, task_document.name, "chores_fast")
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
        "reader-fit-review.subagent.md",
        "repository-process.subagent.md",
        "session-termination.subagent.md",
    ],
)
def test_observed_delegation_prompts_include_required_inputs(task_name: str, tmp_path: pathlib.Path) -> None:
    task_document = launch_requests.resolve_task_document_path(task_name.removesuffix(".subagent.md"))
    extra_params = _observed_input_params(task_name, tmp_path)
    if task_name == "exec-review.subagent.md":
        _prepare_exec_review_plan(tmp_path)
    if task_name == "repository-process.subagent.md":
        with pytest.raises(ActionableError, match="ownership_record"):
            launch_requests._validate_required_prompt_inputs(task_document, extra_params)
        del extra_params["ownership_record"]
        del extra_params["ownership_token"]

    assert launch_requests._validate_required_prompt_inputs(task_document, extra_params) is None


_LANE_OWNER_INPUTS = ("所有登録", "統合先worktree", "統合先branch", "レーン開始時刻")


@pytest.mark.parametrize("missing", _LANE_OWNER_INPUTS)
def test_lane_owner_launch_without_lane_input_is_rejected(missing: str, tmp_path: pathlib.Path) -> None:
    task_document = launch_requests.resolve_task_document_path("exec")
    extra_params = _observed_input_params("exec.subagent.md", tmp_path)
    assert extra_params["担当種別"] == "レーン担当" and set(_LANE_OWNER_INPUTS) <= set(extra_params)
    del extra_params[missing]

    with pytest.raises(ActionableError, match=rf"必須入力が欠けています: {missing};"):
        launch_requests._validate_required_prompt_inputs(task_document, extra_params)


@pytest.mark.parametrize(
    ("name", "value", "rejected", "reason"),
    [
        ("所有登録", "lane-01.json", "所有登録", "所有登録ファイルを絶対パスで読めない"),
        ("所有登録", "{root}/missing.json", "所有登録", "所有登録ファイルを絶対パスで読めない"),
        ("所有登録", "{root}/other-lane.json", "レーン識別子", "`lane`（lane-02）と一致しない"),
        ("統合先branch", "main", "統合先branch", "`base_branch`（develop）と一致しない"),
        ("統合先worktree", "{root}/missing", "統合先worktree", "実在するディレクトリの絶対パスではない"),
        ("レーン開始時刻", "2026-11-15T13:17:00", "レーン開始時刻", "時差付きISO 8601"),
        ("レーン開始時刻", "2999-01-01T00:00:00+09:00", "レーン開始時刻", "現在時刻より後"),
    ],
)
def test_lane_owner_launch_with_wrong_value_is_rejected(
    name: str, value: str, rejected: str, reason: str, tmp_path: pathlib.Path
) -> None:
    task_document = launch_requests.resolve_task_document_path("exec")
    extra_params = _observed_input_params("exec.subagent.md", tmp_path)
    assert launch_requests._validate_required_prompt_inputs(task_document, extra_params) is None
    (tmp_path / "other-lane.json").write_text('{"lane": "lane-02", "base_branch": "develop"}\n', encoding="utf-8")
    extra_params[name] = value.format(root=tmp_path)

    with pytest.raises(ActionableError, match=rf"レーン担当の入力が不成立です: {rejected}: .*{re.escape(reason)}"):
        launch_requests._validate_required_prompt_inputs(task_document, extra_params)


@pytest.mark.parametrize("kind", ["CI修正担当", "レビュー修正担当"])
def test_fix_owner_launch_does_not_need_lane_inputs(kind: str, tmp_path: pathlib.Path) -> None:
    task_document = launch_requests.resolve_task_document_path("exec")
    extra_params = {
        name: value
        for name, value in _observed_input_params("exec.subagent.md", tmp_path).items()
        if name not in _LANE_OWNER_INPUTS
    }
    extra_params["担当種別"] = kind

    assert launch_requests._validate_required_prompt_inputs(task_document, extra_params) is None


@pytest.mark.parametrize("rereview", [False, True])
@pytest.mark.asyncio
async def test_reader_fit_public_start_accepts_declared_review_inputs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, rereview: bool
) -> None:
    task = launch_requests.resolve_task_document_path("reader-fit-review")
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
    task = launch_requests.resolve_task_document_path("exec-review")
    _prepare_exec_review_plan(tmp_path)
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
    task_document = launch_requests.resolve_task_document_path(task_name.removesuffix(".subagent.md"))
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "session", "status": "running", "label": "担当"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    extra_params = _observed_input_params(task_document.name, tmp_path)

    response = await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=extra_params)

    assert response == {"session_id": "session", "status": "running", "label": "担当"}
    manager.start.assert_awaited_once()
    model_type, prompt, cwd = manager.start.await_args.args
    assert model_type == "execute"
    assert cwd == str(tmp_path)
    assert str(task_document) in prompt
    assert all(f"{key}: {value}" in prompt for key, value in extra_params.items())


@pytest.mark.asyncio
async def test_public_lane_resolves_high_tier_candidates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", "claude:opus/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CHORES_MODEL", "claude:sonnet/medium")
    async with _manager_with_fake("claude") as (manager, _backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        task_document = launch_requests.resolve_task_document_path("exec")
        response = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=str(task_document),
            extra_params=_observed_input_params(task_document.name, tmp_path),
        )
        session = manager.sessions[response["session_id"]]
        assert (session.engine, session.model, session.effort) == ("claude", "opus", "high")
        assert session.model_type == "execute"


@pytest.mark.asyncio
@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("route", ["専念型", "付随型"])
async def test_wi_owner_uses_default_base_high_tier_candidates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, route: str
) -> None:
    other = "codex" if engine == "claude" else "claude"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_EXECUTE_MODEL", f"{engine}:wi-owner/high")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_MODEL", f"{other}:orchestrator/high")
    async with _manager_with_fake(engine) as (current, _backend):
        install_backend(current, other, FakeBackend(current.sessions, other))
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        task = launch_requests.resolve_task_document_path("add-wi")
        params = {**_observed_input_params(task.name, tmp_path), "投入経路": route}
        response = await mcp_tools.start(cwd=str(tmp_path), subagent_md_path=str(task), extra_params=params)
        session = current.sessions[response["session_id"]]
        assert (session.engine, session.model, session.effort) == (engine, "wi-owner", "high")
        assert session.model_type == "execute"
        assert session.launch_kind == "delegate"
        assert _atk_config.parse_unresolved_model_candidates("orchestrate")[0] == (other, "orchestrator", "high")


@pytest.mark.asyncio
async def test_start_accepts_independent_wi_draft_review(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    task_document = launch_requests.resolve_task_document_path("wi-draft-review")
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "review", "status": "running", "label": "review"}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = {key: str(tmp_path / f"{key}.md") for key in ("原稿", "要求原本", "引き継ぎ記録先")}
    for key in ("原稿", "要求原本"):
        pathlib.Path(params[key]).write_text("# 資料\n", encoding="utf-8")
    await mcp_tools.start(str(tmp_path), subagent_md_path=str(task_document), extra_params=params)
    assert manager.start.await_args.args[0] == "review"
    assert "usage" not in manager.start.await_args.kwargs


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        ("exec", "execute"),
        ("add-wi", "execute"),
        ("copilot-review-audit", "execute"),
        ("defect-investigation", "execute"),
        ("lane-integration", "execute"),
        ("session-termination", "execute"),
        ("exec-review", "review"),
        ("usability-review", "review"),
        ("wi-draft-review", "review"),
        ("bulk-replace-review", "review_fast"),
        ("external-write-review", "review_fast"),
        ("reader-fit-review", "review_fast"),
        ("refine-prompt", "chores"),
    ],
)
@pytest.mark.asyncio
async def test_public_start_resolves_roles_to_required_engineering_stage(tmp_path, monkeypatch, role, expected):
    if role == "exec-review":
        _prepare_exec_review_plan(tmp_path)
    manager = TaskManager(start=AsyncMock(return_value={"session_id": "role", "status": "running", "label": role}))
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    additional_inputs = {
        "usability-review": {
            "対象worktree": str(tmp_path),
            "変更画面": "screen.html",
            "同種の既存画面": "なし",
            "主要シナリオ": "操作",
            "検証環境": "Linux",
            "引き継ぎ記録先": str(tmp_path / "handoff.md"),
            "round": "1",
        },
        "wi-draft-review": {key: str(tmp_path / f"{key}.md") for key in ("原稿", "要求原本", "引き継ぎ記録先")},
        "refine-prompt": {"対象プロンプト": str(tmp_path / "prompt.md"), "シナリオ": "なし"},
    }
    inputs = additional_inputs[role] if role in additional_inputs else _observed_input_params(f"{role}.subagent.md", tmp_path)
    if role == "wi-draft-review":
        for key in ("原稿", "要求原本"):
            pathlib.Path(inputs[key]).write_text("# 資料\n", encoding="utf-8")
    await mcp_tools.start(str(tmp_path), subagent_md_path=role, extra_params=inputs)
    assert manager.start.await_args.args[0] == expected
    assert "usage" not in manager.start.await_args.kwargs


@pytest.mark.parametrize("stage", [None, "本文", "方針"])
def test_wi_draft_review_requires_material_for_selected_stage(tmp_path: pathlib.Path, stage: str | None) -> None:
    """段階省略の本文互換を保ち、方針と本文の条件付き必須資料を検査する。"""
    document = launch_requests.resolve_task_document_path("wi-draft-review")
    path = tmp_path / "資料.md"
    path.write_text("# 資料\n", encoding="utf-8")
    params = {name: str(path) for name in ("要求原本", "引き継ぎ記録先")}
    if stage is not None:
        params["確認段階"] = stage
    material = "方針" if stage == "方針" else "原稿"
    with pytest.raises(ActionableError, match=material):
        launch_requests._validate_required_prompt_inputs(document, params)
    params[material] = str(path)
    assert launch_requests._validate_required_prompt_inputs(document, params) is None
    params[material] = str(tmp_path / "未作成.md")
    with pytest.raises(ActionableError, match=material):
        launch_requests._validate_required_prompt_inputs(document, params)
