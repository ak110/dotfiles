"""投入作業の報告用要約がadd-awi-by-userの最終応答にあるかを、Stopの公開入口で検証する。

要約を欠いた作業完了報告にだけ再報告を求め、要約がある報告、作成後の新しいユーザー発話、
要約の無い別の成果作業と中止では終了を妨げないことを確かめる。
"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._common import session_activity
from agent_toolkit._hooks import stop, submission_report_advisor

_SESSION = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_LINES = [
    "指摘の分類と処置:",
    "- 確認の往復: 2回",
    "- 対象リポジトリを外した: 考慮漏れ（再発防止: 採用、登録先: 20261106-000000-001.md）",
]


def _later(seconds: int) -> str:
    return (datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=seconds)).isoformat()


def _transcript(tmp_path: pathlib.Path, entries: list[dict[str, Any]]) -> pathlib.Path:
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return path


def _send_to_user(message: str, *, seconds: int = 30) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": _later(seconds),
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "name": "mcp__plugin_agent-toolkit_agent-toolkit__send_to_user",
                    "input": {"message": message},
                }
            ],
        },
    }


def _payload(transcript: pathlib.Path, last: str = "…") -> str:
    return json.dumps({"session_id": _SESSION, "transcript_path": str(transcript), "last_assistant_message": last})


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION"):
        monkeypatch.delenv(name, raising=False)


def test_missing_summary_line_blocks_with_missing_lines(tmp_path: pathlib.Path) -> None:
    """send_to_userの作業完了報告が要約の一部を欠くと、欠けた行と再報告の方法を返して要約を保持する。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    transcript = _transcript(tmp_path, [_send_to_user("## 作業完了報告\n\n- 登録: 1件\n指摘の分類と処置:\n- 確認の往復: 2回")])

    decision, reason = submission_report_advisor.evaluate(_payload(transcript))

    assert decision == "block"
    assert _LINES[2] in reason
    assert _LINES[1] not in reason
    assert "再報告" in reason
    assert session_activity.read_submission_report(_SESSION) is not None


def test_stop_entry_returns_block_reason(tmp_path: pathlib.Path) -> None:
    """Stopの集約入口も同じ欠落でターンを続けさせる。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    transcript = _transcript(tmp_path, [_send_to_user("作業完了報告")])

    result = stop.evaluate(_payload(transcript))

    assert result.get("decision") == "block"
    assert _LINES[2] in str(result.get("reason"))


@pytest.mark.parametrize("delivery", ["send_to_user", "last_message"])
def test_reported_summary_approves_and_clears(tmp_path: pathlib.Path, delivery: str) -> None:
    """要約の全行が改行・空白の違いを除いて報告にあれば終了を許し、要約を消す。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    body = "## 作業完了報告\n\n" + "\n".join(f"  {line}  " for line in _LINES)
    entries = [_send_to_user(body)] if delivery == "send_to_user" else []
    transcript = _transcript(tmp_path, entries)

    decision, _reason = submission_report_advisor.evaluate(_payload(transcript, body if delivery == "last_message" else "…"))

    assert decision == "approve"
    assert session_activity.read_submission_report(_SESSION) is None


def test_report_before_summary_creation_does_not_count(tmp_path: pathlib.Path) -> None:
    """要約を作る前の途中報告は検査に使わない。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    transcript = _transcript(tmp_path, [_send_to_user("\n".join(_LINES), seconds=-60)])

    assert submission_report_advisor.evaluate(_payload(transcript))[0] == "block"


def test_intermediate_assistant_text_does_not_count(tmp_path: pathlib.Path) -> None:
    """要約の作成後の途中の地の文に要約があっても、最終応答とsend_to_userに無ければ再報告を求める。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    intermediate = {
        "type": "assistant",
        "timestamp": _later(30),
        "message": {"role": "assistant", "content": [{"type": "text", "text": "\n".join(_LINES)}]},
    }
    transcript = _transcript(tmp_path, [intermediate])

    assert submission_report_advisor.evaluate(_payload(transcript, "登録完了。"))[0] == "block"
    assert session_activity.read_submission_report(_SESSION) is not None


def _codex_payload(transcript: pathlib.Path, last: str) -> str:
    return json.dumps(
        {"session_id": _SESSION, "turn_id": "turn-1", "transcript_path": str(transcript), "last_assistant_message": last}
    )


def _codex_user_message(message: str, *, seconds: int) -> dict[str, Any]:
    return {"timestamp": _later(seconds), "type": "event_msg", "payload": {"type": "user_message", "message": message}}


def test_codex_stop_checks_last_message(tmp_path: pathlib.Path) -> None:
    """CodexのStopでも最終応答に要約が無ければ再報告を求め、あれば終了を許して要約を消す。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    transcript = _transcript(tmp_path, [])

    missing = stop.evaluate(_codex_payload(transcript, "登録完了。"))
    assert missing.get("decision") == "block"
    assert _LINES[2] in str(missing.get("reason"))

    reported = stop.evaluate(_codex_payload(transcript, "## 作業完了報告\n\n" + "\n".join(_LINES)))
    assert reported.get("decision") != "block"
    assert session_activity.read_submission_report(_SESSION) is None


def test_codex_new_user_message_expires_summary(tmp_path: pathlib.Path) -> None:
    """Codexでも要約の作成後の人間の発話で要約を消し、機械注入は新しい発話に数えない。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    injected = _codex_user_message('<atk-auto source="agent-toolkit" kind="notice">\n通知\n</atk-auto>', seconds=10)
    transcript = _transcript(tmp_path, [injected])
    assert submission_report_advisor.evaluate(_codex_payload(transcript, "…"))[0] == "block"

    transcript = _transcript(tmp_path, [injected, _codex_user_message("次の依頼", seconds=20)])
    assert submission_report_advisor.evaluate(_codex_payload(transcript, "…"))[0] == "approve"
    assert session_activity.read_submission_report(_SESSION) is None


def _submission(*arguments: str) -> None:
    with pytest.raises(SystemExit) as exc:
        atk.main(["wi", "submission", *arguments])
    assert exc.value.code == 0


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_saved_summary_from_end_reaches_stop_on_each_host(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    """正常な`atk wi submission end`が保存した要約を、各ホストのStop入力で検査し、要約を欠く最終応答だけに再報告を求める。"""
    home = tmp_path / "claude"
    (home / "projects" / "project").mkdir(parents=True)
    (home / "projects" / "project" / f"{_SESSION}.jsonl").write_text("", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    findings = tmp_path / "findings.json"
    findings.write_text(json.dumps({"confirmation_rounds": 1, "entries": []}), encoding="utf-8")
    _submission("begin", "--session-id", _SESSION, "--target-repo", "github.com/example/myrepo")
    _submission("end", "--session-id", _SESSION, "--findings-file", str(findings))
    summary = capsys.readouterr().out
    transcript = _transcript(tmp_path, [])

    def payload(last: str) -> str:
        return _codex_payload(transcript, last) if host == "codex" else _payload(transcript, last)

    missing = stop.evaluate(payload("登録完了。"))
    assert missing.get("decision") == "block"
    assert "- 指摘: なし" in str(missing.get("reason"))

    reported = stop.evaluate(payload(f"## 作業完了報告\n\n{summary}"))
    assert reported.get("decision") != "block"
    assert session_activity.read_submission_report(_SESSION) is None


def test_new_user_prompt_expires_summary(tmp_path: pathlib.Path) -> None:
    """要約の作成後にユーザーの新しい発話があれば、検査せずに要約を消す。機械注入は新しい発話に数えない。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    injected = {
        "type": "user",
        "timestamp": _later(10),
        "message": {"role": "user", "content": '<atk-auto source="agent-toolkit" kind="notice">\n通知\n</atk-auto>'},
    }
    transcript = _transcript(tmp_path, [injected])
    assert submission_report_advisor.evaluate(_payload(transcript))[0] == "block"

    human = {"type": "user", "timestamp": _later(20), "message": {"role": "user", "content": "次の依頼"}}
    transcript = _transcript(tmp_path, [injected, human])
    assert submission_report_advisor.evaluate(_payload(transcript))[0] == "approve"
    assert session_activity.read_submission_report(_SESSION) is None


def test_without_summary_other_work_is_not_judged(tmp_path: pathlib.Path) -> None:
    """要約の無い会話（別の成果作業、中止）と委譲先では判定しない。"""
    transcript = _transcript(tmp_path, [_send_to_user("別の作業の報告")])
    assert submission_report_advisor.evaluate(_payload(transcript)) == ("approve", "")


def test_delegated_session_is_not_judged(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """委譲先は作業完了報告をユーザーへ送らないため判定しない。"""
    session_activity.record_submission_report(_SESSION, _LINES)
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    transcript = _transcript(tmp_path, [])
    assert submission_report_advisor.evaluate(_payload(transcript)) == ("approve", "")
