"""`improvement_status_advisor`が、原因の未調査を理由にした`不要`の報告行だけへ非遮断の通知を返すことを確かめる。"""

import json

import pytest

from agent_toolkit._hooks import improvement_status_advisor, stop


def _payload(text: str, **extra: object) -> str:
    return json.dumps({"session_id": "improvement-status", "last_assistant_message": text, **extra}, ensure_ascii=False)


@pytest.mark.parametrize(
    "text",
    [
        "気付いた改善点: 再現しない失敗（AWI化ステータス: 不要 原因が未調査で、"
        "このセッションでは再現条件を特定できないため。必要なら調査する）",
        "- 気付いた改善点: A（AWI化ステータス: 不要 見送り 原因を調べていないため）",
    ],
)
def test_uninvestigated_reason_is_notified(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """観測した報告行と区分名付きの未調査理由へ、最小調査か引き継ぎを促す通知を返す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    decision, body = improvement_status_advisor.evaluate(_payload(text))
    assert decision == "notify"
    assert 'kind="warn"' in body
    assert "最小調査" in body
    assert "引き継げ" in body


@pytest.mark.parametrize(
    "text",
    [
        "気付いた改善点: A（AWI化ステータス: 不要 処置済み abc1234。原因は未確定だが同じ入力で再現しない）",
        "気付いた改善点: 終了証拠の再現条件（AWI化ステータス: 不要 被覆 原因は未確定だが、"
        "既存AWI 20261106-174207-001.mdへ観測と調査を引き継ぎ済み）",
        "気付いた改善点: A（AWI化ステータス: 不要 非欠陥 未調査を理由に見送らず、調査で仕様どおりと確認した）",
        "気付いた改善点: A（AWI化ステータス: 予定 認可待ち 原因が未調査）",
        "気付いた改善点: A（AWI化ステータス: 済 20261106-000000-001.md）",
        "```text\n気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）\n```",
        "> 気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）",
        "AWI化ステータス: 不要 原因が未調査（報告行の外の言及）",
    ],
)
def test_other_reports_examples_and_quotes_are_not_notified(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """処置を定めた理由、未調査を理由にしない説明、他の値、例と引用、報告行の外には通知しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert improvement_status_advisor.evaluate(_payload(text)) == ("", "")


def test_subagent_and_delegated_session_are_not_notified(monkeypatch: pytest.MonkeyPatch) -> None:
    """委譲先は改善点を委譲元へ返すため、委譲先では判定しない。"""
    text = "気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）"
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert improvement_status_advisor.evaluate(_payload(text, agent_id="subagent-1")) == ("", "")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    assert improvement_status_advisor.evaluate(_payload(text)) == ("", "")


def test_stop_aggregates_notice_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopの集約は通知を文脈へ渡し、ターンの終了を遮断しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("improvement_status_advisor",))
    result = stop.evaluate(_payload("気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）"))
    assert "decision" not in result
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    context = output["additionalContext"]
    assert "原因を調べていない" in context
