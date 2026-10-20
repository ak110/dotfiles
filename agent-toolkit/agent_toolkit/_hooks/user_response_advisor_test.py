"""人間の発話と本文の受渡しをStopの呼び出しで検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import stop

_HUMAN = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "進捗を報告して"}}
_QUEUED = {
    "type": "attachment",
    "attachment": {"type": "queued_command", "prompt": "進捗を報告して", "origin": {"kind": "human"}},
}


def _assistant(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


@pytest.mark.parametrize("api_error,model", [(True, "claude"), (False, "<synthetic>")])
def test_synthetic_api_notice_does_not_answer_human(
    api_error: bool, model: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Code 2.1.303のAPI失敗エントリは人間への回答へ数えない。"""
    message = {"model": model, "content": [{"type": "text", "text": "Credit balance is too low"}]}
    synthetic = {"type": "assistant", "message": message, "isApiErrorMessage": api_error}
    output = _stop([_HUMAN, synthetic], tmp_path, capsys)
    assert output["decision"] == "block"
    assert 'source="user_response_advisor"' in output["reason"]


def _stop(entries: list[dict], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], **extra: str) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    result = stop.main(json.dumps({"session_id": "response-case", "transcript_path": str(path), **extra}))
    assert result == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize(
    "human",
    [_HUMAN, _QUEUED, {**_HUMAN, "message": {"content": "<command-message>スキル起動</command-message>"}}],
)
@pytest.mark.parametrize("text", ["", " \n\t", "…", " … … \n"])
def test_stop_blocks_unanswered_human_input(
    human: dict, text: str, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """入力より前の本文や、思考・ツール結果を応答として数えない。"""
    entries = [
        _assistant("前の発話への回答"),
        human,
        {"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "回答のつもり"}]}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "結果"}]}},
        _assistant(text),
    ]
    output = _stop(entries, tmp_path, capsys)
    assert output["decision"] == "block"
    assert 'source="user_response_advisor"' in output["reason"]
    assert "mcp__agent-toolkit__send_to_user" in output["reason"]
    assert "回答・判断・確認結果" in output["reason"]
    assert "無い環境では応答本文" in output["reason"]


@pytest.mark.parametrize("human", [_HUMAN, _QUEUED])
def test_stop_accepts_visible_response_after_human_input(
    human: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """本文を書いた後のツール実行があっても、可視本文の存在を保持する。"""
    output = _stop([human, _assistant("作業は継続中です。"), _assistant("…")], tmp_path, capsys)
    assert output.get("decision") != "block"


@pytest.mark.parametrize("human", [_HUMAN, _QUEUED])
def test_stop_accepts_send_to_user_message_after_human_input(
    human: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """send_to_userの`message`はユーザーの画面へ届く本文であり、応答として数える。"""
    call = {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "mcp__agent-toolkit__send_to_user",
        "input": {"message": "原因はAだった。"},
    }
    output = _stop([human, {"type": "assistant", "message": {"content": [call]}}, _assistant("…")], tmp_path, capsys)
    assert output.get("decision") != "block"


@pytest.mark.parametrize(
    "notification",
    [
        {"type": "user", "message": {"content": "<task-notification>終了</task-notification>"}},
        {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "<task-notification>終了"}},
    ],
)
def test_stop_accepts_task_notification_wait_turn(
    notification: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """完了通知を最新入力とする待機の回を人間の新しい発話として扱わない。"""
    output = _stop([_HUMAN, notification, _assistant("…")], tmp_path, capsys)
    assert output.get("decision") != "block"


@pytest.mark.parametrize(
    "entry",
    [
        {**_HUMAN, "isMeta": True, "message": {"content": "メタ情報"}},
        {**_HUMAN, "message": {"content": '<atk-auto source="hook" kind="notice">通知</atk-auto>'}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "結果"}]}},
    ],
)
def test_stop_does_not_treat_machine_input_as_human(
    entry: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """人間の発話が無い入力で本文を強制しない。"""
    assert _stop([entry, _assistant("…")], tmp_path, capsys).get("decision") != "block"


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "user", "message": {"content": "/compact"}},
        {"type": "user", "isCompactSummary": True, "message": {"content": "This session is being continued"}},
        {"type": "user", "message": {"content": "<command-name>/goal</command-name>"}},
        {"type": "user", "message": {"content": "<local-command-stdout>結果</local-command-stdout>"}},
        {
            "type": "attachment",
            "attachment": {"type": "queued_command", "origin": {"kind": "peer"}, "prompt": "<agent-message>完了"},
        },
        {
            "type": "attachment",
            "attachment": {"type": "queued_command", "origin": {"kind": "auto-continuation"}, "prompt": "継続"},
        },
        {"type": "user", "message": {"content": "This skill can only be invoked by Claude"}},
        {"type": "user", "entrypoint": "sdk-py", "promptSource": "sdk", "message": {"content": "調査して"}},
    ],
    ids=["compact", "summary", "command", "stdout", "peer", "continuation", "skill-refusal", "sdk"],
)
def test_stop_accepts_scheduled_turn_after_machine_input(
    entry: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude Code 2.1.250～2.1.295のtranscriptで観測した記録形を使う。"""
    scheduled = {"type": "user", "isMeta": True, "scheduledTaskId": "scheduled-case", "message": {"content": "再確認"}}
    output = _stop([entry, scheduled, _assistant("…")], tmp_path, capsys)
    assert 'source="user_response_advisor"' not in output.get("reason", "")


def test_stop_does_not_require_delegate_to_answer_user(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Agentとagents_serverの委譲先を既存の主体判定で除く。"""
    output = _stop([_HUMAN, _assistant("…")], tmp_path, capsys, agent_id="agent-1")
    assert 'source="user_response_advisor"' not in output.get("reason", "")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    output = _stop([_HUMAN, _assistant("…")], tmp_path, capsys)
    assert 'source="user_response_advisor"' not in output.get("reason", "")


def test_new_human_input_requires_a_new_visible_response(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """前の人間の発話への回答を、途中配送された次の発話の回答へ流用しない。"""
    output = _stop([_HUMAN, _assistant("前の発話への回答"), _QUEUED, _assistant("…")], tmp_path, capsys)
    assert output["decision"] == "block"


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize("prompt", ["<task-notification>を説明して", "通知の例は<task-notification>です"])
def test_human_notification_quote_still_requires_response(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, queued: bool, prompt: str
) -> None:
    """通知タグを引用した人間入力も、由来の印に従って応答を要求する。"""
    entry = (
        {"type": "attachment", "attachment": {"type": "queued_command", "origin": {"kind": "human"}, "prompt": prompt}}
        if queued
        else {**_HUMAN, "message": {"content": prompt}}
    )
    assert _stop([entry, _assistant("…")], tmp_path, capsys)["decision"] == "block"


def _said(prompt: str) -> dict:
    return {**_HUMAN, "message": {"content": prompt}}


_TOOL_CALL = {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}]}}
_TOOL_RESULT = {"type": "user", "message": {"content": [{"type": "tool_result", "content": "結果"}]}}
_QUEUED_OK = {
    "type": "attachment",
    "attachment": {"type": "queued_command", "prompt": "OK", "origin": {"kind": "human"}},
}


@pytest.mark.parametrize(
    "human",
    [
        _said("OK"),
        _said("ok"),
        _said("OK!"),
        _said("ＯＫ"),
        _said("了解"),
        _QUEUED_OK,
        _said("わかりました"),
        _said("OKです"),
        _said("いいよ"),
        _said("はい、了解です"),
        _said("了解👍"),
    ],
    ids=["OK", "ok", "OK!", "fullwidth", "了解", "queued", "わかりました", "OKです", "いいよ", "連結", "絵文字"],
)
def test_stop_accepts_ellipsis_after_acknowledgement(
    human: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """単純な了承には伝える回答が無く、ツールを呼ばずに`…`だけで終える規範どおりのStopを遮断しない。

    了承は構成規則で判定するため、`わかりました`・`OKです`のように例示した語以外の言い回しも受理する。
    """
    output = _stop([_assistant("前の回答"), human, _assistant("…")], tmp_path, capsys)
    assert 'source="user_response_advisor"' not in output.get("reason", "")


@pytest.mark.parametrize(
    "entries",
    [
        [_said("OK"), _assistant("")],
        [_said("OK"), _TOOL_CALL, _TOOL_RESULT, _assistant("…")],
        [_said("OK?"), _assistant("…")],
        [_said("OK、あとXも直して"), _assistant("…")],
        [_said("進捗を報告して"), _said("OK"), _assistant("…")],
    ],
    ids=["empty", "tool-after-ack", "question", "request-after-ack", "mixed-inputs"],
)
def test_stop_blocks_acknowledgement_that_still_requires_response(
    entries: list[dict], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """本文が空、了承の後のツール呼び出し、問い・依頼を含む入力、了承でない入力の混在では本文を求める。"""
    output = _stop([_assistant("前の回答"), *entries], tmp_path, capsys)
    assert output["decision"] == "block"
    assert 'source="user_response_advisor"' in output["reason"]
