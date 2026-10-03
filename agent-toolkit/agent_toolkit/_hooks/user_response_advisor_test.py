"""人間の発話と本文の受渡しをStopの呼び出しで検証する。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import stop

_HUMAN = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "進捗を報告して"}}
_QUEUED = {"type": "attachment", "attachment": {"type": "queued_command", "prompt": "進捗を報告して"}}


def _assistant(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


def _stop(entries: list[dict], tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], **extra: str) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    result = stop.main(json.dumps({"session_id": "response-case", "transcript_path": str(path), **extra}))
    assert result == 0
    return json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("human", [_HUMAN, _QUEUED])
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
    assert "拡張思考" in output["reason"]
    assert "発話本文として出力" in output["reason"]
    assert "判断の報告・確認結果・回答" in output["reason"]
    assert "内容も含め" in output["reason"]
    assert "受領や状況の説明だけで済ませない" in output["reason"]


@pytest.mark.parametrize("human", [_HUMAN, _QUEUED])
def test_stop_accepts_visible_response_after_human_input(
    human: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """本文を書いた後のツール実行があっても、可視本文の存在を保持する。"""
    output = _stop([human, _assistant("作業は継続中です。"), _assistant("…")], tmp_path, capsys)
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
    output = _stop([_HUMAN, _assistant("受領しました。"), notification, _assistant("…")], tmp_path, capsys)
    assert output.get("decision") != "block"


@pytest.mark.parametrize(
    "entry",
    [
        {"type": "user", "isMeta": True, "message": {"content": "メタ情報"}},
        {"type": "user", "message": {"content": '<atk-auto source="hook" kind="notice">通知</atk-auto>'}},
        {"type": "user", "message": {"content": [{"type": "tool_result", "content": "結果"}]}},
    ],
)
def test_stop_does_not_treat_machine_input_as_human(
    entry: dict, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """人間の発話が無い入力で本文を強制しない。"""
    assert _stop([entry, _assistant("…")], tmp_path, capsys).get("decision") != "block"


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
