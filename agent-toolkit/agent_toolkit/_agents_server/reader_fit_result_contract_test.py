"""生成されたreader-fit起動本文から、対象別の返却回復までを検収する。"""

import asyncio
import json
import pathlib
from types import SimpleNamespace
from typing import Any

# SDK入出力からManagerの状態通知までを通すため、内部の同期点を共有する。
# pylint: disable=protected-access
import claude_agent_sdk
import pytest

from agent_toolkit._agents_server import claude, launch_requests, manager, result_contract, state
from agent_toolkit._atk import config
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    QueueClaudeClient,
    ResultMessage,
    SystemMessage,
    install_backend,
)


def _reader_request(tmp_path):
    paths = [str(tmp_path / name) for name in ("first.md", "second.md")]
    for path in paths:
        pathlib.Path(path).write_text("# 対象\n本文\n", encoding="utf-8")
    role = pathlib.Path(__file__).resolve().parents[2] / "share/reader-fit-review.subagent.md"
    _, prompt, kind, _ = launch_requests.task_document_request(
        str(role),
        {
            "成果物": json.dumps(paths),
            "種別": "エージェント向け文書",
            "読者像": "実行担当",
            "修正範囲": "新規の全文",
            "比較資料": json.dumps([{"比較元本文": "新規", "今回差分": "全文", "現行本文": path} for path in paths]),
        },
    )
    blocks = [
        f"- 成果物: {path}\n  完了状態: completed\n  比較資料: 新規・全文\n  判定: 指摘なし\n  根拠: 本文と呼出先を照合済み"
        for path in paths
    ]
    return paths, prompt, kind, blocks


@pytest.mark.parametrize(
    "case", ["valid", "missing", "duplicate", "collection", "unknown", "no-state", "blocked", "blocked-overall"]
)
@pytest.mark.asyncio
async def test_reader_fit_requires_individual_completion(tmp_path, case):
    """有効対象を保持し、入力集合・個別完了に反する返却を同じ回復へ渡す。"""
    paths, prompt, kind, blocks = _reader_request(tmp_path)
    session = state.SessionState("reader-fit", str(tmp_path), prompt=prompt, launch_kind=kind)
    if case == "missing":
        blocks.pop()
    elif case == "duplicate":
        blocks.append(blocks[0])
    elif case == "collection":
        blocks = ["- 成果物: " + json.dumps(paths) + "\n  完了状態: completed\n  判定: 全件確認済み"]
    elif case == "unknown":
        blocks[1] = blocks[1].replace(paths[1], str(tmp_path / "outside.md"))
    elif case == "no-state":
        blocks[1] = blocks[1].replace("  完了状態: completed\n", "")
    elif case.startswith("blocked"):
        blocks[1] = (
            blocks[1]
            .replace("完了状態: completed", "完了状態: blocked")
            .replace(
                "  判定: 指摘なし",
                "  続行できない区分: inaccessible_input\n  続行できない理由: 比較資料の提供元へ再取得を求める",
            )
        )
    overall = "" if case == "blocked" else "状態: completed\n"
    message = overall + "未解決の指摘数: 0\n対象別の判定と証拠:\n" + "\n".join(blocks) + "\n新しい要件: なし"
    result: dict[str, Any] = {"status": "completed", "agent_message": message, "error": None}
    valid = case in {"valid", "blocked"}
    assert result_contract.hold_invalid_result(session, result) is not valid
    if valid:
        assert session.pending_result is None
    else:
        assert session.pending_result == result
        assert session.result_contract_recovery_reason
        session.result_contract_recovery_consumed = True
        session.clear_hold()
        assert not result_contract.hold_invalid_result(session, result)
        assert result["status"] == "failed"
        assert result["agent_message"] == message
        error = result["error"]
        assert isinstance(error, dict)
        # 検査関数による辞書更新後も、Pylintは初期値Noneから推論する。
        assert "resultContract" in error  # pylint: disable=unsupported-membership-test


@pytest.mark.parametrize("idle_after_result", [False, True])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_reader_fit_recovery_receives_complete_resubmission(tmp_path, monkeypatch, idle_after_result):
    """不正な全体成功を回復し、同じ会話から全対象の再提出をwaitで受領する。"""
    _paths, prompt, kind, blocks = _reader_request(tmp_path)
    final = "状態: completed\n対象別の判定と証拠:\n" + "\n".join(blocks)
    incomplete = "状態: completed\n対象別の判定と証拠:\n" + blocks[0]
    client = QueueClaudeClient(
        [
            [SystemMessage("reader-parent"), AssistantMessage(incomplete), ResultMessage(incomplete)]
            + ([claude_agent_sdk.SystemMessage("session_state_changed", {"state": "idle"})] if idle_after_result else []),
            [AssistantMessage(final), ResultMessage(final)],
        ]
    )
    publications = []
    monkeypatch.setattr(state, "_TERMINAL_LISTENERS", {lambda session: publications.append(session.agent_message)})
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("claude", "model", "high")])
    # SDKの入出力だけを代用し、結果検査・形式回復・再提出の公開は実装を通す。
    async with (
        manager.AgentsServerManager(None) as owner,
        claude.ClaudeServerManager(owner.sessions, owner._condition, client_factory=lambda _options: client) as backend,
    ):
        monkeypatch.setattr(claude, "_build_options", lambda *_args, **_kwargs: SimpleNamespace())
        install_backend(owner, "claude", backend)
        started = await owner.start("execute", prompt, str(tmp_path), launch_kind=kind)
        session = owner.sessions[started["session_id"]]
        async with owner._condition:
            await asyncio.wait_for(
                owner._condition.wait_for(lambda: session.result_available or session.result_contract_recovery_pending), 2
            )
        result = await owner.wait()
        if result["status"] == "running":
            async with owner._condition:
                await asyncio.wait_for(owner._condition.wait_for(lambda: session.result_available), 2)
            result = await owner.wait()
        assert result["status"] == "completed" and result["agent_message"] == final
        assert len(client.queries) == 2 and session.result_contract_recovery_consumed
        assert not session.pending_result and not session.result_contract_recovery_reason
        assert publications and all(message == final for message in publications)
    assert client.disconnected and client.readers == 0
