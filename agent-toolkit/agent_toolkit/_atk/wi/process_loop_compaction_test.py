import types

import pytest

from agent_toolkit._atk.wi import process_loop_compaction
from agent_toolkit._common import process_tree, session_registry


@pytest.mark.parametrize("pending", [False, True])
def test_compaction_never_starts_when_resume_is_blocked(tmp_path, monkeypatch, pending):
    resolution = types.SimpleNamespace(
        state=session_registry.Resolution.TERMINAL if pending else session_registry.Resolution.RUNNING,
        resume_pending=pending,
    )
    monkeypatch.setattr(session_registry, "resolve", lambda *_args: resolution)
    calls = []

    def run(*_args, **_kwargs):
        calls.append(True)
        pytest.fail("停止未確認の会話を起動した")

    monkeypatch.setattr(process_tree, "run_with_timeout", run)
    with pytest.raises(process_loop_compaction.ResumeBlocked):
        process_loop_compaction.compact_before_resume("running", ["claude"], {}, tmp_path, tmp_path / "diagnostic")
    assert not calls


def test_unreclaimed_compaction_process_does_not_allow_resume(tmp_path, monkeypatch):
    failure = process_tree.ReclaimError(["owned child"], [], context="compaction")

    def run(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(process_tree, "run_with_timeout", run)
    with pytest.raises(process_tree.ReclaimError) as raised:
        process_loop_compaction.compact_before_resume("stopped", ["claude"], {}, tmp_path, tmp_path / "diagnostic")
    assert raised.value is failure
