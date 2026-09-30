"""未配送の完了通知の出力ファイルを案内するStop判定を検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import queued_notification_advisor as subject
from agent_toolkit._hooks import stop_gate as _stop_gate
from agent_toolkit._testing.helpers import _write_transcript

_OUTPUT_FILE = "/tmp/claude-1000/tasks/b6n4gipz5.output"


def _notification(task_id: str | None = "b6n4gipz5", output_file: str | None = _OUTPUT_FILE) -> str:
    """ホストが`queue-operation`へ記録する完了通知の本文を生成する。"""
    parts = ["<task-notification>"]
    if task_id is not None:
        parts.append(f"<task-id>{task_id}</task-id>")
    if output_file is not None:
        parts.append(f"<output-file>{output_file}</output-file>")
    parts.append("<status>completed</status>")
    parts.append("</task-notification>")
    return "\n".join(parts)


def _queue(operation: str, content: str | None = None) -> dict:
    """キュー操作の記録を生成する。"""
    entry: dict[str, object] = {"type": "queue-operation", "operation": operation}
    if content is not None:
        entry["content"] = content
    return entry


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """セッション状態とStop判定ログの保存先、transcriptの解析キャッシュを分離する。"""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(_stop_gate, "_wait_for_end_turn", lambda _path: None)
    _stop_gate._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access


def _evaluate(
    transcript: pathlib.Path, *, session_id: str = "queued-session", stop_hook_active: bool = False
) -> tuple[str, str]:
    """Stop入力を組み立てて判定する。"""
    _stop_gate._TRANSCRIPT_ENTRIES_CACHE.clear()  # pylint: disable=protected-access
    payload = {"session_id": session_id, "transcript_path": str(transcript), "stop_hook_active": stop_hook_active}
    return subject.evaluate(json.dumps(payload))


def test_enqueued_notification_reports_task_id_and_output_file(tmp_path: pathlib.Path) -> None:
    """`enqueue`だけが残る通知は、task-idと出力ファイルの絶対パスと読取の指示を返す。"""
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    decision, body = _evaluate(transcript)

    assert decision == "notify"
    assert "b6n4gipz5" in body
    assert _OUTPUT_FILE in body
    assert "出力ファイルを読んで結果を受け取り" in body
    assert "ターンを終えず" in body
    assert "再発行しない" in body


@pytest.mark.parametrize("stop_hook_active", [False, True])
def test_stop_hook_active_does_not_suppress(tmp_path: pathlib.Path, stop_hook_active: bool) -> None:
    """再呼び出しの印があっても案内する。目的の場面は既に継続した後の再呼び出しであるため。"""
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    assert _evaluate(transcript, stop_hook_active=stop_hook_active)[0] == "notify"


def test_delegated_session_is_notified(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """委譲先も出力ファイルを読めるため案内する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    assert _evaluate(transcript)[0] == "notify"


def test_second_stop_for_same_notification_is_silent(tmp_path: pathlib.Path) -> None:
    """同じ通知が残ったままの2回目のStopでは案内しない。"""
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification())])

    assert _evaluate(transcript)[0] == "notify"
    assert _evaluate(transcript, stop_hook_active=True) == ("approve", "")


def test_new_notification_after_notified_one_is_reported_alone(tmp_path: pathlib.Path) -> None:
    """案内済みの通知が残っていても、新しい通知だけを案内する。"""
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
        pytest.param([], id="empty"),
    ],
)
def test_delivered_notifications_are_silent(tmp_path: pathlib.Path, entries: list[dict]) -> None:
    """`dequeue`または`remove`で配送済みの通知は案内しない。"""
    transcript = _write_transcript(tmp_path, entries)

    assert _evaluate(transcript) == ("approve", "")


def test_notification_without_output_file_shows_task_id_only(tmp_path: pathlib.Path) -> None:
    """出力ファイルを持たない通知はtask-idだけを示す。"""
    transcript = _write_transcript(tmp_path, [_queue("enqueue", _notification(output_file=None))])

    decision, body = _evaluate(transcript)

    assert decision == "notify"
    assert "- task-id: b6n4gipz5\n" in body + "\n"
    assert "出力ファイル: " not in body


def test_missing_transcript_path_is_silent() -> None:
    """transcriptを持たない入力では案内しない。"""
    assert subject.evaluate(json.dumps({"session_id": "queued-session"})) == ("approve", "")
