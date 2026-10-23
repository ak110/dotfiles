"""原本読取の診断が公開hookの応答とfail-openを保ち、復旧後はquietになることを確かめる。"""

import io
import json
import pathlib
import sys
from typing import Any

import pytest

from agent_toolkit import hook
from agent_toolkit._hooks import antigravity
from agent_toolkit._hooks.antigravity import _Response


@pytest.mark.parametrize("event", ["PreInvocation", "Stop"])
@pytest.mark.parametrize("failure", ["missing", "permission"])
def test_transcript_read_failure_keeps_public_response_and_recovers(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    event: str,
    failure: str,
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    if failure == "permission":
        transcript.write_text("", encoding="utf-8")
    original = pathlib.Path.open
    blocked = True

    def open_path(path: pathlib.Path, *args: Any, **kwargs: Any) -> Any:
        if path == transcript and failure == "permission" and blocked:
            raise PermissionError(13, "読取拒否", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "open", open_path)
    monkeypatch.setattr(antigravity.session_activity, "record_activity", lambda *_args, **_kwargs: None)
    observed: list[tuple[str, dict[str, Any]]] = []

    def invoke(name: str, payload: dict[str, Any], **_kwargs: Any) -> _Response:
        observed.append((name, payload))
        return _Response(0, {}, "")

    monkeypatch.setattr(antigravity, "_invoke", invoke)
    payload = {"conversationId": "warning-transcript", "workspacePaths": [str(tmp_path)], "transcriptPath": str(transcript)}

    def call() -> tuple[dict[str, Any], str]:
        with (
            io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode()), encoding="utf-8") as stdin,
            monkeypatch.context() as scoped,
        ):
            scoped.setattr(sys, "stdin", stdin)
            assert hook.main(["antigravity", event]) == 0
        captured = capsys.readouterr()
        return json.loads(captured.out), captured.err

    expected = {} if event == "PreInvocation" else {"decision": "allow"}
    body, diagnostic = call()
    assert body == expected
    assert "原本を読めない" in diagnostic and str(transcript) in diagnostic
    assert diagnostic.count("次の操作:") == 1
    assert "transcriptPath" in diagnostic and "所在と読取権限" in diagnostic

    blocked = False
    transcript.write_text(
        json.dumps({"type": "USER_INPUT", "step_index": 1, "content": "入力本文"})
        + "\n"
        + json.dumps({"type": "PLANNER_RESPONSE", "content": "応答本文"})
        + "\n",
        encoding="utf-8",
    )
    observed.clear()
    body, diagnostic = call()
    assert body == expected
    assert not diagnostic
    if event == "PreInvocation":
        assert observed[-1][0] == "user_prompt_submit" and observed[-1][1]["prompt"] == "入力本文"
    else:
        assert observed[-1][0] == "stop" and observed[-1][1]["last_assistant_message"] == "応答本文"
