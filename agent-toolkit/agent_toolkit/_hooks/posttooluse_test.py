# pylint: disable=protected-access
"""posttooluseの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._agents_server import agents_wait, shared_layout, wait_targets
from agent_toolkit._agents_server import tool_names as _agents_server_tool_names
from agent_toolkit._common import state_paths
from agent_toolkit._testing.posttooluse_support import _isolate_conversation, _load_posttooluse_module, _write_root_status

_POSTTOOLUSE_MODULE = _load_posttooluse_module()


def test_list_response_writes_conversation_root_alias(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """空のlist応答でも明示された所有rootを現行会話へ対応付ける。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    shared_layout.status_directory("root-session", tmp_path).mkdir(parents=True)
    payload = {
        "session_id": "current-session",
        "cwd": str(tmp_path),
        "tool_name": "mcp__plugin_agent-toolkit_agents_server__list",
        "tool_input": {},
        "tool_response": {"structuredContent": {"sessions": [], "omitted": 0, "root_session_id": "root-session"}},
    }

    assert _POSTTOOLUSE_MODULE.main(json.dumps(payload)) == 0

    alias = tmp_path / "agents-server" / "aliases" / "current-session.json"
    assert json.loads(alias.read_text(encoding="utf-8")) == {"version": 1, "root_session_id": "root-session"}


@pytest.mark.parametrize(
    ("operation", "structured"),
    [
        ("start", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        # 統合前の起動ツール名を公開する旧版のサーバーの応答も同じく扱う。
        ("start_custom", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_explore", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_write", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("start_shell", {"session_id": "remote-session", "turn_id": "turn-1", "status": "running"}),
        ("send_message", {"delivery": "reply_started"}),
        ("send_message", {"delivery": "reply_ambiguous"}),
    ],
)
def test_start_and_reply_register_wait_target_for_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    structured: dict[str, object],
) -> None:
    """開始または再開したsessionを、PostToolUseの呼出主体の待機対象へ登録する。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    tool_input: dict[str, object] = {"session_id": "remote-session"}
    if operation in _agents_server_tool_names.RECORDED_START_OPERATIONS:
        tool_input = {"prompt": "委譲する", "cwd": str(tmp_path)}

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "root-session",
                "cwd": str(tmp_path),
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": tool_input,
                "tool_response": {"structuredContent": structured},
            }
        )
    )

    retained, error = wait_targets.read_wait_targets(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert exit_code == 0
    assert retained == {"remote-session"}
    assert error is None


def test_reply_after_resume_registers_under_mcp_root_and_wait_collects_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`claude --resume`後の最初の操作が`send_message`でも、`atk agents wait`がそのturnの結果を受け取る。

    MCPサーバーは起動時の識別子のルートへ書き、会話側は再開した会話の識別子で動く。
    旧ルートの状態ディレクトリが残り索引が無い状態で、応答の`root_session_id`から索引を作成せず
    環境変数のルートへ登録すると、待機は旧ルートを読んで対象不在で終わる。
    """
    _isolate_conversation(monkeypatch, tmp_path, "conversation-session")
    _write_root_status(tmp_path, "conversation-session", [])
    mcp_root = _write_root_status(tmp_path, "mcp-root", [{"session_id": "remote-session", "status": "running"}])

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "conversation-session",
                "cwd": str(tmp_path),
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "remote-session", "prompt": "続行"},
                "tool_response": {"structuredContent": {"delivery": "reply_started", "root_session_id": "mcp-root"}},
            }
        )
    )
    assert exit_code == 0
    assert wait_targets.read_wait_targets("mcp-root", "root.json", tmp_path) == ({"remote-session"}, None)
    assert wait_targets.read_wait_targets("conversation-session", "root.json", tmp_path) == (set(), None)

    results = mcp_root / "results"
    results.mkdir()
    terminal = {"session_id": "remote-session", "status": "completed", "turn_seq": 2, "agent_message": "完了"}
    (results / "remote-session.json").write_text(json.dumps(terminal), encoding="utf-8")
    capsys.readouterr()

    wait_exit = agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "conversation-session"}, state_root=tmp_path)

    lines = capsys.readouterr().out.splitlines()
    assert wait_exit == 0
    delivered = [json.loads(line) for line in lines]
    for row in delivered:
        row.pop("agent_message_path")
    assert delivered == [{"session_id": "remote-session", "status": "completed", "agent_message": "完了"}]


@pytest.mark.parametrize(
    ("operation", "structured"),
    [
        ("show", {"session_id": "remote-session", "status": "running", "root_session_id": "mcp-root"}),
        ("send_message", {"delivery": "steered", "root_session_id": "mcp-root"}),
        ("kill", {"status": "interrupted", "kill_requested": True, "root_session_id": "mcp-root"}),
    ],
)
@pytest.mark.parametrize("delegated", [False, True], ids=["conversation", "delegate"])
def test_any_response_with_root_writes_alias_except_in_delegate(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    operation: str,
    structured: dict[str, object],
    delegated: bool,
) -> None:
    """`root_session_id`を持つ応答なら操作名によらず別名索引を書き、委譲先の環境では書かない。"""
    _isolate_conversation(monkeypatch, tmp_path, "conversation-session")
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "owner-session")
    _write_root_status(tmp_path, "mcp-root", [])

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "conversation-session",
                "cwd": str(tmp_path),
                "tool_name": f"mcp__plugin_agent-toolkit_agents_server__{operation}",
                "tool_input": {"session_id": "remote-session"},
                "tool_response": {"structuredContent": structured},
            }
        )
    )

    alias = tmp_path / "agents-server" / "aliases" / "conversation-session.json"
    assert exit_code == 0
    assert alias.exists() is not delegated
    if not delegated:
        assert json.loads(alias.read_text(encoding="utf-8")) == {"version": 1, "root_session_id": "mcp-root"}


def test_steer_does_not_register_wait_target(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """既存turnへ追送するsteerは、新しい待機対象として登録しない。"""
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "root-session")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "root-session",
                "cwd": str(tmp_path),
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__send_message",
                "tool_input": {"session_id": "remote-session", "prompt": "補足する"},
                "tool_response": {"structuredContent": {"delivery": "steered"}},
            }
        )
    )

    retained, error = wait_targets.read_wait_targets(
        "root-session",
        "root.json",
        tmp_path,
    )
    assert exit_code == 0
    assert retained == set()
    assert error is None


def test_start_registers_wait_target_for_delegated_caller(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """委譲先が開始したsessionを、その委譲先の状態ファイルにだけ登録する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_OWNER_SESSION", "root-session")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "delegate-session")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setattr(_POSTTOOLUSE_MODULE, "update_state", lambda *_args: None)
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)

    exit_code = _POSTTOOLUSE_MODULE.main(
        json.dumps(
            {
                "session_id": "delegate-session",
                "cwd": str(tmp_path),
                "tool_name": "mcp__plugin_agent-toolkit_agents_server__start",
                "tool_input": {"prompt": "委譲する", "cwd": str(tmp_path)},
                "tool_response": {"structuredContent": {"session_id": "remote-session", "status": "running"}},
            }
        )
    )

    retained, error = wait_targets.read_wait_targets(
        "root-session",
        "delegate-session.json",
        tmp_path,
    )
    assert exit_code == 0
    assert retained == {"remote-session"}
    assert error is None
    assert not (
        shared_layout.wait_targets_directory(
            "root-session",
            "root.json",
            tmp_path,
        )
    ).exists()
