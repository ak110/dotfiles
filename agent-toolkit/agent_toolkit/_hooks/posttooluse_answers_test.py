# pylint: disable=protected-access
"""posttooluse_answersの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._testing.helpers import _read_state
from agent_toolkit._testing.posttooluse_support import _ask_user_question_payload, _run

_FREE_TEXT = "process-loopが動いてるからそっちでマージされるはず"


_QUESTION = "Issue対応の回までマージを待ちますか？"


_MULTI_QUESTION = "どの対応を進めますか？"


_LABELS = ("待つ (Recommended)", "今すぐマージする")


@pytest.mark.parametrize("codex", [False, True])
@pytest.mark.parametrize(
    "command",
    [
        "git commit -m message",
        "git commit --amend --no-edit",
        "git -C /repo push",
        "git commit -m message && git push",
        "git commit -n -m message",
        "git commit -m --help",
        "for item in one; do git commit -m message; done",
        "if test -f marker; then git commit -m message; elif test -d marker; then git push; fi",
        "case value in one) git commit -m message;; esac",
        "time for item in one; do git commit -m message; done",
        "! if test -f marker; then git push; fi",
        "time -p { git commit -m message; }",
    ],
)
def test_git_completion_notice_distinguishes_retries_commits_and_targets(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, command: str, codex: bool
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    payload = {
        "session_id": "completion-notice",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "tool_response": {"exit_code": 0},
    }
    if codex:
        payload["turn_id"] = "codex-turn"
    for index in range(2):
        result = _run(payload, state_dir=tmp_path)
        assert result.returncode == 0
        if index:
            assert not result.stdout
            continue
        body = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        assert body.count("報告を書く前に") == 1
        assert "report-completion" in body
        assert ("SKILL.md" in body) is codex
    payload["tool_response"] = {"exit_code": 0, "stdout": "[main 1234567] 次のcommit"}
    changed_commit = _run(payload, state_dir=tmp_path)
    assert changed_commit.returncode == 0
    assert "report-completion" in json.loads(changed_commit.stdout)["hookSpecificOutput"]["additionalContext"]
    assert not _run(payload, state_dir=tmp_path).stdout
    payload["cwd"] = str(tmp_path / "other-repository")
    changed_target = _run(payload, state_dir=tmp_path)
    assert changed_target.returncode == 0
    assert "report-completion" in json.loads(changed_target.stdout)["hookSpecificOutput"]["additionalContext"]
    assert not any("completion" in key for key in _read_state(tmp_path, "completion-notice"))


@pytest.mark.parametrize(
    "case",
    [
        "failure",
        "nonzero",
        "background",
        "moved-background",
        "shell-background",
        "coproc",
        "named-coproc",
        "agent",
        "delegate",
        "dry-run",
        "help",
        "global-help",
        "text",
    ],
)
def test_git_completion_notice_excludes_non_completion_operations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    commands = {
        "shell-background": "git push &",
        "coproc": "coproc git push",
        "named-coproc": "coproc WORKER if test -f marker; then git push; fi",
        "dry-run": "git push --dry-run",
        "help": "git commit --help",
        "global-help": "git --help push",
        "text": "echo 'git push'",
    }
    payload: dict = {
        "session_id": "excluded-notice",
        "hook_event_name": "PostToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": commands.get(case, "git push")},
        "tool_response": {"exit_code": 0},
    }
    if case == "failure":
        payload["hook_event_name"] = "PostToolUseFailure"
    elif case == "nonzero":
        payload["tool_response"] = {"exit_code": 1}
    elif case == "background":
        payload["tool_input"] = {"command": "git push", "run_in_background": True}
    elif case == "moved-background":
        payload["tool_response"] = {"backgroundTaskId": "task-1"}
    elif case == "agent":
        payload["agent_id"] = "child-1"
    elif case == "delegate":
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    result = _run(payload, state_dir=tmp_path)
    assert result.returncode == 0
    assert "report-completion" not in result.stdout


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
    """提示した`label`だけの回答と、ユーザーが応答せずに閉じた回答には注記を返さない。"""
    result = _run(_ask_user_question_payload("ask-user-question-labels", tool_response), state_dir=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


# test-copy-contract: 回答欄4種から抽出した本文の強調・例示・調査・引用に応じて通知を選択する契約。
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
    """自由記述の配送欄によらず、通常発話と同じ内容注記が届く。

    例示の語があれば調査の語が無い提案にも範囲判断の節を示し、調査の語も含む場合だけ手順1の調査の所在を加える。
    """
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
    assert "references/user-utterance-correction.md" in context
    assert "「割り込み・訂正・中止」" in context
    assert ("認識合わせ" in context) == ("！！" in text)
    has_example = not text.startswith("`") and "例えば" in text
    assert ("「原文の保持と取り込み」" in context) == has_example
    assert ("範囲語か開放列挙" in context) == (has_example and "調査" in text)


# test-copy-contract: 同一回答の再送は抑止し、別の質問・回答・呼出IDでは通知を再び1回だけ配送する契約。
@pytest.mark.parametrize("changed_field", ["tool_use_id", "question", "answer"])
def test_free_text_notices_distinguish_replays_questions_and_answers(tmp_path: pathlib.Path, changed_field: str) -> None:
    """同一回答の再配送を抑え、別の質問・自由回答へ必要な手順と内容注記を届ける。"""
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
    for marker in ("自由記述の回答", "認識合わせ", "範囲語か開放列挙"):
        assert marker in first
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
