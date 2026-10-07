"""agent-toolkit/agent_toolkit/_hooks/agents_server_observations.py のテスト。"""

import json
import pathlib
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit._agents_server import shared_layout
from agent_toolkit._common import state_paths
from agent_toolkit._hooks import agents_server_observations


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
