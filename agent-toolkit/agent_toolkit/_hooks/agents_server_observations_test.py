"""agent-toolkit/agent_toolkit/_hooks/agents_server_observations.py のテスト。"""

import contextlib
import copy
import io
import json
import os
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import manager, mcp_tools, shared_layout, status_file
from agent_toolkit._agents_server import state as server_state
from agent_toolkit._common import atomic_file, session_registry, session_state, state_paths
from agent_toolkit._hooks import agents_server_observations, agents_server_session_advisor, posttooluse, pretooluse
from agent_toolkit._hooks.pretooluse import agent_checks
from agent_toolkit._testing import agents_server_support

pytestmark = pytest.mark.usefixtures("_forbid_backend_process")


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


def _acquire_delegation(payload: dict[str, Any]) -> None:
    """委譲操作の前に求められる`agent-toolkit:delegation`の取得を、Skill起動のPostToolUseとして観測させる。"""
    skill = {"session_id": payload["session_id"], "tool_name": "Skill", "tool_input": {"skill": "agent-toolkit:delegation"}}
    assert _hook(posttooluse.main, {"hook_event_name": "PostToolUse", **skill}) == 0


@pytest.mark.asyncio
async def test_projection_probe_preparation_failure_closes_manager(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """投影失敗の準備で例外が起きても、生成済みmanagerの解放を完了する。"""
    acquired = []
    closed = []

    class TrackedManager(manager.AgentsServerManager):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            acquired.append(self)

        async def close(self) -> None:
            await super().close()
            closed.append(self)

    def fail(*_args: Any, **_kwargs: Any) -> Callable[..., None]:
        raise RuntimeError("投影試験の準備失敗")

    monkeypatch.setattr(manager, "AgentsServerManager", TrackedManager)
    monkeypatch.setitem(globals(), "_refuse_replace_into", fail)
    with pytest.raises(RuntimeError, match="投影試験の準備失敗"):
        await test_public_start_after_projection_failure_reaches_hook_records_and_send_message(tmp_path, monkeypatch)
    assert acquired and closed == acquired


@pytest.mark.asyncio
async def test_public_start_after_projection_failure_reaches_hook_records_and_send_message(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """表示保存に失敗した公開start応答から、PostToolUseが所有・cwd・待機登録を記録し、同じsessionへの追送が受理される。"""
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
    async with manager.AgentsServerManager(writer) as current:
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
            assert _hook(pretooluse.main, payload) == 0
            _acquire_delegation(payload)
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
        ("start", {"mode": "shell", "command": "make test", "cwd": "/tmp/x"}, "chores_fast"),
        ("start", {"mode": "explore", "prompt": "調べる", "cwd": "/tmp/x"}, "chores_fast"),
        ("start", {"mode": "explore", "prompt": "調べる", "cwd": "/tmp/x", "model_type": "review"}, "review"),
        ("start", {"mode": "delegate", "prompt": "調べる", "cwd": "/tmp/x", "model_type": "execute"}, "execute"),
        ("start", {"subagent_md_path": "/plugin/share/exec.subagent.md", "cwd": "/tmp/x"}, "execute"),
        ("start", {"model_type": "review"}, "review"),
        ("start", {"mode": "unknown", "prompt": "調べる"}, None),
        # 統合前の起動ツール名で記録された呼び出しも、対応するmodeの種別を記録する。
        ("start_write", {"prompt": "起草する", "cwd": "/tmp/x"}, "write"),
        ("start_shell", {"command": "make test", "cwd": "/tmp/x"}, "chores_fast"),
        ("start_explore", {"prompt": "調べる", "cwd": "/tmp/x"}, "chores_fast"),
        ("start_custom", {"prompt": "調べる", "cwd": "/tmp/x"}, None),
    ),
)
def test_agents_server_model_type_matches_server_defaults(operation: str, tool_input: dict, expected: str | None) -> None:
    """記録する工程種別は、サーバーが各modeの省略時に使う種別と一致する。

    writeで種別を省略すると`write`を使うため、shellと同じ`chores_fast`を記録すると工程を別の種別に加算してしまう。
    """
    model_type = agents_server_observations._agents_server_model_type(tool_input, operation)  # pylint: disable=protected-access
    assert model_type == expected


def test_new_role_model_type_comes_from_same_declaration_as_server(tmp_path: pathlib.Path) -> None:
    """役割名対応表の無い定義でも、起動時と観測時の段位が一致する。"""
    from agent_toolkit._agents_server import launch_requests  # pylint: disable=import-outside-toplevel

    manifest = tmp_path / ".claude-plugin/plugin.json"
    manifest.parent.mkdir()
    manifest.write_text('{"name":"agent-toolkit"}', encoding="utf-8")
    document = tmp_path / "share/new-check.subagent.md"
    document.parent.mkdir()
    document.write_text(
        "---\nmode: explore\nmodel_type: chores_fast\nusage: chores\n"
        "required_inputs: []\nwrites_worktree: false\n---\n# 読取確認\n",
        encoding="utf-8",
    )
    model_type, _prompt, _mode, _handoff = launch_requests.task_document_request(str(document), {})
    inputs = {"subagent_md_path": str(document)}
    assert agents_server_observations._agents_server_model_type(inputs, "start") == model_type  # pylint: disable=protected-access
    assert (
        agents_server_observations._agents_server_model_type(  # pylint: disable=protected-access
            {**inputs, "model_type": "execute"}, "start"
        )
        == "execute"
    )


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
    async with manager.AgentsServerManager(writer) as current:
        backend = _RegisteredBackend(current.sessions, "claude")
        agents_server_support.install_backend(current, "claude", backend)
        monkeypatch.setattr(mcp_tools, "_MANAGER", current)
        writer.activate()
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
        assert _hook(pretooluse.main, send) == 0
        _acquire_delegation(send)
        assert _hook(pretooluse.main, send) == 0
        assert (await mcp_tools.send_message(successor, "残工程を続ける"))["delivery"] == "steered"
        assert backend.send_calls == 1


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


@pytest.mark.parametrize("namespace", ["mcp__agents_server__", "mcp__plugin_agent-toolkit_agents_server__"])
@pytest.mark.parametrize("operation", ["send_message", "kill"])
@pytest.mark.parametrize("actor", ["main", "agent-a"])
def test_owned_show_recovers_missing_cwd(tmp_path, monkeypatch, namespace, operation, actor):
    """公開PostToolUseのshowでcwdを回復し、同じ子への追送と中断を許可する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_registry.publish("owned-child", terminal=False, engine="claude", cwd=str(tmp_path), launcher_session_id="local")
    payload = {"session_id": "local", "agent_id": actor, "cwd": str(tmp_path)}
    start_input = {"mode": "delegate", "cwd": str(tmp_path)}
    assert (
        _hook(
            posttooluse.main,
            {
                **payload,
                "tool_name": namespace + "start",
                "tool_input": start_input,
                "tool_response": {"session_id": "owned-child", "status": "running", "root_session_id": "local"},
            },
        )
        == 0
    )
    # 追送により観測責任者が変わっても、起動時主体で回復する。
    assert (
        _hook(
            posttooluse.main,
            {
                **payload,
                "agent_id": "other-observer",
                "tool_name": namespace + "send_message",
                "tool_input": {"session_id": "owned-child", "prompt": "続行する"},
                "tool_response": {"delivery": "reply_started"},
            },
        )
        == 0
    )
    session_state.update_state("local", lambda state: {**state, "agents_server_cwd_by_session": {}})
    inputs = {"session_id": "owned-child", "prompt": "続行する"}
    before = agent_checks._check_agents_server_continuation_target("local", inputs, namespace + operation)  # pylint: disable=protected-access
    assert before.block is not None
    assert (
        _hook(
            posttooluse.main,
            {
                **payload,
                "tool_name": namespace + "show",
                "tool_input": {"session_id": "owned-child"},
                "tool_response": {
                    "structuredContent": {
                        "session_id": "owned-child",
                        "status": "failed",
                        "cwd": str(tmp_path),
                        "error": {"message": "失敗した"},
                    }
                },
            },
        )
        == 0
    )
    saved = session_state.read_state("local")
    assert agents_server_observations.session_cwd(saved, "owned-child") == str(tmp_path)
    assert agents_server_observations.has_pending_owned_observation(saved, "other-observer")
    record = agents_server_observations.session_record(saved, "owned-child")
    assert record is not None and record["launcher_agent_id"] == actor
    after = agent_checks._check_agents_server_continuation_target("local", inputs, namespace + operation)  # pylint: disable=protected-access
    assert after.allow and after.block is None


@pytest.mark.parametrize("failure", ["foreign", "unknown", "mismatched", "relative", "empty", "other-actor", "missing-actor"])
def test_foreign_show_does_not_recover_cwd(tmp_path, monkeypatch, failure):
    """所有不明、応答不一致、不正cwdと別主体のshowでは操作権限を回復しない。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    if failure != "unknown":
        session_registry.publish(
            "child",
            terminal=False,
            engine="claude",
            cwd=str(tmp_path),
            launcher_session_id="foreign" if failure == "foreign" else "local",
        )
    record = {"session_id": "child", "status": "running", "owner_agent_id": "main", "pending_observation": True}
    if failure != "missing-actor":
        record["launcher_agent_id"] = "agent-a" if failure == "other-actor" else "main"
    session_state.update_state("local", lambda _state: {"agents_server_sessions": {"child": record}})
    response = {
        "session_id": "other" if failure == "mismatched" else "child",
        "status": "running",
        "cwd": "relative" if failure == "relative" else "" if failure == "empty" else str(tmp_path),
        "root_session_id": "local",
        "live_child_sessions": [{"session_id": "child", "cwd": str(tmp_path)}],
    }
    assert (
        _hook(
            posttooluse.main,
            {
                "session_id": "local",
                "tool_name": "mcp__agents_server__show",
                "tool_input": {"session_id": "child"},
                "tool_response": response,
            },
        )
        == 0
    )
    assert agents_server_observations.session_cwd(session_state.read_state("local"), "child") is None
    for operation in ("send_message", "kill"):
        result = agent_checks._check_agents_server_continuation_target(  # pylint: disable=protected-access
            "local", {"session_id": "child"}, "mcp__agents_server__" + operation
        )
        assert result.block is not None


@pytest.mark.parametrize(
    "tool_name", sorted(agents_server_observations.AGENTS_SERVER_HOOK_TOOL_NAMES | {"mcp__agents_server__wait"})
)
@pytest.mark.parametrize("shape", ["isError", "json", "ToolError", "content"])
def test_error_response_preserves_observation_state(tmp_path, monkeypatch, tool_name, shape):
    """全観測操作の失敗は状態、別名、待機登録とStopの未観測警告を保持する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    shared_layout.status_directory("wrong-root", tmp_path).mkdir(parents=True)
    initial = {
        "agents_server_sessions": {
            "child": {
                "session_id": "child",
                "status": "running",
                "owner_agent_id": "main",
                "pending_observation": True,
            }
        },
        "agents_server_cwd_by_session": {"child": str(tmp_path)},
    }
    session_state.update_state("local", lambda _state: copy.deepcopy(initial))
    response = {
        "isError": True,
        "structuredContent": {
            "session_id": "child",
            "status": "interrupted",
            "root_session_id": "wrong-root",
            "delivery": "reply_started",
            "live_child_sessions": [{"session_id": "other", "cwd": str(tmp_path)}],
        },
    }
    if shape == "json":
        response = json.dumps(response)
    elif shape == "ToolError":
        response = "ToolError: 実行中のsessionは破棄できない。次の操作: showで確認する"
    elif shape == "content":
        # mcp_transport._call_registered_toolのToolError応答で、ホストが本文だけを渡す場合。
        response = [{"type": "text", "text": "実行中のsessionは破棄できない。次の操作: showで確認する"}]
    before_files = {
        str(path.relative_to(tmp_path)): path.read_bytes() for path in (tmp_path / "agents-server").rglob("*") if path.is_file()
    }
    assert (
        _hook(
            posttooluse.main,
            {
                "session_id": "local",
                "tool_name": tool_name,
                "tool_input": {"session_id": "child", "cwd": str(tmp_path)},
                "tool_response": response,
            },
        )
        == 0
    )
    saved = session_state.read_state("local")
    assert {key: saved[key] for key in initial} == initial
    after_files = {
        str(path.relative_to(tmp_path)): path.read_bytes() for path in (tmp_path / "agents-server").rglob("*") if path.is_file()
    }
    assert after_files == before_files
    result, message = agents_server_session_advisor.evaluate(json.dumps({"session_id": "local"}))
    assert result == "notify" and "child" in message


def test_terminal_session_error_is_not_tool_failure(tmp_path, monkeypatch):
    """正常killが返す終了session自身のerrorは状態へ反映する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    session_state.update_state(
        "local",
        lambda _state: {
            "agents_server_sessions": {
                "child": {
                    "session_id": "child",
                    "status": "running",
                    "owner_agent_id": "main",
                    "pending_observation": True,
                }
            }
        },
    )
    assert (
        _hook(
            posttooluse.main,
            {
                "session_id": "local",
                "tool_name": "mcp__agents_server__kill",
                "tool_input": {"session_id": "child"},
                "tool_response": {"session_id": "child", "status": "interrupted", "error": {"message": "中断した"}},
            },
        )
        == 0
    )
    record = agents_server_observations.session_record(session_state.read_state("local"), "child")
    assert record is not None and record["error"] == {"message": "中断した"}
    assert record["pending_observation"] is False
