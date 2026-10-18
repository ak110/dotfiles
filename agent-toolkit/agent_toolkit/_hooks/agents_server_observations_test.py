"""agent-toolkit/agent_toolkit/_hooks/agents_server_observations.py のテスト。"""

import contextlib
import io
import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import manager, mcp_tools, shared_layout, status_file
from agent_toolkit._agents_server import state as server_state
from agent_toolkit._common import atomic_file, session_state, state_paths
from agent_toolkit._hooks import agents_server_observations, posttooluse, pretooluse
from agent_toolkit._testing import agents_server_support


class _WindowsAccessDenied(PermissionError):
    """Windowsの`os.replace`が返すWinError 5と同じ識別値を持つ置換拒否。"""

    winerror = 5


class _RegisteredBackend(agents_server_support.FakeBackend):
    """モデル出力を観測済みで、登録簿へ公開するsessionを返す。"""

    async def start(self, *args: Any, **kwargs: Any) -> server_state.SessionState:
        session = await super().start(*args, **kwargs)
        session.model_output_observed = True
        session.publish_registry = True
        return session


def _refuse_replace_into(monkeypatch: pytest.MonkeyPatch, destination: pathlib.Path) -> Callable[..., None]:
    """`atomic_file`が指定先へ置換するときだけ、Windowsで続くアクセス拒否と同じ例外で失敗させ、差し替え前の`os.replace`を返す。"""
    original = os.replace

    def replace(source: Any, target: Any) -> None:
        if pathlib.Path(target) != destination:
            original(source, target)
            return
        error = _WindowsAccessDenied(13, "アクセスが拒否されました", str(source), None, str(target))
        raise error

    monkeypatch.setattr(atomic_file.os, "replace", replace)
    return original


def _hook(hook: Callable[[str], int], payload: dict[str, Any]) -> int:
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        return hook(json.dumps(payload, ensure_ascii=False))


@pytest.mark.asyncio
async def test_public_start_after_projection_failure_reaches_hook_records_and_send_message(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """表示保存に失敗した公開start応答から、PostToolUseが所有・cwd・待機登録を記録し、同じsessionへの追送が通る。"""
    for key in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "observed-root")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("observed-root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    current = manager.AgentsServerManager(writer)
    backend = _RegisteredBackend(current.sessions, "claude")
    agents_server_support.install_backend(current, "claude", backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", current)
    writer.activate()
    writer.path.unlink(missing_ok=True)
    original_replace = _refuse_replace_into(monkeypatch, writer.path)
    tool_name = "mcp__plugin_agent-toolkit_agents_server__start"
    start_input = {"mode": "delegate", "prompt": "処理", "cwd": str(tmp_path), "model_type": "claude:test/medium"}
    try:
        started = await mcp_tools.start(
            mode="delegate", prompt=start_input["prompt"], cwd=str(tmp_path), model_type="claude:test/medium"
        )
        assert started["persistence_warnings"] and not writer.path.exists()
        child = started["session_id"]
        payload = {
            "session_id": "observed-root",
            "tool_name": tool_name,
            "tool_use_id": "start-call",
            "transcript_path": str(tmp_path / "transcript.jsonl"),
            "cwd": str(tmp_path),
            "tool_input": start_input,
        }
        assert _hook(pretooluse.main, payload) == 2
        assert _hook(pretooluse.main, payload) == 0
        assert _hook(posttooluse.main, {**payload, "tool_response": {"structuredContent": started}}) == 0

        recorded = session_state.read_state("observed-root")
        assert agents_server_observations.session_cwd(recorded, child) == str(tmp_path)
        record = agents_server_observations.session_record(recorded, child)
        assert record is not None and record["owner_agent_id"] == "main" and record["pending_observation"] is True
        assert agents_server_observations.pending_session_ids(recorded, "main") == [child]
        assert (shared_layout.wait_targets_directory("observed-root", "root.json", tmp_path) / f"{child}.json").is_file()

        send_input = {"session_id": child, "prompt": "続ける"}
        send_payload = {
            **payload,
            "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
            "tool_use_id": "send-call",
            "tool_input": send_input,
        }
        assert _hook(pretooluse.main, send_payload) == 0
        sent = await mcp_tools.send_message(child, "続ける")
        assert sent["delivery"] == "steered" and backend.send_calls == 1
        assert _hook(posttooluse.main, {**send_payload, "tool_response": {"structuredContent": sent}}) == 0
        after_send = agents_server_observations.session_record(session_state.read_state("observed-root"), child)
        assert after_send is not None and after_send["status"] == "running"
        assert len(backend.start_calls) == 1
    finally:
        monkeypatch.setattr(atomic_file.os, "replace", original_replace)
        await current.close()


def test_kill_observation_attempt_clears_only_the_requested_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """中断の観測試行は入力のsessionだけを解消する。"""
    state = {
        "agents_server_sessions": {
            "remote-a": {"pending_observation": True, "owner_agent_id": "main"},
            "remote-b": {"pending_observation": True, "owner_agent_id": "main"},
        }
    }

    def apply(_session_id: str, mutator: Callable[[dict[str, Any]], object]) -> None:
        mutator(state)

    monkeypatch.setattr(agents_server_observations, "update_state", apply)
    agents_server_observations._record_agents_server_observation_attempt(  # pylint: disable=protected-access
        "local", {"session_id": "remote-a"}, operation="kill"
    )

    assert state["agents_server_sessions"]["remote-a"]["pending_observation"] is False
    assert state["agents_server_sessions"]["remote-b"]["pending_observation"] is True


def test_start_state_record_writes_conversation_root_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """start応答が明示したルート識別子の索引を書く。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    shared_layout.status_directory("root-session", tmp_path).mkdir(parents=True)

    agents_server_observations._record_agents_server_root_alias(  # pylint: disable=protected-access
        "current-session",
        {"root_session_id": "root-session"},
    )

    alias = tmp_path / "agents-server" / "aliases" / "current-session.json"
    assert json.loads(alias.read_text(encoding="utf-8")) == {"version": 1, "root_session_id": "root-session"}


def test_start_state_record_without_shared_status_does_not_write_alias(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """応答が所有rootを明示しない場合は索引を書かない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    agents_server_observations._record_agents_server_root_alias(  # pylint: disable=protected-access
        "current-session",
        {"session_id": "remote-session"},
    )

    assert not (tmp_path / "agents-server" / "aliases" / "current-session.json").exists()


def test_root_alias_uses_explicit_response_without_scanning_other_roots(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """同じ子識別子を持つ別rootがあっても応答の所有rootだけへ対応付ける。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    for root_session_id in ("root-a", "root-b"):
        root = shared_layout.status_directory(root_session_id, tmp_path)
        root.mkdir(parents=True)
        (root / "root.json").write_text(
            json.dumps({"version": 1, "sessions": [{"session_id": "remote-session"}]}),
            encoding="utf-8",
        )

    agents_server_observations._record_agents_server_root_alias(  # pylint: disable=protected-access
        "current-session",
        {"root_session_id": "root-b"},
    )

    alias = tmp_path / "agents-server" / "aliases" / "current-session.json"
    assert json.loads(alias.read_text(encoding="utf-8"))["root_session_id"] == "root-b"


@pytest.mark.parametrize(
    ("operation", "tool_input", "expected"),
    (
        ("start", {"mode": "write", "prompt": "起草する", "cwd": "/tmp/x"}, "write"),
        ("start", {"mode": "shell", "command": "make test", "cwd": "/tmp/x"}, "low_tier"),
        ("start", {"mode": "explore", "prompt": "調べる", "cwd": "/tmp/x"}, "low_tier"),
        ("start", {"mode": "explore", "prompt": "調べる", "cwd": "/tmp/x", "model_type": "medium_tier"}, "medium_tier"),
        ("start", {"mode": "delegate", "prompt": "調べる", "cwd": "/tmp/x", "model_type": "high_tier"}, "high_tier"),
        ("start", {"subagent_md_path": "/plugin/share/exec.subagent.md", "cwd": "/tmp/x"}, "high_tier"),
        ("start", {"model_type": "medium_tier"}, "medium_tier"),
        ("start", {"mode": "unknown", "prompt": "調べる"}, None),
        # 統合前の起動ツール名で記録された呼び出しも、対応するmodeの種別を記録する。
        ("start_write", {"prompt": "起草する", "cwd": "/tmp/x"}, "write"),
        ("start_shell", {"command": "make test", "cwd": "/tmp/x"}, "low_tier"),
        ("start_explore", {"prompt": "調べる", "cwd": "/tmp/x"}, "low_tier"),
        ("start_custom", {"prompt": "調べる", "cwd": "/tmp/x"}, None),
    ),
)
def test_agents_server_model_type_matches_server_defaults(operation: str, tool_input: dict, expected: str | None) -> None:
    """記録する工程種別は、サーバーが各modeの省略時に使う種別と一致する。

    writeで種別を省略すると`write`を使うため、shellと同じ`low_tier`を記録すると工程を別の種別に加算してしまう。
    """
    model_type = agents_server_observations._agents_server_model_type(tool_input, operation)  # pylint: disable=protected-access
    assert model_type == expected


@pytest.mark.asyncio
async def test_public_handoff_start_registers_successor_for_wait_and_followup(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """前任IDだけの公開start応答から後任のcwdと待機対象を登録し、後任への追送を通す。"""
    for key in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "CODEX_THREAD_ID"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "handoff-root")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    writer = status_file.StatusFileWriter(
        {},
        status_file.StatusFileIdentity("handoff-root", "root.json", None),
        state_root=tmp_path,
        aggregate_seconds=0,
    )
    current = manager.AgentsServerManager(writer)
    backend = _RegisteredBackend(current.sessions, "claude")
    agents_server_support.install_backend(current, "claude", backend)
    monkeypatch.setattr(mcp_tools, "_MANAGER", current)
    writer.activate()
    try:
        started = await mcp_tools.start(
            str(tmp_path),
            mode="delegate",
            model_type="claude:test/medium",
            prompt="引き継ぐ作業",
        )
        predecessor = current.sessions[started["session_id"]]
        agents_server_support._complete(predecessor, message="確定事項と残工程")  # pylint: disable=protected-access
        record = tmp_path / "claude" / "projects" / "trial" / f"{predecessor.session_id}.jsonl"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "元の依頼"}}), encoding="utf-8")
        response = await mcp_tools.start(handoff_from=predecessor.session_id)
        successor = response["session_id"]
        assert successor != predecessor.session_id
        payload: dict[str, Any] = {
            "session_id": "handoff-root",
            "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
            "tool_input": {"handoff_from": predecessor.session_id},
            "tool_response": {"structuredContent": response},
        }
        warnings = agents_server_observations.observe_tool(
            payload,
            "handoff-root",
            payload["tool_name"],
            payload["tool_input"],
            moved_to_background=False,
        )
        assert not warnings
        saved = session_state.read_state("handoff-root")
        assert agents_server_observations.session_cwd(saved, successor) == str(tmp_path)
        assert agents_server_observations.pending_session_ids(saved, "main") == [successor]
        saved_session = agents_server_observations.session_record(saved, successor)
        assert saved_session is not None and saved_session["model_type"] == "claude:test/medium"
        marker = shared_layout.wait_targets_directory("handoff-root", "root.json", tmp_path) / f"{successor}.json"
        assert marker.is_file()
        send = {
            **payload,
            "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
            "tool_input": {"session_id": successor, "prompt": "残工程を続ける"},
        }
        assert _hook(pretooluse.main, send) == 2
        assert _hook(pretooluse.main, send) == 0
        assert (await mcp_tools.send_message(successor, "残工程を続ける"))["delivery"] == "steered"
        assert backend.send_calls == 1
    finally:
        await current.close()


@pytest.mark.parametrize("handoff", [False, True])
def test_response_cwd_is_accepted_only_for_handoff_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    handoff: bool,
) -> None:
    """通常起動は入力cwdの欠落を診断し、引き継ぎ起動だけ応答cwdを検収する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "observed-root")
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    shared_layout.status_directory("observed-root", tmp_path).mkdir(parents=True)
    tool = "mcp__plugin_agent-toolkit_agents_server__start"
    inputs = {"handoff_from": "predecessor"} if handoff else {"mode": "delegate"}
    payload = {
        "tool_input": inputs,
        "tool_response": {
            "session_id": "successor",
            "status": "running",
            "root_session_id": "observed-root",
            "cwd": str(tmp_path),
        },
    }
    warnings = agents_server_observations.observe_tool(
        payload,
        "observed-root",
        tool,
        inputs,
        moved_to_background=False,
    )
    assert any("cwd" in warning.body for warning in warnings) is not handoff
