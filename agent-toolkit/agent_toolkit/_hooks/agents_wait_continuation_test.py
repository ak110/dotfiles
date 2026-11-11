"""観測の試みの後に残る待機を、登録・公開Post・公開Stopへ接続する。"""

import json

from agent_toolkit._agents_server import shared_layout, shared_roots, wait_targets
from agent_toolkit._common import session_registry, session_state
from agent_toolkit._common.file_lock import acquire_lock, release_lock
from agent_toolkit._hooks import posttooluse, stop


def test_wait_result_keeps_running_target_until_continuation_is_owned(monkeypatch, capsys):
    local, root, remote = "wait-owner", "wait-root", "remaining-worker"
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
        monkeypatch.delenv(name, raising=False)
    identity = shared_roots.StatusFileIdentity(root, "root.json", None)
    monkeypatch.setattr(shared_roots, "resolve_wait_identity", lambda *_args, **_kwargs: identity)
    monkeypatch.setattr(shared_roots, "resolve_conversation_root_session_id", lambda *_args, **_kwargs: root)
    wait_targets.retain_wait_targets(root, "root.json", [remote])
    session_registry.publish(remote, state=session_registry.PublicationState.PENDING_RUNNING, cwd="/test")
    session_state.update_state(
        local,
        lambda state: {
            **state,
            "agents_server_sessions": {remote: {"owner_agent_id": "main", "pending_observation": True}},
        },
    )
    assert (
        posttooluse.main(
            json.dumps(
                {"session_id": local, "tool_name": "Bash", "tool_input": {"command": "atk agents wait"}, "tool_response": ""}
            )
        )
        == 0
    )
    capsys.readouterr()
    assert session_state.read_state(local)["agents_server_sessions"][remote]["pending_observation"] is False
    payload = json.dumps({"session_id": local})
    result = stop.evaluate(payload)
    specific = result["hookSpecificOutput"]
    assert isinstance(specific, dict)
    context = specific["additionalContext"]
    assert isinstance(context, str)
    assert remote in context and "現在有効な待機" in context and "atk agents wait" in context
    assert "decision" not in result

    # CLIの待機所有権があれば、同じ登録・観測履歴でも警告しない。
    lock_path = shared_layout.status_directory(root) / "wait-locks" / "root.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w+b") as stream:
        acquire_lock(stream)
        try:
            assert not stop.evaluate(payload)
        finally:
            release_lock(stream)

    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    assert not stop.evaluate(json.dumps({"session_id": local, "last_assistant_message": f"待機中: {remote}"}))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION")
    session_registry.publish(remote, state=session_registry.PublicationState.RESULT_COMPLETED, cwd="/test")
    assert not stop.evaluate(payload)
