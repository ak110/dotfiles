"""agyの6イベントと、開いているbrain記録からの活動検出を検証する。"""

import os
import pathlib

import pytest

from agent_toolkit._common import session_activity, session_registry


def test_agy_hook_activity_and_stop_continue(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Stop許可で応答待ちになり、継続・次の呼出で活動へ戻る。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    host = session_activity.HostProcess("agy", identity)
    payload = {"session_id": "agy-conversation", "runtime": "agy"}
    for event, output, expected in (
        ("SessionStart", "{}", True),
        ("PreInvocation", "{}", True),
        ("PreToolUse", "{}", True),
        ("PostToolUse", "{}", True),
        ("PostInvocation", "{}", True),
        ("Stop", '{"decision":"continue","reason":"未完了"}', True),
        ("Stop", "{}", False),
        ("PreInvocation", "{}", True),
    ):
        session_activity.record_activity(
            {**payload, "hook_event_name": event},
            output=output,
            process=host,
            state_root=tmp_path,
        )
        snapshot = session_activity.scan_activity(state_root=tmp_path, processes=())
        assert snapshot.has_activity is expected
        assert not snapshot.uncertain


def test_agy_native_log_remains_uncertain_without_stop(tmp_path: pathlib.Path) -> None:
    """PLANNER_RESPONSE/DONEはtool前にも現れるため、開いた記録だけでは応答待ちにしない。"""
    path = tmp_path / "brain/native-conversation/.system_generated/logs/transcript_full.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text('{"type":"PLANNER_RESPONSE","status":"DONE","content":"調査する"}\n', encoding="utf-8")
    identity = session_registry.ProcessIdentity.of(os.getpid())
    assert identity is not None
    with path.open("a", encoding="utf-8"):
        snapshot = session_activity.scan_activity(
            state_root=tmp_path,
            processes=[session_activity.HostProcess("agy", identity)],
        )
        assert snapshot.active_session_ids == {"native-conversation"}
        assert snapshot.uncertain
