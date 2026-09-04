"""`SubagentStop`の助言フックの構造的な完了判定を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import subagent_stop_advisor as advisor


def _minimal_report() -> str:
    """委譲調整役の構造化された最小完了報告を返す。"""
    return """status: completed
要約: 完了
コミット一覧:
- aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
検証結果:
- pytest: 終了コード0、警告0件
レビュー:
- 実装レビュー担当: 完了
指摘:
- 指摘なし
計画検査: 完了条件を満たす
阻害要因:
- なし
"""


def _payload(report: str = "完了") -> dict[str, object]:
    return {
        "session_id": "session",
        "agent_id": "agent",
        "agent_transcript_path": "/tmp/agent.jsonl",
        "last_assistant_message": report,
    }


def test_current_minimal_executor_report_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps(_payload(_minimal_report()))) == 0
    assert capsys.readouterr().out == ""


def test_nonempty_report_does_not_require_legacy_labels(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps(_payload("完了"))) == 0
    assert capsys.readouterr().out == ""


def test_english_completion_report_is_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    """英語の完了報告も非空なら空stdoutで許可する。"""
    assert advisor.main(json.dumps(_payload("Done here."))) == 0
    assert capsys.readouterr().out == ""


def test_japanese_completion_report_is_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    """日本語の完了報告は空stdoutで許可する。"""
    assert advisor.main(json.dumps(_payload("完了報告を確認した。"))) == 0
    assert capsys.readouterr().out == ""


def test_non_string_report_is_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    """非文字列の完了報告は空stdoutで許可する。"""
    assert advisor.main(json.dumps({**_payload(), "last_assistant_message": {"status": "completed"}})) == 0
    assert capsys.readouterr().out == ""


def test_empty_completion_report_is_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps(_payload("  \n"))) == 0

    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert decision["reason"].startswith(
        "[auto-generated: agent-toolkit/subagent-stop][block] Provide a non-empty completion report"
    )
    assert "Fix: Write a non-empty completion report and stop again." in decision["reason"]


def test_pending_child_with_nonempty_report_is_approved(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """未消化の子起動があっても非空の完了報告だけでSubagentStopを許可する。"""
    transcript = tmp_path / "agent.jsonl"
    entries = [
        {
            "type": "assistant",
            "isSidechain": True,
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "toolu_child", "name": "Agent", "input": {}}],
                "stop_reason": "tool_use",
            },
        },
        {
            "type": "user",
            "isSidechain": True,
            "toolUseResult": {"isAsync": True, "agentId": "child-agent"},
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_child",
                        "content": [{"type": "text", "text": "Async agent launched successfully"}],
                    }
                ],
            },
        },
    ]
    transcript.write_text("".join(f"{json.dumps(entry, ensure_ascii=False)}\n" for entry in entries), encoding="utf-8")

    payload = {**_payload(_minimal_report()), "agent_transcript_path": str(transcript)}
    assert advisor.main(json.dumps(payload, ensure_ascii=False)) == 0
    assert capsys.readouterr().out == ""


def test_recursive_hook_is_approved_with_empty_output(capsys: pytest.CaptureFixture[str]) -> None:
    """再帰呼び出し時の許可は両ホスト共通で空stdoutとする。"""
    assert advisor.main(json.dumps({"stop_hook_active": True})) == 0
    assert capsys.readouterr().out == ""


def test_codex_empty_report_is_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    """Codexでも空の完了報告は遮断する。"""
    assert advisor.main(json.dumps({**_payload("   "), "turn_id": "turn-1"})) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "block"


def test_codex_normal_report_is_allowed(capsys: pytest.CaptureFixture[str]) -> None:
    """Codexの非空報告は空stdoutで許可する。"""
    assert advisor.main(json.dumps({**_payload(_minimal_report()), "turn_id": "turn-1"})) == 0
    assert capsys.readouterr().out == ""


def test_invalid_payload_fails_open(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main("not json") == 0
    assert advisor.main("[]") == 0
    assert capsys.readouterr().out == ""
