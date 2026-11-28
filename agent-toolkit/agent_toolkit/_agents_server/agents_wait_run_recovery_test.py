# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""待機CLIの保存runの回復と再配送を検証する。"""

import json
import pathlib
from collections.abc import Callable
from typing import Any, Literal

import pytest

from agent_toolkit._agents_server import (
    agents_wait,
    notice_inbox,
    responses,
    result_projection,
    retained_results,
    shared_layout,
    wait_run,
)
from agent_toolkit._common import session_registry
from agent_toolkit._testing.agents_wait_support import _publish_result_on_sleep, _running_body, _write_own_status
from agent_toolkit._testing.agents_wait_support import _short_wait as _short_wait
from agent_toolkit._testing.agents_wait_support import _wait_environment as _wait_environment
from agent_toolkit._testing.agents_wait_support_contention import (
    _alive_followers,
    _interrupt_after_published,
    _leave_result_original,
    _LockedStepFailed,
    _run_contended_wait,
    _stash_notice,
    _stash_result,
    _write_current_wait_run,
)

_REVIEW_ACTION = "同じレビュー担当の前回の返却"


@pytest.fixture(name="review_results")
def _review_results(wait_environment: pathlib.Path) -> pathlib.Path:
    wait_environment.mkdir(parents=True)
    return wait_environment


def _write_review_reply(
    results: pathlib.Path,
    turn: int,
    *,
    session_id: str = "session-1",
    label: str = "continuation-review",
    kind: Literal["delegate", "explore", "shell"] = "delegate",
    task_role: str | None = None,
    status: str = "completed",
    held: bool = False,
    record_turn: bool = True,
    body: bool = True,
    format_recovered: bool = False,
) -> None:
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.from_status(status, result_available=True),
        launch_kind=kind,
        launch_info=session_registry.LaunchInfo(task_role=task_role),
        model_type="execute",
        turn_seq=turn,
        result_contract_recovery_consumed=format_recovered,
        state_root=results.parents[2],
    )
    result = {
        "session": {"session_id": session_id, "label": label},
        "status": status,
        **({"agent_message": f"返却 {turn}"} if body else {}),
        **({"turn_seq": turn, "finalized_at": "2026-11-28T09:00:00Z"} if record_turn else {}),
        **({"error": {"heldResultFinalized": True}} if held else {}),
    }
    (results / f"{session_id}.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


def _wait_review(results: pathlib.Path) -> int:
    return agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=results.parents[2])


def _review_row(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    result = capsys.readouterr()
    assert not result.err
    return json.loads(result.out)


def _review_run_directory(results: pathlib.Path) -> pathlib.Path:
    return results.parent / "wait-results" / "root.json"


@pytest.mark.parametrize("last_turn", [3, 6])
def test_wait_guides_only_first_free_text_review_reply(review_results, capsys, monkeypatch, last_turn):
    monkeypatch.setattr(result_projection, "was_review_guidance_presented", lambda *_: True)
    monkeypatch.setattr(result_projection, "has_read_review_guidance", lambda *_: True)
    rows = []
    for turn in range(1, last_turn + 1):
        _write_own_status(review_results, [{"session_id": "session-1"}])
        _write_review_reply(review_results, turn, kind="explore")
        assert _wait_review(review_results) == 0
        rows.append(_review_row(capsys))

    assert [index for index, row in enumerate(rows, 1) if _REVIEW_ACTION in row["next_action"]] == [2]
    previous_path = pathlib.Path(rows[0]["agent_message_path"])
    assert previous_path.read_text(encoding="utf-8") == "返却 1"
    action = rows[1]["next_action"]
    assert result_projection.REVIEW_RESULT_NEXT_ACTION not in action
    assert action.splitlines()[:-1] == rows[0]["next_action"].splitlines()
    assert str(previous_path) in action
    assert "「方式見直しと再レビュー」手順11の条件に当たるかを判定してから修正を依頼する" in action
    assert "論点の再発や再レビューの要否を判定したものではない" in action


@pytest.mark.parametrize(
    ("earlier", "current"),
    [
        ({"status": "failed"}, {}),
        ({"status": "interrupted"}, {}),
        ({"held": True}, {}),
        ({}, {"status": "failed"}),
        ({}, {"status": "interrupted"}),
        ({}, {"held": True}),
        ({}, {"label": "reader-fit-review"}),
        ({}, {"label": "other-label"}),
        ({}, {"task_role": "exec-review"}),
        ({}, {"kind": "shell"}),
        ({}, {"record_turn": False}),
        ({"record_turn": False}, {}),
    ],
    ids=[
        "previous-failed",
        "previous-interrupted",
        "previous-wait-statement",
        "failed",
        "interrupted",
        "wait-statement",
        "reader-fit",
        "other-label",
        "task-role",
        "shell",
        "unknown-current-turn",
        "unknown-previous-turn",
    ],
)
def test_wait_keeps_normal_delivery_outside_review_reply_conditions(review_results, capsys, earlier, current):
    for turn, options in enumerate((earlier, current), 1):
        _write_own_status(review_results, [{"session_id": "session-1"}])
        _write_review_reply(review_results, turn, **options)
        assert _wait_review(review_results) == 0
        row = _review_row(capsys)
        assert row["agent_message"] == f"返却 {turn}"
        assert _REVIEW_ACTION not in row.get("next_action", "")


@pytest.mark.parametrize(
    "source", ["send-message", "other-recipient", "unreadable-directory", "damaged-run", "format-recovery"]
)
def test_wait_counts_only_eligible_deliveries_in_recipient_history(review_results, capsys, monkeypatch, source):
    if source == "send-message":
        previous = result_projection.with_result_next_action({"status": "completed", "agent_message": "返却 1"}, "scope-review")
        reply = responses.public_send_message_response(
            {"delivery": "reply_started", "label": "scope-review", "previous_result": previous}
        )
        assert reply["previous_result"]["next_action"] == result_projection.REVIEW_RESULT_NEXT_ACTION
    else:
        directory = _review_run_directory(review_results)
        target = directory.with_name("other.json") if source == "other-recipient" else directory
        target.mkdir(parents=True)
        recorded = wait_run.RunningRun(run_id="earlier", targets=["session-1"]).publish(
            json.dumps({"status": "completed", "session_id": "session-1"}), 0, "stdout", result_turns={"session-1": 1}
        )
        if source != "format-recovery":
            recorded = recorded.deliver(continuable=False)
        (target / "earlier.json").write_text(
            "{" if source == "damaged-run" else recorded.model_dump_json(exclude_unset=True), encoding="utf-8"
        )
        if source == "unreadable-directory":
            original = pathlib.Path.iterdir

            def unreadable(path):
                if path == directory:
                    raise PermissionError("待機run一覧を読めない")
                return original(path)

            monkeypatch.setattr(pathlib.Path, "iterdir", unreadable)

    _write_own_status(review_results, [{"session_id": "session-1"}])
    _write_review_reply(review_results, 2, format_recovered=source == "format-recovery")
    assert _wait_review(review_results) == 0
    first = _review_row(capsys)
    assert first["agent_message"] == "返却 2"
    assert _REVIEW_ACTION not in first["next_action"]
    if source == "format-recovery":
        _write_own_status(review_results, [{"session_id": "session-1"}])
        _write_review_reply(review_results, 3)
        assert _wait_review(review_results) == 0
        assert first["agent_message_path"] in _review_row(capsys)["next_action"]


@pytest.mark.parametrize("damage", ["launch-kind-type", "missing-model"])
def test_wait_delivers_when_review_registry_becomes_invalid_after_collection(review_results, capsys, monkeypatch, damage):
    for turn in (1, 2):
        _write_own_status(review_results, [{"session_id": "session-1"}])
        _write_review_reply(review_results, turn)
        if turn == 1:
            assert _wait_review(review_results) == 0
            _review_row(capsys)
    path = session_registry.registry_directory(review_results.parents[2]) / "session-1.json"
    release = agents_wait.wait_targets.release_wait_target

    def release_then_damage(root, owner, session_id, state_root):
        release(root, owner, session_id, state_root)
        if session_id == "session-1":
            record = json.loads(path.read_text(encoding="utf-8"))
            if damage == "launch-kind-type":
                record["launch_kind"] = []
            else:
                record.pop("model")
            replacement = path.with_suffix(".replacement")
            replacement.write_text(json.dumps(record), encoding="utf-8")
            replacement.replace(path)

    monkeypatch.setattr(agents_wait.wait_targets, "release_wait_target", release_then_damage)
    assert _wait_review(review_results) == 0
    row = _review_row(capsys)
    assert row["status"] == "completed"
    assert row["agent_message"] == "返却 2"
    assert pathlib.Path(row["agent_message_path"]).read_text(encoding="utf-8") == "返却 2"
    assert row["next_action"] == result_projection.REVIEW_RESULT_NEXT_ACTION


@pytest.mark.parametrize("interrupt", ["before-body", "before-output", "after-output"])
def test_wait_recovers_first_review_reply_until_delivery_is_confirmed(review_results, capsys, monkeypatch, interrupt):
    _write_own_status(review_results, [{"session_id": "session-1"}])
    _write_review_reply(review_results, 1, body=False)
    assert _wait_review(review_results) == 0
    assert _REVIEW_ACTION not in _review_row(capsys)["next_action"]
    _write_own_status(review_results, [{"session_id": "session-1"}])
    _write_review_reply(review_results, 2)
    first_output: dict[str, Any] | None = None
    if interrupt == "before-output":
        _interrupt_after_published(monkeypatch, review_results.parents[2], capsys)
    elif interrupt == "before-body":

        def fail_body(*_args):
            raise OSError("本文を保存できない")

        with monkeypatch.context() as injection:
            injection.setattr(agents_wait._BodyFileWriter, "write", fail_body)  # pylint: disable=protected-access
            assert _wait_review(review_results) == 11
        output = capsys.readouterr()
        assert not output.out and "結果本文のファイルを書けません" in output.err
    else:
        original_write = agents_wait._write_json  # pylint: disable=protected-access

        def interrupt_before_confirmation(path, value):
            if value.get("status") == "foreground-delivered":
                raise KeyboardInterrupt
            original_write(path, value)

        with monkeypatch.context() as injection:
            injection.setattr(agents_wait, "_write_json", interrupt_before_confirmation)
            with pytest.raises(KeyboardInterrupt):
                _wait_review(review_results)
        first_output = _review_row(capsys)
        assert _REVIEW_ACTION in first_output["next_action"]

    assert _wait_review(review_results) == 0
    recovered = _review_row(capsys)
    assert recovered["agent_message"] == "返却 2"
    assert recovered["next_action"].count(_REVIEW_ACTION) == 1
    assert "前回の`atk agents wait`の出力" in recovered["next_action"]
    if first_output is not None:
        assert recovered == first_output

    directory = _review_run_directory(review_results)
    current = wait_run.read_current(directory / "current.json")
    assert current is not None
    _write_own_status(review_results, [{"session_id": "still-running", "status": "running"}])
    acquire = agents_wait.acquire_lock
    contended = True

    def acquire_after_preceding_wait(stream, **options):
        nonlocal contended
        if contended:
            contended = False
            raise BlockingIOError("先行する待機が所有")
        acquire(stream, **options)

    with monkeypatch.context() as injection:
        injection.setattr(agents_wait, "acquire_lock", acquire_after_preceding_wait)
        injection.setattr(agents_wait, "get_cli_wait_timeout", lambda _: 10)
        injection.setattr(agents_wait.time, "sleep", lambda _: None)
        assert _wait_review(review_results) == 8
    reference = _review_row(capsys)
    assert reference["status"] == "consumed"
    assert reference["recovery"]["run_path"] == str(directory / f"{current.run_id}.json")
    assert reference["recovery"]["agent_message_paths"] == [recovered["agent_message_path"]]
    assert _REVIEW_ACTION not in json.dumps(reference, ensure_ascii=False)


def test_wait_deduplicates_review_turns_with_one_scan_for_the_batch(review_results, capsys, monkeypatch):
    directory = _review_run_directory(review_results)
    ids = ["session-1", "session-2"]
    reads = []
    original_read = wait_run.read_run

    def read(path):
        if path.name.startswith("history-"):
            reads.append(path.name)
        return original_read(path)

    monkeypatch.setattr(wait_run, "read_run", read)
    directory.mkdir(parents=True)
    for index in range(1, 101):
        earlier_ids = ids if index <= 2 else ["other-session"]
        launch = wait_run.RunningRun(run_id=f"history-{index}", targets=earlier_ids)
        output = "\n".join(json.dumps({"status": "completed", "session_id": identifier}) for identifier in earlier_ids)
        turns = dict.fromkeys(earlier_ids, 1)
        delivered = launch.publish(output, 0, "stdout", result_turns=turns).deliver(continuable=False)
        (directory / f"history-{index}.json").write_text(delivered.model_dump_json(), encoding="utf-8")

    for label, current_ids in (("other-label", ["session-3", "session-4"]), ("scope-review", ids)):
        _write_own_status(review_results, [{"session_id": identifier} for identifier in current_ids])
        for identifier in current_ids:
            _write_review_reply(review_results, 2, session_id=identifier, label=label)
        assert _wait_review(review_results) == 0
        rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
        assert {row["session_id"] for row in rows} == set(current_ids)
        assert [_REVIEW_ACTION in row.get("next_action", "") for row in rows] == [label == "scope-review"] * len(current_ids)
        assert len(reads) == len(set(reads)) == (0 if label == "other-label" else 100)


@pytest.mark.parametrize(
    ("prepare", "expected"),
    [
        (_leave_result_original, {"session_id": "session-1", "status": "completed"}),
        (_stash_result, {"session_id": "session-1", "status": "completed"}),
        (
            _stash_notice,
            {
                "session_id": "session-1",
                "status": "running",
                "notices": [{"body": "通知"}],
            },
        ),
    ],
    ids=["original-result", "stashed-result", "stashed-notice"],
)
def test_wait_recovers_contended_running_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    prepare: Callable[[pathlib.Path, pathlib.Path], None],
    expected: dict[str, object],
) -> None:
    assert _run_contended_wait(tmp_path, monkeypatch, prepare) == 0

    captured = capsys.readouterr()
    assert [_running_body(json.loads(line)) for line in captured.out.splitlines()] == [expected]
    assert not captured.err


def test_contended_wait_joins_follower_when_prepare_fails(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """準備が失敗しても、lockを解放して後発待機の終了を待ってから例外を伝える。"""

    def leave_result_then_fail(state_root: pathlib.Path, run_path: pathlib.Path) -> None:
        _leave_result_original(state_root, run_path)
        raise _LockedStepFailed

    with pytest.raises(_LockedStepFailed):
        _run_contended_wait(tmp_path, monkeypatch, leave_result_then_fail)

    assert not _alive_followers()
    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_wait_continues_after_contended_owner_aborts(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """lock取得後も待機を続け、後から届く終了時の結果を受け取る。"""
    results = shared_layout.results_directory("root-session", tmp_path)
    monkeypatch.setattr(agents_wait, "get_cli_wait_timeout", lambda _bucket: 10.0)
    monkeypatch.setattr(agents_wait.time, "sleep", _publish_result_on_sleep(results))

    assert _run_contended_wait(tmp_path, monkeypatch) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_wait_recovers_target_added_during_lock_contention(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """先行待機がlock保持中に加えた対象と退避結果を引き継ぐ。"""

    def add_target(state_root: pathlib.Path, run_path: pathlib.Path) -> None:
        run = json.loads(run_path.read_text(encoding="utf-8"))
        run["targets"] = ["session-1", "session-2"]
        agents_wait._write_json(run_path, run)  # pylint: disable=protected-access
        results = shared_layout.results_directory("root-session", state_root)
        results.mkdir(parents=True, exist_ok=True)
        (results / "session-2.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
        claimed, error = retained_results.take_result(
            "root-session",
            "session-2",
            "root.json",
            collector="test",
            state_root=state_root,
            stash_path=run_path.with_suffix("") / "results" / "session-2.json",
        )
        assert claimed == {"status": "completed"}
        assert error is None

    assert _run_contended_wait(tmp_path, monkeypatch, add_target) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-2", "status": "completed"}


@pytest.mark.parametrize(
    ("status", "expected_code", "expected_stream"),
    [
        ("published", 0, "stdout"),
        ("running", 3, "stdout"),
    ],
)
def test_later_wait_uses_matching_current_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
    expected_code: int,
    expected_stream: str,
) -> None:
    """公開済みrunは回収し、残存する未公開runは待機を再開する。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, run_path = _write_current_wait_run(tmp_path, target="session-1", status=status)

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == expected_code
    )
    captured = capsys.readouterr()
    output = captured.out if expected_stream == "stdout" else captured.err
    if status == "published":
        assert json.loads(output) == {"session_id": "session-1", "status": "completed"}
        assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "consumed"
    else:
        assert _running_body(json.loads(output)) == {"session_id": "session-1", "status": "running"}
    assert json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8")) == {"run_id": "run-1"}


def test_wait_recovers_abandoned_run(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(
        json.dumps({"status": "completed", "turn_seq": 3, "finalized_at": "2026-11-21T02:00:00Z"}), encoding="utf-8"
    )

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}
    receipt = json.loads(run_path.read_text(encoding="utf-8"))
    assert receipt["status"] == "foreground-delivered"
    assert receipt["result_turns"] == {"session-1": 3}


@pytest.mark.parametrize("record", ["current", "run"])
@pytest.mark.parametrize("failure", ["json", "unicode", "permission", "shape"])
def test_wait_preserves_unreadable_recovery_record(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record: str,
    failure: str,
) -> None:
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    directory, run_path = _write_current_wait_run(tmp_path, target="session-1", status="published")
    target = directory / "current.json" if record == "current" else run_path
    original = target.read_bytes()

    def fail_open(path: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if path == target:
            raise PermissionError("読取拒否")
        return open(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        if failure == "permission":
            patch.setattr(pathlib.Path, "open", fail_open)
        else:
            target.write_bytes({"json": b"{", "unicode": b"\xff", "shape": b"[]"}[failure])
        assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 6
        captured = capsys.readouterr()
        assert not captured.out
        assert str(target) in captured.err
        assert set(directory.glob("*.json")) == {directory / "current.json", run_path}
    target.write_bytes(original)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


@pytest.mark.parametrize("original_exists", [False, True])
def test_wait_recovers_claimed_result_once(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    original_exists: bool,
) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    status_path = _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    results.mkdir(parents=True)
    result_path = results / "session-1.json"
    result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    stash_path = run_path.with_suffix("") / "results" / "session-1.json"
    claimed, error = retained_results.take_result(
        "root-session", "session-1", "root.json", collector="test", state_root=tmp_path, stash_path=stash_path
    )
    assert claimed == {"status": "completed"}
    assert error is None
    assert stash_path.exists()
    if original_exists:
        result_path.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    else:
        status_path.unlink()

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == [
        {"session_id": "session-1", "status": "completed"}
    ]
    assert not result_path.exists()


@pytest.mark.parametrize("original_exists", [False, True])
def test_wait_recovers_claimed_notice_once(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    original_exists: bool,
) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    _, run_path = _write_current_wait_run(tmp_path, target="session-1", status="running")
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    notice_path = notices / "session-1.1.json"
    payload = {"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}
    notice_path.write_text(json.dumps(payload), encoding="utf-8")
    stash_directory = run_path.with_suffix("") / "notices"
    assert notice_inbox.take_notices(
        "root-session",
        "session-1",
        tmp_path,
        recipient_session_id="root-session",
        owner_status_file="root.json",
        stash_directory=stash_directory,
    ) == [{"body": payload["body"]}]
    if original_exists:
        notice_path.write_text(json.dumps(payload), encoding="utf-8")

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert [_running_body(json.loads(line)) for line in capsys.readouterr().out.splitlines()] == [
        {
            "session_id": "session-1",
            "status": "running",
            "notices": [{"body": payload["body"]}],
        }
    ]
    assert not notice_path.exists()


def test_wait_replays_published_run_after_publication_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    _interrupt_after_published(monkeypatch, tmp_path, capsys)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0

    assert json.loads(capsys.readouterr().out) == {"session_id": "session-1", "status": "completed"}


def test_wait_replays_published_notice_after_publication_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    _interrupt_after_published(monkeypatch, tmp_path, capsys)

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert _running_body(json.loads(capsys.readouterr().out)) == {
        "session_id": "session-1",
        "status": "running",
        "notices": [{"body": "通知"}],
    }


def test_wait_replays_flushed_result_after_interrupt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    results = shared_layout.results_directory("root-session", tmp_path)
    _write_own_status(results, [{"session_id": "session-1"}])
    results.mkdir(parents=True)
    (results / "session-1.json").write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    notices = shared_layout.notices_directory("root-session", tmp_path)
    notices.mkdir()
    (notices / "session-1.1.json").write_text(
        json.dumps({"version": 1, "session_id": "session-1", "sent_at": "2026-09-25T00:00:00Z", "body": "通知"}),
        encoding="utf-8",
    )
    original_write = agents_wait._write_json  # pylint: disable=protected-access

    def interrupt_after_flush(path: pathlib.Path, value: dict[str, object]) -> None:
        if value.get("status") == "foreground-delivered":
            raise KeyboardInterrupt
        original_write(path, value)

    monkeypatch.setattr(agents_wait, "_write_json", interrupt_after_flush)
    with pytest.raises(KeyboardInterrupt):
        agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path)
    first = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert first == [
        {
            "session_id": "session-1",
            "status": "completed",
            "notices": [{"body": "通知"}],
        }
    ]

    monkeypatch.setattr(agents_wait, "_write_json", original_write)
    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 0
    assert [json.loads(line) for line in capsys.readouterr().out.splitlines()] == first
    run_directory = shared_layout.status_directory("root-session", tmp_path) / "wait-results" / "root.json"
    current = json.loads((run_directory / "current.json").read_text(encoding="utf-8"))
    run_path = run_directory / f"{current['run_id']}.json"
    assert json.loads(run_path.read_text(encoding="utf-8"))["status"] == "consumed"
    assert not run_path.with_suffix("").exists()


@pytest.mark.parametrize("status", ["consumed", "foreground-delivered"])
@pytest.mark.parametrize("continuable", [False, True])
def test_later_wait_starts_new_run_for_consumed_current_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
    continuable: bool,
) -> None:
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, _ = _write_current_wait_run(
        tmp_path,
        target="session-1",
        status=status,
        continuable=continuable,
    )

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 3
    )
    assert _running_body(json.loads(capsys.readouterr().out)) == {"session_id": "session-1", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


@pytest.mark.parametrize("status", ["consumed", "running", "published"])
def test_later_wait_starts_new_run_for_changed_targets(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    status: str,
) -> None:
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-2"}])
    run_directory, _ = _write_current_wait_run(tmp_path, target="session-1", status=status)

    assert (
        agents_wait.wait_for_result(
            environment={"CLAUDE_CODE_SESSION_ID": "root-session"},
            state_root=tmp_path,
        )
        == 3
    )
    assert _running_body(json.loads(capsys.readouterr().out)) == {"session_id": "session-2", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"


def test_later_wait_starts_new_run_for_continuable_published_run(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """継続可能な公開runの旧出力を再演せず、新しい待機を開始する。"""
    _write_own_status(shared_layout.results_directory("root-session", tmp_path), [{"session_id": "session-1"}])
    run_directory, _ = _write_current_wait_run(tmp_path, target="session-1", status="published", continuable=True)

    assert agents_wait.wait_for_result(environment={"CLAUDE_CODE_SESSION_ID": "root-session"}, state_root=tmp_path) == 3

    assert _running_body(json.loads(capsys.readouterr().out)) == {"session_id": "session-1", "status": "running"}
    current = json.loads(run_directory.joinpath("current.json").read_text(encoding="utf-8"))
    assert current["run_id"] != "run-1"
