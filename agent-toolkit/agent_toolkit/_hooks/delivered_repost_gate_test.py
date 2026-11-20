"""送達後の終了応答と、未送達の本文への切替をStop入力で確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import stop

_SEND_TO_USER = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
_HUMAN = {"type": "user", "origin": {"kind": "human"}, "message": {"content": "進捗を教えて"}}
_SEND = {
    "type": "assistant",
    "message": {"content": [{"type": "tool_use", "id": "s1", "name": _SEND_TO_USER, "input": {"message": "進捗は順調です。"}}]},
}


def _result(identifier: str = "s1", *, error: bool = False) -> dict:
    block = {
        "type": "tool_result",
        "tool_use_id": identifier,
        "content": "対応画面なし" if error else "表示した",
        "is_error": error,
    }
    return {"type": "user", "message": {"content": [block]}}


def _text(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


@pytest.fixture(autouse=True)
def _main_session(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "AGENT_TOOLKIT_HANDOFF_PATH"):
        monkeypatch.delenv(name, raising=False)


def _stop(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    return stop.evaluate(json.dumps({"session_id": "repost-gate", "transcript_path": str(path), **extra}))


@pytest.mark.parametrize(
    "body",
    [
        "Progress is on track. All checks passed.",
        "進捗は順調です。検証も通りました。",
        "要点を再掲します\n検証も通りました",
    ],
)
def test_two_or_more_sentences_after_delivery_are_blocked(body: str, tmp_path: pathlib.Path) -> None:
    """送達成功後の英語・日本語・改行区切りの2文以上の本文は遮断し、`…`だけで閉じるよう求める。"""
    result = _stop([_HUMAN, _SEND, _result(), _text(body)], tmp_path)
    assert result["decision"] == "block"
    assert "…" in str(result["reason"])


@pytest.mark.parametrize("first_body", ["I sent the finished one-sentence notice to the user's screen.", "…"])
def test_reading_group_notice_stop_replay(first_body: str, tmp_path: pathlib.Path) -> None:
    """観測した読書会の会話を再生し、各差し戻しの指示に従って終了するまでを確かめる。"""

    def call(name: str, identifier: str, inputs: dict) -> dict:
        block = {"type": "tool_use", "id": identifier, "name": name, "input": inputs}
        return {"type": "assistant", "message": {"content": [block]}}

    entries = [
        {**_HUMAN, "message": {"content": "読書会の参加者に送る案内を1文で作ってください。"}},
        call(_SEND_TO_USER, "s1", {"message": "読書会の参加者に送る1文の案内を作り始める。先に文体を確認したい。"}),
        _result(),
        call("AskUserQuestion", "q", {"questions": [{"question": "敬体と常体のどちらで書くか？"}]}),
        {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q", "content": "敬体"}]}},
        call(
            _SEND_TO_USER,
            "s2",
            {
                "message": "案内を敬体で仕上げた。\n\n"
                "> 次回の読書会は〇月〇日（〇）〇時から〇〇にて『〇〇』を取り上げますので、皆さまぜひご参加ください。\n\n"
                "日時・場所・書名はまだ聞いていないので「〇〇」にしてある。実際の内容に差し替えて使ってほしい。"
            },
        ),
        _result("s2"),
    ]
    body = first_body
    corrections: list[str] = []
    for _ in range(3):
        entries.append(_text(body))
        result = _stop(entries, tmp_path, last_assistant_message=body, stop_hook_active=bool(corrections))
        if result.get("decision") != "block":
            break
        reason = result["reason"]
        if 'source="response-language"' in reason:
            corrections.append("response-language")
            body = "案内文はユーザーの画面へ送った。敬体の1文で、日時・場所・書名は「〇〇」の仮置きにしてある。"
        else:
            assert 'source="delivered_repost_gate"' in reason and "`…`だけ" in reason
            corrections.append("delivered_repost_gate")
            body = "…"
    assert result.get("decision") != "block"
    assert corrections == ([] if first_body == "…" else ["delivered_repost_gate"])


@pytest.mark.parametrize("body", ["進捗は順調です。", "All checks passed."])
def test_even_one_sentence_after_delivery_is_blocked(body: str, tmp_path: pathlib.Path) -> None:
    assert "delivered_repost_gate" in _stop([_HUMAN, _SEND, _result(), _text(body)], tmp_path)["reason"]


def test_without_successful_delivery_the_body_is_not_judged(tmp_path: pathlib.Path) -> None:
    """送達が無い、または送達が失敗したターンの本文は再掲に当たらない。"""
    two = "進捗は順調です。検証も通りました。"
    assert "decision" not in _stop([_HUMAN, _text(two)], tmp_path)
    assert "decision" not in _stop([_HUMAN, _SEND, _result(error=True), _text(two)], tmp_path)
    assert "decision" not in _stop([_HUMAN, _text(two), _SEND, _result()], tmp_path)


def test_failed_final_delivery_after_progress_can_fall_back_to_text(tmp_path: pathlib.Path) -> None:
    """進捗は表示できても完了報告を表示できなければ、その本文を再掲扱いしない。"""
    report = "検証が終わりました。失敗はありません。"
    call = {"type": "tool_use", "id": "s2", "name": _SEND_TO_USER, "input": {"message": report}}
    send = {"type": "assistant", "message": {"content": [call]}}
    failed = _result("s2", error=True)
    result = _stop([_HUMAN, _SEND, _result(), send, failed, _text(report)], tmp_path)
    assert "decision" not in result


def test_pending_async_work_and_subagent(tmp_path: pathlib.Path) -> None:
    """非同期の待機が残るStopにも適用し、サブエージェントは対象にしない。"""
    entries = [_HUMAN, _SEND, _result(), _text("待機します。結果を待ちます。")]
    background = [{"id": "b1", "type": "shell", "status": "running", "description": "待機"}]
    assert _stop(entries, tmp_path, background_tasks=background)["decision"] == "block"
    assert "decision" not in _stop(entries, tmp_path, agent_id="sub-1")


@pytest.mark.parametrize("command", ["goal", "restart"])
@pytest.mark.parametrize("sent_in_current_turn", [False, True])
def test_originless_command_starts_its_own_delivery_turn(
    command: str, sent_in_current_turn: bool, tmp_path: pathlib.Path
) -> None:
    """Claude Codeのコマンド入力は前ターンの送達を切り離し、今回の送達後の再掲だけを止める。"""
    command_input = {
        "type": "user",
        "promptId": "new-prompt",
        "message": {
            "content": f"<command-name>/{command}</command-name>\n"
            f"<command-message>{command}</command-message>\n<command-args>進捗を教えて</command-args>"
        },
    }
    entries = [_HUMAN, _SEND, _result(), command_input]
    if sent_in_current_turn:
        entries += [_SEND, _result()]
    result = _stop([*entries, _text("今回は作業中です。結果はまだありません。")], tmp_path)
    assert (result.get("decision") == "block") is sent_in_current_turn


@pytest.mark.parametrize(
    "extra",
    [
        {"isMeta": True},
        {"isSidechain": True},
        {"type": "system", "subtype": "local_command"},
        {"origin": {"kind": "machine"}},
        {"message": {"content": "機械側の通知"}},
    ],
)
def test_machine_command_record_does_not_reset_delivery(extra: dict, tmp_path: pathlib.Path) -> None:
    """機械側のコマンド記録やpromptIdの変化で、同じターンの送達を消さない。"""
    command = {
        "type": "user",
        "promptId": "machine-prompt",
        "message": {
            "content": "<command-name>/goal</command-name>\n"
            "<command-message>goal</command-message>\n<command-args>続けて</command-args>"
        },
        **extra,
    }
    result = _stop([_HUMAN, _SEND, _result(), command, _text("送達済みです。検証も通りました。")], tmp_path)
    assert result["decision"] == "block"
