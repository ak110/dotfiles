"""引き継ぎ起動が使う記録と、終端結果の候補除外・案内の契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from agent_toolkit._agents_server import launch_requests, mcp_tools, record_paths, resume_waits, session_handoff, state
from agent_toolkit._atk import config
from agent_toolkit._common import session_registry
from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._testing.agents_server_support import (
    FakeBackend,
    _complete,
    _install_backend,
    _manager_with_fake,
    _observed_input_params,
)

pytestmark = pytest.mark.usefixtures("agents_server_isolation")


@pytest.fixture(name="record_extractor")
def _record_extractor(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> pathlib.Path:
    """外部ホストの記録と会話抽出CLIを、専用の入力・出力で代用する。"""
    record = tmp_path / "record.jsonl"
    record.write_text("記録本文", encoding="utf-8")

    def find_record(identifier: str, **_kwargs: Any) -> record_paths.SessionRecord:
        return record_paths.SessionRecord(identifier.split("-", 1)[0], (record,))

    def extract(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--work-dir" in command:
            directory = pathlib.Path(command[command.index("--work-dir") + 1])
            (directory / "conversation.md").write_text("完了した操作と残工程の会話", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "会話本文", "")

    monkeypatch.setattr(record_paths, "find_session_record", find_record)
    monkeypatch.setattr(session_handoff.process_tree, "run_with_timeout", extract)
    return record


@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
@pytest.mark.asyncio
async def test_start_handoff_restores_route_and_excludes_predecessor_after_record_expiry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    record_extractor: pathlib.Path,
    launch_kind: state.LaunchKind,
) -> None:
    """公開start1回から、期限切れの除外記録に依存せず後続候補へ条件と全文を渡す。"""
    del record_extractor
    requested: list[tuple[str, str | None]] = []

    def candidates(model_type: str, *, usage: str | None = None, **_kwargs: Any) -> list[state.ModelCandidate]:
        requested.append((model_type, usage))
        return [("codex", "first", "medium"), ("claude", "second", "medium")]

    monkeypatch.setattr(config, "parse_unresolved_model_candidates", candidates)
    manager, first = _manager_with_fake("codex")
    second = FakeBackend(manager.sessions, "claude")
    _install_backend(manager, "claude", second)
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    try:
        started = await manager.start(
            "high_tier",
            "元の作業",
            str(tmp_path),
            launch_kind=launch_kind,
            usage="review",
            label="担当",
        )
        predecessor = manager.sessions[started["session_id"]]
        _complete(predecessor, message="第1turnの最終応答")
        await manager.send_message(predecessor.session_id, "追送全文")
        _complete(predecessor, message="中断までの進捗", error={"codexErrorInfo": "usageLimitExceeded"})
        # 候補除外記録のない状態と、結果回収後を同時に用意する。
        predecessor.result_delivered = True
        response = await mcp_tools.start(handoff_from=predecessor.session_id)
        successor = manager.sessions[response["session_id"]]
        assert response["session_id"] != predecessor.session_id
        assert response["cwd"] == str(tmp_path)
        assert response["label"] == "担当"
        assert successor.engine == "claude"
        assert successor.launch_kind == launch_kind
        assert successor.usage == "review"
        assert requested == [("high_tier", "review")] * 2
        assert len(first.start_calls) == 1
        assert len(second.start_calls) == 1
        assert "追送全文" in successor.prompt
        assert "第1turnの最終応答" in successor.prompt
        assert "中断までの進捗" in successor.prompt
        assert ("codex", "first", "medium") in successor.excluded_candidates
    finally:
        await manager.close()


@pytest.mark.parametrize("extra", [{"cwd": "/repo"}, {"mode": "task"}, {"prompt": "変更"}, {"label": "別名"}])
@pytest.mark.asyncio
async def test_public_start_rejects_mixed_handoff_inputs(extra: dict[str, str]) -> None:
    """前任の入力を暗黙に上書きする混在は、MCP境界で修正可能な診断を返す。"""
    with pytest.raises(ToolError, match="handoff_from"):
        await mcp_tools.mcp.call_tool("start", {"handoff_from": "codex-session", **extra})


@pytest.mark.asyncio
async def test_start_handoff_rejects_running_predecessor_before_extracting_records(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """実行中の前任へは終端待機・killを案内し、後任を生成しない。"""
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("codex", "first", "medium")])
    manager, backend = _manager_with_fake("codex")
    try:
        started = await manager.start("high_tier", "作業", str(tmp_path))
        with pytest.raises(ActionableError, match="前任は実行中") as raised:
            await manager.start_handoff(started["session_id"])
        assert "kill" in raised.value.next_action
        assert "atk agents wait" in raised.value.next_action
        assert len(backend.start_calls) == 1
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_task_handoff_keeps_delivered_role_prompt_and_continues_handoff_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    record_extractor: pathlib.Path,
) -> None:
    """taskで配送した役割本文・名前付き入力・用途と引き継ぎ記録の継続を後任へ渡す。"""
    del record_extractor
    monkeypatch.setattr(config, "parse_unresolved_model_candidates", lambda *_args, **_kwargs: [("codex", "first", "medium")])
    manager, _ = _manager_with_fake("codex")
    monkeypatch.setattr(mcp_tools, "_MANAGER", manager)
    params = _observed_input_params("exec.subagent.md", tmp_path)
    try:
        started = await mcp_tools.start(
            str(tmp_path),
            subagent_md_path="exec",
            extra_params=params,
            model_type="high_tier",
        )
        predecessor = manager.sessions[started["session_id"]]
        _complete(predecessor, message="計画に基づく進捗全文")
        response = await mcp_tools.start(handoff_from=predecessor.session_id)
        successor = manager.sessions[response["session_id"]]
        document = launch_requests._SHARE_DIRECTORY / "exec.subagent.md"  # pylint: disable=protected-access
        assert str(document) in successor.prompt
        assert predecessor.prompt in successor.prompt
        assert successor.usage == predecessor.usage == "execute"
        assert successor.launch_kind == predecessor.launch_kind
        assert successor.label == predecessor.label
        assert "（継続）" in successor.prompt
        assert "計画に基づく進捗全文" in successor.prompt
    finally:
        await manager.close()


@pytest.mark.parametrize("engine", ["codex", "claude", "agy"])
@pytest.mark.parametrize("launch_kind", ["delegate", "explore", "write", "shell"])
def test_prompt_delivers_full_history_and_record_paths(
    tmp_path: pathlib.Path,
    engine: str,
    launch_kind: state.LaunchKind,
) -> None:
    """全engine・modeに、長い追送・全turnの最終応答・経過と詳細照会を全文で届ける。"""
    handoff = tmp_path / "handoff.md"
    predecessor = state.SessionState(
        "predecessor",
        str(tmp_path),
        engine=engine,
        launch_kind=launch_kind,
        prompt=f"元の起動本文\n引き継ぎ記録先: {handoff}（新規）",
        model_type="high_tier",
        status="failed",
        error={"message": "終端理由"},
        turn_seq=2,
        followup_prompts=("追送全文" * 2000,),
        final_responses=((1, "第1turn全文" * 2000),),
        agent_message="第2turn全文" * 2000,
    )
    conversation = tmp_path / "conversation.md"
    records = (tmp_path / "record.jsonl",)
    prompt = session_handoff.compose_prompt(predecessor, conversation, records)
    for expected in (
        predecessor.prompt,
        f"引き継ぎ記録先: {handoff}（継続）",
        str(conversation),
        str(records[0]),
        predecessor.followup_prompts[0],
        predecessor.final_responses[0][1],
        predecessor.agent_message,
        "前任へ配送した原文",
        "引き継ぎ節を現在の開始条件として扱い",
        "既存の記録を読む",
        "会話の流れ・追送・各turnの最終応答を読み",
        "その記述とも比べ",
        "現在の確定事項と残工程を復元する",
        "完了済みの操作と作成済みの成果物を現物で確かめ",
        "最新の指示で中止された範囲",
        "既に完了・続行済みの範囲は再実行せず",
        "残る認可内の工程だけを続ける",
    ):
        assert expected in prompt
    if engine != "agy":
        assert f"--{'codex-thread' if engine == 'codex' else 'claude-session'}-id predecessor --detail" in prompt


@pytest.mark.asyncio
async def test_turn_history_survives_collection_release_and_registry_restart(tmp_path: pathlib.Path) -> None:
    """回収・解放後も登録簿の同じ7日保持内で追送と応答と終端理由を引き継げる。"""
    predecessor = state.SessionState(
        "predecessor",
        str(tmp_path),
        engine="codex",
        model="model",
        effort="high",
        model_type="high_tier",
        prompt="元の起動本文",
        usage="review",
        followup_prompts=("追送全文",),
        turn_seq=1,
    )
    predecessor.status = "completed"
    predecessor.turn_completed = True
    predecessor.agent_message = "第1turnの応答全文"
    resume_waits.begin_auto_resume_wait(
        predecessor,
        {
            "status": "completed",
            "agent_message": predecessor.agent_message,
            "error": None,
        },
    )
    predecessor.touch()
    state.begin_reply(predecessor)
    predecessor.status = "failed"
    predecessor.turn_completed = True
    predecessor.agent_message = "第2turnの応答全文"
    predecessor.error = {"codexErrorInfo": "usageLimitExceeded"}
    predecessor.touch()
    session_registry.publish(
        predecessor.session_id,
        terminal=True,
        engine=predecessor.engine,
        cwd=predecessor.cwd,
        model=predecessor.model,
        effort=predecessor.effort,
        model_type=predecessor.model_type,
        turn_seq=predecessor.turn_seq,
        status="failed",
        launch_info=session_registry.LaunchInfo.of(predecessor),
        state_root=tmp_path,
    )
    session_registry.release(predecessor.session_id, reason="stopped", state_root=tmp_path)
    resolution = session_registry.resolve(predecessor.session_id, state_root=tmp_path)
    assert resolution.state is session_registry.Resolution.RELEASED
    assert resolution.resume_info is None
    restored = resolution.handoff_info
    assert restored is not None
    assert restored.launch_info == session_registry.LaunchInfo.of(predecessor)
    assert restored.launch_info.final_responses == ((1, "第1turnの応答全文"), (2, "第2turnの応答全文"))
    assert json.loads(restored.launch_info.terminal_error or "null") == predecessor.error


def test_legacy_registry_optional_history_keeps_default_values() -> None:
    """追加項目の無い旧登録簿と不正な任意値は、従来の起動情報を保つ。"""
    restored = session_registry.LaunchInfo.from_record({"label": "old", "prompt": "元の依頼", "followup_prompts": 3})
    assert restored.label == "old"
    assert restored.prompt == "元の依頼"
    assert restored.usage is None
    assert not restored.followup_prompts
    assert not restored.final_responses


@pytest.mark.parametrize("engine", ["codex", "claude"])
def test_handoff_prepares_conversation_through_registered_script(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    engine: str,
) -> None:
    """実際の記録解決と登録済み抽出CLIから、後任がReadできる会話ファイルを生成する。"""
    identifier = "019900aa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    # session_review_prepare_test.pyの既存Claude transcriptとCodex rolloutの記録形を使う。
    if engine == "claude":
        root = tmp_path / "claude"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(root))
        record = root / "projects" / "repo" / f"{identifier}.jsonl"
        entries = [{"type": "user", "message": {"role": "user", "content": "確定事項と残工程"}}]
    else:
        root = tmp_path / "codex"
        monkeypatch.setenv("CODEX_HOME", str(root))
        record = root / "sessions" / "2026" / "09" / "06" / f"rollout-test-{identifier}.jsonl"
        entries = [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "確定事項と残工程"}],
                },
            }
        ]
    record.parent.mkdir(parents=True)
    record.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    predecessor = state.SessionState(
        identifier,
        str(tmp_path),
        engine=engine,
        model_type="high_tier",
        status="interrupted",
        prompt="元の依頼",
    )
    prompt = session_handoff.prepare_prompt(predecessor)
    conversation_line = next(line for line in prompt.splitlines() if line.startswith("会話の流れ: "))
    conversation = pathlib.Path(conversation_line.removeprefix("会話の流れ: "))
    assert "確定事項と残工程" in conversation.read_text(encoding="utf-8")
    assert str(record) in prompt
