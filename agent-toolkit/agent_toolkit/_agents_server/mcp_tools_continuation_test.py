"""mcp_toolsのcontinuationの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import launch_requests, mcp_tools, session_handoff, state
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_mcp_tools_support import _start_exec_review_session, _write_review_plan
from agent_toolkit._testing.agents_server_support import (
    _complete,
    _manager_with_fake,
    _observed_input_params,
    _recording_candidates,
    _without_root,
)
from agent_toolkit._testing.helpers import delivery_payload

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.mark.asyncio
async def test_new_request_and_handoff_preserve_required_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """新要求を配送し、後任にも原要求と追送を保持したまま定型継続できる。"""
    _write_review_plan(tmp_path)
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        conversation = tmp_path / "conversation.md"
        conversation.write_text("外部の会話抽出が返した記録\n", encoding="utf-8")
        record = tmp_path / "record.jsonl"
        record.write_text("{}\n", encoding="utf-8")

        def extract_context(source: state.SessionResumeState, **_kwargs: Any) -> str:
            # 外部CLIの会話抽出だけを代替し、保持情報からの引き継ぎ本文生成は実処理を使う。
            return session_handoff.compose_prompt(source, conversation, (record,))

        monkeypatch.setattr(session_handoff, "prepare_prompt", extract_context)
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="exec-review",
            extra_params=_observed_input_params("exec-review.subagent.md", tmp_path),
        )
        await mcp_tools.send_message(started["session_id"], "追加要求: 新しい文書の差分も確認する")
        assert "新しい文書の差分" in backend.prompts[-1]
        for _ in range(2):
            await mcp_tools.send_message(started["session_id"], "現在の作業を続行する")
        assert backend.send_calls == 3
        document = launch_requests._SHARE_DIRECTORY / "exec-review.subagent.md"
        with pytest.raises(ValueError, match="役割文書が全文再掲"):
            await mcp_tools.send_message(started["session_id"], document.read_text(encoding="utf-8"))
        assert backend.send_calls == 3
        _complete(manager.sessions[started["session_id"]])
        successor = await mcp_tools.start(handoff_from=started["session_id"])
        received = manager.sessions[successor["session_id"]].prompt
        assert "レビュー基準: 計画" in received
        assert "追加要求: 新しい文書の差分も確認する" in received
        await mcp_tools.send_message(successor["session_id"], instruction="再レビューせよ", extra_params={"round": "2"})
        assert delivery_payload(backend.prompts[-1]) == "再レビューせよ\nround: 2"


@pytest.mark.asyncio
async def test_send_message_tool_returns_previous_result(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """未回収の結果を持つ終了済みsessionへ送ると、MCPの応答がその結果を返す。"""
    async with _manager_with_fake("codex") as (manager, _):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        session = state.SessionState("thread-1", str(tmp_path), engine="codex")
        _complete(session, message="計画作成完了")
        manager.sessions[session.session_id] = session

        response = await mcp_tools.send_message(session.session_id, "実装開始")

        assert _without_root(response) == {
            "delivery": "reply_started",
            "label": "",
            "previous_result": {"status": "completed", "agent_message": "計画作成完了"},
        }


@pytest.mark.asyncio
async def test_followup_rejects_extra_lines_before_delivery(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """定型追送へ説明と初回入力を加えると、公開send_messageは配送を拒否し履歴を保つ。"""
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    session = manager.sessions[started["session_id"]]
    count = len(backend.prompts)
    try:
        for extra in ("検証結果: 成功", "担当別の根拠を説明します", "レビュー基準: 計画"):
            with pytest.raises(ToolError, match="入力|行") as raised:
                await mcp_tools.mcp.call_tool(
                    "send_message",
                    {
                        "session_id": session.session_id,
                        "prompt": "再レビューせよ\nround: 2\n" + extra,
                    },
                )
            assert extra.partition(": ")[0] in str(raised.value)
            assert "最小の再実行形" in str(raised.value)
            assert len(backend.prompts) == count
            assert not session.followup_prompts
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_continuation_input_errors_return_reason_and_next_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """継続入力の形式不成立は、配送せずに理由と次の操作をツールのエラー応答で返す。

    PreToolUseのhookは入力形式を判定しないため、修正案内はこの応答だけが担う。
    """
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    session_id = started["session_id"]
    count = len(backend.prompts)
    freeform: dict[str, str] = {}
    forms: list[dict[str, Any]] = []
    cases = [
        ("send_message", {"session_id": session_id, "prompt": "続行する", "instruction": "再レビューせよ"}, "排他的"),
        ("send_message", {"session_id": session_id}, "排他的"),
        ("send_message", {"session_id": session_id, "message": "続行する"}, "排他的"),
        ("send_message", {"session_id": session_id, "prompt": "続行する", "extra_params": {}}, "排他的"),
        ("send_message", {"session_id": session_id, "instruction": "宣言されていない指示"}, "宣言された継続指示ではありません"),
        ("send_message", {"session_id": session_id, "prompt": "   "}, "prompt must be a non-empty string"),
        ("send_message", {"session_id": "", "prompt": "続行する"}, "invalid session_id"),
        ("kill", {"session_id": ""}, "invalid session_id"),
    ]
    try:
        for tool, arguments, reason in cases:
            with pytest.raises(ToolError) as raised:
                await mcp_tools.mcp.call_tool(tool, arguments)
            message = str(raised.value)
            assert reason in message, (tool, arguments, message)
            assert NEXT_ACTION_PREFIX in message, (tool, arguments, message)
            assert len(backend.prompts) == count
            assert not manager.sessions[session_id].followup_prompts
            if reason in {"排他的", "宣言された継続指示ではありません"}:
                freeform, _ = json.JSONDecoder().raw_decode(message.split("自由文の再実行形: ", 1)[1])
                forms = json.loads(message.split("定型指示の最小の再実行形: ", 1)[1])
                assert freeform == {"prompt": "<今回の指示と新しい入力>"}
                assert {"instruction": "再レビューせよ", "extra_params": {"round": "<round>"}} in forms
        assert freeform and forms
        await mcp_tools.mcp.call_tool("send_message", {"session_id": session_id, **freeform})
        assert "<今回の指示と新しい入力>" in backend.prompts[-1]
        retry = next(item for item in forms if item["instruction"] == "再レビューせよ")
        retry["extra_params"]["round"] = "2"
        await mcp_tools.mcp.call_tool("send_message", {"session_id": session_id, **retry})
        assert "再レビューせよ\nround: 2" in backend.prompts[-1]
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_followup_preserves_required_and_updated_inputs(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """構造化・自由本文は必要な制御値と同じパスの更新を保持する。"""
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    contract = tmp_path / "更新 契約.yaml"
    contract.write_text("clauses: []\n", encoding="utf-8")
    try:
        for round_number in ("2", "3"):
            contract.write_text(f"round: {round_number}\n", encoding="utf-8")
            await mcp_tools.mcp.call_tool(
                "send_message",
                {
                    "session_id": started["session_id"],
                    "instruction": "再レビューせよ",
                    "extra_params": {"round": round_number, "review_contract": str(contract)},
                },
            )
            assert f"round: {round_number}" in backend.prompts[-1]
            assert str(contract) in backend.prompts[-1]
        await mcp_tools.send_message(started["session_id"], "再レビューせよ\nround: 4")
        assert "round: 4" in backend.prompts[-1]
        await mcp_tools.send_message(
            started["session_id"], instruction="再レビューせよ", extra_params={"round": "5", "rewrite_map": str(contract)}
        )
        assert f"rewrite_map: {contract}" in backend.prompts[-1]
        direct_contract = "目的: 必要な入力を保持する\n\n対象: 現在の差分"
        await mcp_tools.send_message(
            started["session_id"],
            instruction="再レビューせよ",
            extra_params={"round": "6", "review_contract": direct_contract},
        )
        expected = "再レビューせよ\nround: 6\nreview_contract:\n  目的: 必要な入力を保持する\n  \n  対象: 現在の差分"
        assert delivery_payload(backend.prompts[-1]) == expected
        await mcp_tools.send_message(started["session_id"], expected.replace("round: 6", "round: 7"))
        assert delivery_payload(backend.prompts[-1]) == expected.replace("round: 6", "round: 7")
        before = backend.send_calls
        for params in (
            {},
            {"round": "8", "rewrite_map": str(contract) + " 説明"},
            {"round": "8", "review_contract": str(contract) + " 説明"},
        ):
            with pytest.raises(ToolError):
                await mcp_tools.mcp.call_tool(
                    "send_message",
                    {
                        "session_id": started["session_id"],
                        "instruction": "再レビューせよ",
                        "extra_params": params,
                    },
                )
        assert backend.send_calls == before
    finally:
        await manager.close()


@pytest.mark.parametrize("declaration_state", ["宣言なし", "取得不能", "文書消失"])
@pytest.mark.asyncio
async def test_continuation_recovery_without_readable_declaration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, declaration_state: str
) -> None:
    """宣言の不在と取得失敗を区別し、案内された自由文で同じ担当へ配送できる。"""
    manager, backend, started = await _start_exec_review_session(monkeypatch, tmp_path)
    session = manager.sessions[started["session_id"]]
    if declaration_state == "宣言なし":
        monkeypatch.setattr(session, "prompt", "自由文で起動した担当")
    elif declaration_state == "文書消失":
        monkeypatch.setattr(launch_requests.task_documents, "find_task_documents", lambda _text: [])
    else:
        monkeypatch.setattr(launch_requests.task_documents, "read_declaration", lambda _path: "読取失敗の診断")
    count = len(backend.prompts)
    retry: dict[str, str] = {}
    try:
        for args in ({}, {"instruction": "未知の指示"}, {"prompt": "続行", "extra_params": {}}):
            with pytest.raises(ToolError) as raised:
                await mcp_tools.mcp.call_tool("send_message", {"session_id": session.session_id, **args})
            message = str(raised.value)
            assert len(backend.prompts) == count
            assert not session.followup_prompts
            if declaration_state == "宣言なし":
                assert "宣言された定型指示はありません" in message
            else:
                diagnosis = "読取失敗の診断" if declaration_state == "取得不能" else "起動記録の役割文書へ到達できません"
                assert "定型指示は確認できません: " + diagnosis in message
                assert "定型指示はありません" not in message
            retry, _ = json.JSONDecoder().raw_decode(message.split("自由文の再実行形: ", 1)[1])
        assert retry
        await mcp_tools.mcp.call_tool("send_message", {"session_id": session.session_id, **retry})
        assert "<今回の指示と新しい入力>" in backend.prompts[-1]
        assert len(session.followup_prompts) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_wi_policy_review_continues_to_draft_in_same_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """原稿なしの方針検収から、同じ公開sessionへの本文確認へ進める。"""
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        materials = {}
        for name in ("方針", "原稿", "要求と出所", "成立性の証拠"):
            path = tmp_path / f"{name}.md"
            path.write_text(f"# {name}\n", encoding="utf-8")
            materials[name] = str(path)
        common = {key: materials[key] for key in ("要求と出所", "成立性の証拠")}
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="wi-draft-review",
            extra_params={
                **common,
                "確認段階": "方針",
                "方針": materials["方針"],
                "引き継ぎ記録先": str(tmp_path / "handoff.md"),
            },
        )
        assert f"方針: {materials['方針']}" in backend.prompts[0]
        session_id = started["session_id"]
        await mcp_tools.mcp.call_tool(
            "send_message",
            {
                "session_id": session_id,
                "instruction": "本文確認",
                "extra_params": {**common, "原稿": materials["原稿"]},
            },
        )
        assert "本文確認" in backend.prompts[-1]
        assert materials["原稿"] in backend.prompts[-1]
        assert len(manager.sessions) == 1
        assert len(manager.sessions[session_id].followup_prompts) == 1


@pytest.mark.parametrize(
    ("role", "instruction", "params"),
    [
        ("exec", "実装開始", {}),
        ("exec", "実装なし", {}),
        ("exec", "レビュー指摘の対応をせよ", {"レビュー指摘管理表": "@table"}),
        ("exec", "レビュー指摘の対応をせよ", {"レビュー指摘管理表": "@tables"}),
        (
            "exec",
            "統合開始",
            {
                "統合区分": "マージなし",
                "レーン開始時刻": "2026-10-18T19:30:00+09:00",
                "実行レビュー済みHEAD": "なし",
                "統合先worktree": "/repo",
                "統合先branch": "develop",
                "計画ファイル名一覧": "[]",
                "AWI終了区分": "adopt",
            },
        ),
        ("add-wi", "保存開始", {"要求識別子": "request-1"}),
    ],
)
@pytest.mark.asyncio
async def test_declared_role_continuations_keep_required_inputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    role: str,
    instruction: str,
    params: dict[str, str],
) -> None:
    """各役割の継続指示は必要入力だけを配送し、書込担当一覧は起動応答の実sessionで更新する。"""
    table = tmp_path / "review.tsv"
    if params.get("レビュー指摘管理表") in ("@table", "@tables"):
        table.write_text("指摘の記録\n", encoding="utf-8")
        other_table = tmp_path / "別計画の review.tsv"
        other_table.write_text("別計画の指摘\n", encoding="utf-8")
        value = str(table) if params["レビュー指摘管理表"] == "@table" else json.dumps([str(table), str(other_table)])
        params = {**params, "レビュー指摘管理表": value}
    _recording_candidates(monkeypatch)
    async with _manager_with_fake("codex") as (manager, backend):
        monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path=role,
            extra_params=_observed_input_params(f"{role}.subagent.md", tmp_path),
        )
        if role == "exec":
            initial_writers = _observed_input_params("exec.subagent.md", tmp_path)["書込担当一覧"]
            assert f"書込担当一覧: {initial_writers}" in backend.prompts[-1]
            writers = json.dumps([*json.loads(initial_writers), started["session_id"]])
            await mcp_tools.send_message(started["session_id"], f"書込担当一覧: {writers}")
            assert f"書込担当一覧: {writers}" in backend.prompts[-1]
            if instruction == "統合開始":
                params = {**params, "書込担当一覧": writers}
                count = len(backend.prompts)
                with pytest.raises(ToolError, match="書込担当一覧"):
                    await mcp_tools.mcp.call_tool(
                        "send_message",
                        {
                            "session_id": started["session_id"],
                            "instruction": instruction,
                            "extra_params": {key: value for key, value in params.items() if key != "書込担当一覧"},
                        },
                    )
                assert len(backend.prompts) == count
        await mcp_tools.mcp.call_tool(
            "send_message",
            {
                "session_id": started["session_id"],
                "instruction": instruction,
                "extra_params": params,
            },
        )
        expected = "\n".join([instruction, *(f"{name}: {value}" for name, value in params.items())])
        assert delivery_payload(backend.prompts[-1]) == expected
        if role == "exec" and instruction == "レビュー指摘の対応をせよ":
            await mcp_tools.mcp.call_tool("send_message", {"session_id": started["session_id"], "prompt": expected})
            assert delivery_payload(backend.prompts[-1]) == expected
            session = manager.sessions[started["session_id"]]
            received = len(backend.prompts)
            history = session.followup_prompts
            missing = str(tmp_path / "存在しない表.tsv")
            for invalid_params in (
                {**params, "レビュー指摘管理表": json.dumps([str(table), missing])},
                {**params, "round": "1"},
            ):
                with pytest.raises(ToolError) as raised:
                    await mcp_tools.mcp.call_tool(
                        "send_message",
                        {"session_id": started["session_id"], "instruction": instruction, "extra_params": invalid_params},
                    )
                assert "最小の再実行形" in str(raised.value)
                assert missing in str(raised.value) if "round" not in invalid_params else "round" in str(raised.value)
                assert len(backend.prompts) == received
                assert session.followup_prompts == history
            for extra in ("検証結果: 成功", "担当別根拠を補足する"):
                with pytest.raises(ToolError) as raised:
                    await mcp_tools.mcp.call_tool(
                        "send_message", {"session_id": started["session_id"], "prompt": expected + "\n" + extra}
                    )
                assert extra.partition(": ")[0] in str(raised.value)
                assert "最小の再実行形" in str(raised.value)
                assert len(backend.prompts) == received
                assert session.followup_prompts == history
