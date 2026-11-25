import json
import pathlib
import subprocess
import time
import types

import pytest

from agent_toolkit._atk.wi import background_sync, entries, snapshot
from agent_toolkit._atk.wi.constants import WI_STATES
from agent_toolkit._common import session_state, uwi_watch
from agent_toolkit._hooks import posttooluse, process_wi_notifications, uwi_completion
from agent_toolkit._testing import git_repository

_SESSION = "circuit-session"
_SENDER = "originating-session"
_REPO = "github.com/test/repo"


def _save(notes: pathlib.Path, number: int, *, repository: str = _REPO, uwi: bool = False, answer: str = "") -> str:
    name = f"20261101-000000-{number:03d}.md"
    path = notes / "2026" / "11" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    extra = f"submitter_session: {_SENDER}\n" if uwi else ""
    body = f"## 質問\n\n本文\n\n## 回答\n\n{answer}" if uwi else "## 反映内容と反映先\n\n本文\n"
    path.write_text(
        f"---\ntype: {'uwi' if uwi else 'awi'}\nstate: inbox\ntarget_repo: {repository}\n"
        f"{extra}---\n\n# 題名{number}\n\n{body}",
        encoding="utf-8",
    )
    return name


def _commit(notes: pathlib.Path) -> None:
    git_repository.run_git(notes, "add", "2026")
    git_repository.run_git(notes, "commit", "-m", "保存")


@pytest.mark.parametrize("direct", [False, True])
def test_circuit_notifies_new_processable_work_once(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], direct: bool
) -> None:
    notes = git_repository.init_repository(tmp_path / "notes")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESSION)
    if direct:
        assert session_state.update_state(_SESSION, session_state.set_process_wi_invoked)
    else:
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
        monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", _SESSION)
    original = _save(notes, 1)
    answered_elsewhere = _save(notes, 2, uwi=True)
    missing_submitter = _save(notes, 3, uwi=True)
    _commit(notes)
    initial = tuple(entries.iter_entries(notes, WI_STATES, None))
    assert snapshot.record_list_start(
        notes,
        initial,
        configured_start=lambda: ([(_REPO, None)], snapshot.identifiers(initial)),
        candidate_list=True,
        output=None,
        configured_output=False,
    )
    assert process_wi_notifications.build_notice(_SESSION) is None
    foreign = _save(notes, 4, repository="github.com/test/other")
    added = _save(notes, 5)
    assert uwi_watch.add(_SENDER, [answered_elsewhere])
    _save(notes, 2, uwi=True, answer="回答\n")
    _save(notes, 3, uwi=True, answer="別ホストからの回答\n")
    _commit(notes)
    assert answered_elsewhere in (uwi_completion.build_notice(_SENDER) or "")
    payload = json.dumps({"session_id": _SESSION, "tool_name": "Read", "tool_input": {}, "tool_response": "取得した本文"})
    assert posttooluse.main(payload) == 0
    rendered = capsys.readouterr().out
    notice = json.loads(rendered)["hookSpecificOutput"]["additionalContext"]
    assert added in notice and "題名5" in notice and missing_submitter in notice
    assert all(name not in notice for name in (original, foreign, answered_elsewhere))

    def unexpected_scan(*_args: object, **_kwargs: object) -> None:
        pytest.fail("変更の目印が同じままキューを走査した")

    monkeypatch.setattr(process_wi_notifications, "_scan", unexpected_scan)
    assert process_wi_notifications.build_notice(_SESSION) is None


@pytest.mark.parametrize("sender_action", ["exit", "receive", "receive_then_exit"])
def test_circuit_rechecks_suppressed_answer_without_scanning_queue(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, sender_action: str
) -> None:
    notes = git_repository.init_repository(tmp_path / "notes")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", _SESSION)
    assert session_state.update_state(_SESSION, session_state.set_process_wi_invoked)
    answered = _save(notes, 1, uwi=True)
    _commit(notes)
    initial = tuple(entries.iter_entries(notes, WI_STATES, None))
    assert snapshot.record_list_start(
        notes,
        initial,
        configured_start=lambda: ([(_REPO, None)], snapshot.identifiers(initial)),
        candidate_list=True,
        output=None,
        configured_output=False,
    )
    assert uwi_watch.add(_SENDER, [answered])
    _save(notes, 1, uwi=True, answer="回答\n")
    _commit(notes)
    living = {_SENDER}
    monkeypatch.setattr(process_wi_notifications.session_activity, "living_root_session_ids", lambda: frozenset(living))
    assert process_wi_notifications.build_notice(_SESSION) is None

    def unexpected_scan(*_args: object, **_kwargs: object) -> None:
        pytest.fail("変更の目印が同じままキューを走査した")

    monkeypatch.setattr(process_wi_notifications, "_scan", unexpected_scan)
    assert process_wi_notifications.build_notice(_SESSION) is None
    if sender_action == "exit":
        living.clear()
        notice = process_wi_notifications.build_notice(_SESSION) or ""
        assert answered in notice and "題名1" in notice
    elif sender_action == "receive":
        assert answered in (uwi_completion.build_notice(_SENDER) or "")
        assert process_wi_notifications.build_notice(_SESSION) is None
    else:

        def exit_after_receiving() -> frozenset[str]:
            assert answered in (uwi_completion.build_notice(_SENDER) or "")
            return frozenset()

        monkeypatch.setattr(process_wi_notifications.session_activity, "living_root_session_ids", exit_after_receiving)
        assert process_wi_notifications.build_notice(_SESSION) is None
        monkeypatch.setattr(process_wi_notifications.session_activity, "living_root_session_ids", frozenset)
    living.clear()
    assert process_wi_notifications.build_notice(_SESSION) is None


@pytest.mark.usefixtures("leaf_git_commands")
def test_other_clone_is_notified_after_detached_fetch(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    remote = git_repository.init_bare_repository(tmp_path / "origin.git")
    writer = git_repository.init_repository(tmp_path / "writer", initial_branch="main", origin=str(remote))
    _save(writer, 1)
    _commit(writer)
    git_repository.run_git(writer, "push", "-u", "origin", "main")
    notes = tmp_path / "reader"
    git_repository.run_git(tmp_path, "clone", str(remote), str(notes))
    background_sync.explicit_sync_finished(notes)
    fetched = background_sync.read_status(notes)["last_fetch"]
    assert isinstance(fetched, (float, int))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID", _SESSION)
    initial = tuple(entries.iter_entries(notes, WI_STATES, None))
    assert snapshot.record_list_start(
        notes,
        initial,
        configured_start=lambda: ([(_REPO, None)], snapshot.identifiers(initial)),
        candidate_list=True,
        output=None,
        configured_output=False,
    )
    remote_item = _save(writer, 2)
    _commit(writer)
    git_repository.run_git(writer, "push", "origin", "main")
    worker = []
    original_popen = subprocess.Popen

    def observe(*args, **kwargs):
        process = original_popen(*args, **kwargs)  # pylint: disable=consider-using-with
        if args and background_sync.__name__ in args[0]:
            worker.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", observe)
    now = fetched + 59
    monkeypatch.setattr(process_wi_notifications, "time", types.SimpleNamespace(time=lambda: now))
    assert process_wi_notifications.build_notice(_SESSION) is None
    assert not worker
    now = fetched + 60
    assert process_wi_notifications.build_notice(_SESSION) is None
    assert len(worker) == 1
    process = worker[0]
    try:
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
    now = time.time()
    assert remote_item in (process_wi_notifications.build_notice(_SESSION) or "")
    assert len(worker) == 1
