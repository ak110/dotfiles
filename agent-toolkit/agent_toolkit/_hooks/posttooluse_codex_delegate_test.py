# pylint: disable=protected-access
"""posttooluse_codex_delegateの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout, shared_roots, wait_targets
from agent_toolkit._common import session_state, state_paths
from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _load_posttooluse_module, _run, _run_codex_delegate_hook, _state_path

_POSTTOOLUSE_MODULE = _load_posttooluse_module()


@pytest.mark.parametrize(
    ("operation", "tool_input", "structured"),
    [
        ("start", {"prompt": "委譲する"}, {"session_id": "remote-session", "status": "running"}),
        ("send_message", {"session_id": "remote-session", "prompt": "再開する"}, {"delivery": "reply_started"}),
    ],
)
def test_codex_delegate_hook_session_registers_wait_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    tool_input: dict[str, object],
    structured: dict[str, object],
) -> None:
    """Codex委譲先はhook入力のthreadから内側MCPの書込主体へ待機対象を登録する。"""
    shared_roots.write_host_alias("root-session", "writer-session", "codex-thread", tmp_path)
    exit_code = _run_codex_delegate_hook(monkeypatch, tmp_path, operation, tool_input, structured)

    retained, error = wait_targets.read_wait_targets(
        "root-session",
        "writer-session.json",
        tmp_path,
    )
    assert (exit_code, retained, error) == (0, {"remote-session"}, None)
    root_targets = shared_layout.wait_targets_directory(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert not root_targets.exists()


@pytest.mark.parametrize(
    ("operation", "tool_input", "structured"),
    [
        ("start", {"prompt": "委譲する"}, {"session_id": "remote-session", "status": "running"}),
        ("send_message", {"session_id": "remote-session", "prompt": "再開する"}, {"delivery": "reply_started"}),
    ],
)
def test_codex_delegate_hook_reports_ambiguous_writer_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    tool_input: dict[str, object],
    structured: dict[str, object],
) -> None:
    """曖昧な書込主体索引を診断し、誤った待機対象を登録せず正常終了する。"""
    for writer in ("writer-a", "writer-b"):
        shared_roots.write_host_alias("root-session", writer, "codex-thread", tmp_path)
    exit_code = _run_codex_delegate_hook(monkeypatch, tmp_path, operation, tool_input, structured)

    output = json.loads(capsys.readouterr().out)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert exit_code == 0
    assert "agents_serverの待機対象を登録できない" in context
    assert "書込主体を一意に解決できません" in context
    assert "\n次の操作: `atk agents wait`はこのセッションを待機対象として扱わないため" in context
    assert "`show`" in context
    for owner_status_file in ("writer-a.json", "writer-b.json", "codex-thread.json"):
        retained, error = wait_targets.read_wait_targets("root-session", owner_status_file, tmp_path)
        assert retained == set()
        assert error is None


def test_codex_delegate_hook_without_host_alias_uses_hook_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """Codexによる起動で書込主体索引が無い場合は検証済みhook sessionで待機対象を登録する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "codex-thread",
                "cwd": str(tmp_path),
                "tool_name": "mcp__agents_server__start",
                "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "remote-session", "status": "running"}},
            }
        )
    )

    retained, error = wait_targets.read_wait_targets("root-session", "codex-thread.json", tmp_path)
    assert (exit_code, retained, error) == (0, {"remote-session"}, None)


@pytest.mark.parametrize(
    ("owner_session", "tool_name"),
    [
        (None, "mcp__agents_server__start"),
        ("root-session", "mcp__plugin_agent-toolkit_agents_server__start"),
    ],
)
def test_start_without_caller_identity_does_not_register_wait_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    owner_session: str | None,
    tool_name: str,
) -> None:
    """呼出主体またはCodexによる起動を解決できない開始応答は待機対象登録簿へ書き込まない。"""
    for name in (
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_STATUS_HOST_SESSION",
        "CLAUDE_CODE_SESSION_ID",
        "CODEX_THREAD_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    if owner_session is not None:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", owner_session)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "hook-session",
                "cwd": str(tmp_path),
                "tool_name": tool_name,
                "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "remote-session", "status": "running"}},
            }
        )
    )

    assert exit_code == 0
    assert not (tmp_path / "agents-server").exists()


def test_wait_collects_posttooluse_registered_result_and_releases_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """開始直後の待機はPostToolUseの登録から終了時の結果を回収し、登録を解除する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    payload = {
        "session_id": "root-session",
        "cwd": str(tmp_path),
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
        "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
        "tool_response": {
            "structuredContent": {
                "session_id": "remote-session",
                "status": "running",
                "root_session_id": "root-session",
            }
        },
    }
    assert _POSTTOOLUSE_MODULE.main(json.dumps(payload)) == 0
    results = shared_layout.results_directory("root-session", tmp_path)
    results.mkdir(parents=True)
    (results / "remote-session.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")

    exit_code = agents_wait.wait_for_result(
        environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
        state_root=tmp_path,
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"status": "completed", "session_id": "remote-session"}
    retained, error = wait_targets.read_wait_targets(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert retained == set()
    assert error is None


@pytest.mark.parametrize(
    ("tool_name", "command"),
    [
        ("Bash", "C:/Users/x/dotfiles/agent-toolkit/scripts/atk.cmd agents wait"),
        ("Bash", r"'C:\Users\x\dotfiles\agent-toolkit\scripts\atk.cmd' agents wait"),
        ("PowerShell", r"& 'C:\Users\x\dotfiles\agent-toolkit\scripts\atk.cmd' agents wait"),
    ],
)
def test_windows_atk_cmd_wait_clears_pending_observation(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, tool_name: str, command: str
) -> None:
    """Windows向け起動形の回収後はStopの未観測警告を解消する。"""
    from agent_toolkit._hooks import agents_server_session_advisor  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    sid, child = "windows-wait-parent", "windows-wait-child"
    path = _state_path(sid)
    path.write_text(
        json.dumps(
            {
                "agents_server_sessions": {
                    child: {
                        "pending_observation": True,
                        "owner_agent_id": "main",
                        "status": "completed",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    result = _run(
        {
            "session_id": sid,
            "tool_name": tool_name,
            "tool_input": {"command": command},
            "tool_response": {"stdout": "結果回収済み"},
        },
        state_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert _read_state(tmp_path, sid)["agents_server_sessions"][child]["pending_observation"] is False
    monkeypatch.setattr(session_state, "state_path", lambda _sid: path)
    assert agents_server_session_advisor.evaluate(json.dumps({"session_id": sid}))[0] == "approve"
