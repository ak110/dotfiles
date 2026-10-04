"""報告の直接発話をStopへ与え、報告本文の判定だけが遮断に作用することを確かめる。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import report_validation, stop
from agent_toolkit._hooks.termination_evidence_test import isolated_session  # noqa: F401  # pylint: disable=unused-import


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    ("review", "submission", "expected"),
    [
        ("", "", "block"),
        ("## 振り返り結果報告\n\n言い回しは任意。", "", "approve"),
        ("## 振り返り結果報告\n### 確定した問題と対策\n- 対策（投入予定: 対策のタイトル）", "", "block"),
        (
            "## 振り返り結果報告\n### 確定した問題と対策\n- 対策（投入予定: 対策のタイトル）",
            "## AWI投入結果報告\n- 20261004-003316-001.md",
            "approve",
        ),
    ],
)
def test_stop_uses_visible_headings_for_each_host(
    tmp_path: pathlib.Path, host: str, review: str, submission: str, expected: str
) -> None:
    """作業報告・予告の後は不足段階だけを要求し、直接発話すると解除する。"""
    path = tmp_path / "transcript.jsonl"
    texts = ["## 作業完了報告\n完了した。", review, submission]
    entries = []
    for text in texts:
        if not text:
            continue
        entries.append(
            {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}
            if host == "claude"
            else {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "channel": "final",
                    "content": [{"type": "output_text", "text": text}],
                },
            }
        )
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries) + "\n", encoding="utf-8")
    payload = {"session_id": "evidence-test", "transcript_path": str(path), "stop_hook_active": False}
    if host == "codex":
        payload["turn_id"] = "codex-turn"
    result = stop.evaluate(json.dumps(payload))
    assert result.get("decision", "approve") == expected
    reason = str(result.get("reason", ""))
    assert "completion-report-check" not in reason


@pytest.mark.parametrize("host", ["claude", "codex"])
@pytest.mark.parametrize(
    "body",
    [
        "### 確定した問題と対策\n- 対策の説明だけ",
        "### 対策を見送った問題\n- 判定済み: 問題; 根拠: 未照会なので分からない",
        "### 対策を見送った問題\n- 判定済み: 問題",
        "### 対策を見送った問題\n- 未確定: 問題; 照会: 実施; 再現: 実施",
    ],
)
def test_stop_rejects_required_evidence_violation(host: str, body: str) -> None:
    """違反した行と直し方を両ホストのStopへ返す。"""
    text = "## 作業完了報告\n完了。\n\n## 振り返り結果報告\n" + body
    payload = {"session_id": "evidence-test", "last_assistant_message": text, "stop_hook_active": False}
    if host == "codex":
        payload["turn_id"] = "codex-turn"
    result = stop.evaluate(json.dumps(payload))
    assert result["decision"] == "block"
    reason = str(result["reason"])
    assert body.splitlines()[-1] in reason and "直接発話" in reason


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_submission_rejects_scheduled_marker(host: str) -> None:
    text = "## 振り返り結果報告\n結果。\n## AWI投入結果報告\n- （投入予定: 対策）"
    payload = {"session_id": "evidence-test", "last_assistant_message": text, "stop_hook_active": False}
    if host == "codex":
        payload["turn_id"] = "codex-turn"
    result = stop.evaluate(json.dumps(payload))
    assert result["decision"] == "block" and "最終報告の投入予定" in str(result["reason"])


@pytest.mark.parametrize("reference", ["20261004-003316-001.md", "（投入予定: 対策）", "（同一セッションで実装済み: abc1234）"])
def test_accepts_measure_reference_and_tolerates_layout(reference: str) -> None:
    """H3順序・要約の接頭辞・なし併記・未実施理由と内部パスは追加の判定にしない。"""
    text = (
        "## 振り返り結果報告\n- 別の要約: 任意\n- 成果ファイル: /some/path\n"
        "### 対策を見送った問題\n- なし\n- 判定済み: 問題; 根拠: 同じ条件で確認済み\n"
        "- 未確定: 問題; 照会: 原記録を照会; 再現: 同じ条件を実行; 残る理由: 外部応答の欠落\n"
        "### 確定した問題と対策\n- 対策 " + reference
    )
    assert not report_validation.validate_report(text, "review-result")


def test_code_fence_heading_is_not_report() -> None:
    assert not report_validation.reports_from_messages(["````markdown\n## 作業完了報告\n````\n"])


def test_markdown_heading_and_bullet_variants_preserve_evidence_check() -> None:
    """見出しの閉じ記号・別の箇条書き記号でも同じ意味の報告本文の判定を適用する。"""
    text = "## 振り返り結果報告\n### 対策を見送った問題 ###\n* 判定済み: 問題; 根拠: 未確定\n"
    assert report_validation.validate_report(text, "review-result")
