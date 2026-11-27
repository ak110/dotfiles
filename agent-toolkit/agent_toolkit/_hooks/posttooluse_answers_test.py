# pylint: disable=protected-access
"""posttooluse_answersの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.posttooluse_support import _ask_user_question_payload, _run

_FREE_TEXT = "process-loopが動いてるからそっちでマージされるはず"


_QUESTION = "Issue対応の回までマージを待ちますか？"


_MULTI_QUESTION = "どの対応を進めますか？"


_LABELS = ("待つ (Recommended)", "今すぐマージする")


@pytest.mark.parametrize(
    "tool_response",
    [
        pytest.param({}, id="labels-only"),
        pytest.param({"answers": {_QUESTION: _FREE_TEXT}, "afkTimeoutMs": 60000}, id="afk-timeout"),
        pytest.param(
            {"answers": {_QUESTION: "(notes only)"}, "annotations": {_QUESTION: {"preview": "案"}}}, id="preview-only"
        ),
    ],
)
def test_answer_without_user_text_receives_no_notice(tmp_path: pathlib.Path, tool_response: dict) -> None:
    result = _run(_ask_user_question_payload("ask-user-question-labels", tool_response), state_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


# test-copy-contract: 回答欄4種から抽出した本文の例示・調査・引用に応じて通知を選択し、感嘆符では通知しない契約。
@pytest.mark.parametrize("field", ["response", "answers", "annotations", "multi"])
@pytest.mark.parametrize(
    "text",
    [
        "違います！！",
        "例えば設定などを調査して",
        "例えば設定, 調査して",
        "`例えば調査`は引用です",
        "全体が分かりにくい。例えば表示順を簡潔にすべき",
    ],
)
def test_free_text_content_notices_match_prompt(tmp_path: pathlib.Path, field: str, text: str) -> None:
    responses: dict[str, dict[str, object]] = {
        "response": {"response": text},
        "answers": {"answers": {_QUESTION: text}},
        "annotations": {"annotations": {_QUESTION: {"notes": text}}},
        "multi": {"answers": {_MULTI_QUESTION: f"{_LABELS[0]}, {text}"}},
    }
    response = responses[field]
    result = _run(_ask_user_question_payload("content-notices", response), state_dir=tmp_path)
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "事実の主張か是正を求める対象を含む場合がある" not in context
    assert "この注記は`AskUserQuestion`" not in context
    assert "playbooks/user-utterance.md" in context
    assert "「割り込み・訂正・中止」" in context
    assert "「発話の理解」" in context
    assert "調査より先に" in context
    assert "伝えてください" in context and "`agent-toolkit:atk-mode/references/delivery.md`「手段の選択」" in context
    assert "認識合わせ" not in context
    has_example = not text.startswith("`") and "例えば" in text
    assert ("「原文の保持と取り込み」" in context) == has_example
    assert ("`explore`へ分担する案" in context) == (has_example and "調査" in text)


# test-copy-contract: 同一回答の再送は抑止し、別の質問・回答・呼出IDでは通知を再び1回だけ配送する契約。
@pytest.mark.parametrize("changed_field", ["tool_use_id", "question", "answer"])
def test_free_text_notices_distinguish_replays_questions_and_answers(tmp_path: pathlib.Path, changed_field: str) -> None:
    payload = _ask_user_question_payload("free-text-notice-target", {"response": "例えば設定を調査して！！"})
    payload["tool_use_id"] = "question-call-1"
    payload["tool_input"] = {"questions": payload["tool_response"]["questions"]}

    def context(event: dict) -> str:
        result = _run(event, state_dir=tmp_path)
        assert result.returncode == 0, result.stderr
        if not result.stdout:
            return ""
        return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]

    first = context(payload)
    for marker in ("自由記述の回答", "原文の対象と例示の関係"):
        assert marker in first
    assert "認識合わせ" not in first
    assert not context(payload)

    changed = json.loads(json.dumps(payload))
    if changed_field == "tool_use_id":
        changed["tool_use_id"] = "question-call-2"
    elif changed_field == "question":
        question = "別の対応について回答しますか？"
        changed["tool_input"]["questions"][0]["question"] = question
        changed["tool_response"]["questions"][0]["question"] = question
        changed["tool_response"]["answers"][question] = changed["tool_response"]["answers"].pop(_QUESTION)
    else:
        changed["tool_response"]["response"] = "例えば実装を調査して！！"
    assert context(changed) == first
    assert not context(changed)
