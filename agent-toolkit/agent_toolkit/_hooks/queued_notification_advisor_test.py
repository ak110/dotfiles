"""未配送の完了通知の出力ファイルを案内するStop判定を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import background_tasks as _background_tasks
from agent_toolkit._hooks import queued_notification_advisor as subject
from agent_toolkit._hooks import transcript_scan as _transcript_scan
from agent_toolkit._testing.helpers import _write_transcript

_OUTPUT_FILE = "/tmp/claude-1000/tasks/b6n4gipz5.output"


def _notification(task_id: str | None = "b6n4gipz5", output_file: str | None = _OUTPUT_FILE) -> str:
    parts = ["<task-notification>"]
    if task_id is not None:
        parts.append(f"<task-id>{task_id}</task-id>")
    if output_file is not None:
        parts.append(f"<output-file>{output_file}</output-file>")
    parts.append("<status>completed</status>")
    parts.append("</task-notification>")
    return "\n".join(parts)


def _queue(operation: str, content: str | None = None) -> dict:
    entry: dict[str, object] = {"type": "queue-operation", "operation": operation}
    if content is not None:
        entry["content"] = content
    return entry


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access


def _evaluate(
    transcript: pathlib.Path, *, session_id: str = "queued-session", stop_hook_active: bool = False
) -> tuple[str, str]:
    _transcript_scan._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
    payload = {"session_id": session_id, "transcript_path": str(transcript), "stop_hook_active": stop_hook_active}
    return subject.evaluate(json.dumps(payload))


def test_enqueued_notification_reports_task_id_and_output_file(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    decision, body = _evaluate(transcript)

    assert decision == "notify"
    assert "b6n4gipz5" in body
    assert _OUTPUT_FILE in body
    assert "全量を読み" in body
    assert "出力先が未通知" in body and "委譲元" in body


@pytest.mark.parametrize("stop_hook_active", [False, True])
def test_stop_hook_active_does_not_suppress(tmp_path: pathlib.Path, stop_hook_active: bool) -> None:
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    assert _evaluate(transcript, stop_hook_active=stop_hook_active)[0] == "notify"


def test_delegated_session_is_notified(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    assert _evaluate(transcript)[0] == "notify"


@pytest.mark.parametrize("launch_kind", ["Agent", "Task"])
@pytest.mark.parametrize("with_tool_id", [False, True])
def test_agent_notification_uses_returned_message(tmp_path: pathlib.Path, launch_kind: str, with_tool_id: bool) -> None:
    tool_id = "toolu_agent1"
    notification = _notification()
    if with_tool_id:
        notification = notification.replace("</task-notification>", f"<tool-use-id>{tool_id}</tool-use-id></task-notification>")
    entries = [
        {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": tool_id, "name": launch_kind}]}},
        {
            "type": "user",
            "toolUseResult": {"agentId": "b6n4gipz5"},
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": "起動済み"}]},
        },
        _queue("enqueue", notification),
    ]
    transcript = _write_transcript(tmp_path, entries)
    decision, body = _evaluate(transcript)
    assert decision == "notify"
    assert "本文を結果として" in body and "再取得は不要" in body
    assert "b6n4gipz5" in body
    assert _OUTPUT_FILE not in body
    assert "そのパスを`Read`の`file_path`に指定して" not in body
    assert _evaluate(transcript) == ("approve", "")


def test_mixed_agent_and_bash_notifications_keep_distinct_guidance(tmp_path: pathlib.Path) -> None:
    agent = _notification("agent", "/tmp/agent-transcript.jsonl").replace(
        "</task-notification>", "<tool-use-id>toolu_agent</tool-use-id></task-notification>"
    )
    bash = _notification("bash", "/tmp/bash.output").replace(
        "</task-notification>", "<tool-use-id>toolu_bash</tool-use-id></task-notification>"
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use", "id": "toolu_agent", "name": "Agent"},
                        {"type": "tool_use", "id": "toolu_bash", "name": "Bash"},
                    ]
                },
            },
            _queue("enqueue", agent),
            _queue("enqueue", bash),
        ],
    )
    decision, body = _evaluate(transcript)
    assert decision == "notify"
    assert "本文を結果として" in body and "再取得は不要" in body
    assert "全量を読み" in body
    assert "/tmp/agent-transcript.jsonl" not in body
    assert "/tmp/bash.output" in body
    assert _evaluate(transcript) == ("approve", "")


def test_second_stop_for_same_notification_is_silent(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    assert _evaluate(transcript)[0] == "notify"
    assert _evaluate(transcript, stop_hook_active=True) == ("approve", "")


@pytest.mark.parametrize("previous_warning", [False, True])
def test_delivery_includes_warned_notifications_without_updating_warning_state(
    tmp_path: pathlib.Path, previous_warning: bool
) -> None:
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])
    if previous_warning:
        assert _evaluate(transcript)[0] == "notify"
    before = read_state("queued-session")
    payload = json.dumps({"session_id": "queued-session", "transcript_path": str(transcript)})
    assert subject.evaluate(payload, allow_delivery=True) == ("yield", "")
    assert read_state("queued-session") == before


@pytest.mark.parametrize(
    "phase,loop,other,yields",
    [
        ("resumed", True, False, False),
        ("resumed", True, True, True),
        ("pending", True, False, True),
        ("resumed", False, False, True),
    ],
)
def test_restarted_wait_notification_does_not_pause_loop(
    tmp_path,
    monkeypatch,
    phase,
    loop,
    other,
    yields,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1" if loop else "")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    directory = tmp_path / "agent-toolkit-function-hooks"
    directory.mkdir()
    (directory / "restart-queued-session.txt").write_text(
        json.dumps(
            {
                "phase": phase,
                "waits": [{"id": "b6n4gipz5", "command": "atk agents wait"}],
            }
        ),
        encoding="utf-8",
    )
    events = [_queue("enqueue", _notification())]
    if other:
        events.append(_queue("enqueue", _notification("unrelated")))
    transcript = _write_transcript(tmp_path, events)
    payload = json.dumps({"session_id": "queued-session", "transcript_path": str(transcript)})
    assert (subject.evaluate(payload, allow_delivery=True)[0] == "yield") is yields


def test_new_notification_after_notified_one_is_reported_alone(tmp_path: pathlib.Path) -> None:
    first = _notification()
    second = _notification("bj8pfie1d", "/tmp/claude-1000/tasks/bj8pfie1d.output")
    transcript = _write_transcript(tmp_path, [_queue("enqueue", first)])
    assert _evaluate(transcript)[0] == "notify"

    transcript = _write_transcript(tmp_path, [_queue("enqueue", first), _queue("enqueue", second)])
    decision, body = _evaluate(transcript)

    assert decision == "notify"
    assert "bj8pfie1d" in body
    assert "b6n4gipz5" not in body


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param([_queue("enqueue", _notification()), _queue("dequeue")], id="dequeue"),
        pytest.param([_queue("enqueue", _notification()), _queue("remove", _notification())], id="remove"),
        pytest.param([_queue("enqueue", _notification()), _queue("popAll", _notification())], id="popAll"),
        pytest.param([_queue("enqueue", _notification()), _queue("popOne", _notification())], id="popOne"),
        pytest.param([], id="empty"),
    ],
)
def test_delivered_notifications_are_silent(tmp_path: pathlib.Path, entries: list[dict]) -> None:
    transcript = _write_transcript(tmp_path, entries)

    assert _evaluate(transcript) == ("approve", "")


def test_notification_without_output_file_shows_task_id_only(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification(output_file=None))])

    decision, body = _evaluate(transcript)

    assert decision == "notify"
    assert "- task-id: b6n4gipz5\n" in body + "\n"
    assert "出力ファイル: " not in body


@pytest.mark.parametrize("operation", ["popAll", "popOne"])
def test_pop_removes_only_matching_content_from_stop_and_advisor(tmp_path: pathlib.Path, operation: str) -> None:
    first = _notification()
    second = _notification("other", "/tmp/other.output")
    entries = [
        _queue("enqueue", first),
        _queue("enqueue", second),
        _queue("enqueue", first),
        _queue(operation, "登録されていない本文"),
        _queue(operation, first),
    ]
    assert _background_tasks.queued_task_notification_contents(entries) == [second, first]
    transcript = _write_transcript(tmp_path, entries)
    assert _background_tasks.is_pending_async_work(str(transcript), "queued-session", background_tasks=[], session_state=None)
    entries.extend([_queue(operation, first), _queue(operation, second)])
    transcript = _write_transcript(tmp_path, entries)
    assert _evaluate(transcript) == ("approve", "")
    assert not _background_tasks.is_pending_async_work(
        str(transcript), "queued-session", background_tasks=[], session_state=None
    )


@pytest.mark.parametrize("operation", ["popAll", "popOne"])
@pytest.mark.parametrize(("delivered", "expected"), [(False, "notify"), (True, "approve")])
def test_editable_pop_then_delivery(tmp_path: pathlib.Path, operation: str, delivered: bool, expected: str) -> None:
    entries = [_queue("enqueue", "編集する入力"), _queue("enqueue", _notification()), _queue(operation, "編集する入力")]
    if delivered:
        entries.append(_queue("dequeue"))
    transcript = _write_transcript(tmp_path, entries)
    assert _evaluate(transcript)[0] == expected


def _delivered_user(content: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": content}}


def test_notification_delivered_after_reclaimed_input_is_silent(tmp_path: pathlib.Path) -> None:
    """取り戻した入力の後に通知が配送された記録では、案内せず残る非同期作業としても扱わない。"""
    entries = [
        _queue("enqueue", "送信待ちの入力"),
        _queue("popAll", "送信待ちの入力"),
        _queue("enqueue", _notification()),
        _queue("dequeue"),
        _delivered_user(_notification()),
    ]
    transcript = _write_transcript(tmp_path, entries)

    assert _evaluate(transcript) == ("approve", "")
    assert not _background_tasks.is_pending_async_work(
        str(transcript), "queued-session", background_tasks=[], session_state=None
    )


def test_notification_stays_when_agent_message_is_dequeued_first(tmp_path: pathlib.Path) -> None:
    agent_message = "<agent-message>委譲先からの途中報告</agent-message>"
    entries = [
        _queue("enqueue", _notification()),
        _queue("enqueue", agent_message),
        _queue("dequeue"),
        _delivered_user(agent_message),
    ]
    assert _background_tasks.queued_task_notification_contents(entries) == [_notification()]
    transcript = _write_transcript(tmp_path, entries)

    decision, body = _evaluate(transcript)

    assert decision == "notify"
    assert "b6n4gipz5" in body


def test_missing_transcript_path_is_silent() -> None:
    assert subject.evaluate(json.dumps({"session_id": "queued-session"})) == ("approve", "")
