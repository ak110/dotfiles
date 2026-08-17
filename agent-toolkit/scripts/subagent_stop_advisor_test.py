"""`SubagentStop`の助言フックの構造的な完了判定を検証する。"""

from __future__ import annotations

import json

import pytest
import subagent_stop_advisor as advisor


def _minimal_report() -> str:
    """委譲調整役の構造化された最小完了報告を返す。"""
    return """status: completed
summary: 完了
commits:
- aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
verification:
- pytest: 終了コード0、警告0件
reviews:
- 計画準拠系と独立系: 完了
findings:
- 指摘なし
plan_check: 完了条件を満たす
blockers:
- なし
"""


def _payload(report: str = "完了") -> dict[str, object]:
    return {
        "session_id": "session",
        "agent_id": "agent",
        "agent_transcript_path": "/tmp/agent.jsonl",
        "last_assistant_message": report,
    }


@pytest.fixture(autouse=True)
def _active_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        advisor,
        "read_state",
        lambda _session_id: {advisor._PLAN_IMPL_EXECUTOR_ACTIVE_KEY: {"agent": {}}},  # pylint: disable=protected-access
    )
    monkeypatch.setattr(advisor, "has_pending_agent_launches", lambda *_args: False)


def test_current_minimal_executor_report_passes(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps(_payload(_minimal_report()))) == 0
    assert capsys.readouterr().out == ""


def test_nonempty_report_does_not_require_legacy_labels(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps(_payload("status: completed"))) == 0
    assert capsys.readouterr().out == ""


def test_empty_completion_report_is_blocked(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main(json.dumps(_payload("  \n"))) == 0

    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert decision["reason"].startswith(
        "[auto-generated: agent-toolkit/subagent-stop][block] Provide a non-empty completion report"
    )


def test_registered_orchestrator_with_pending_child_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(advisor, "has_pending_agent_launches", lambda *_args: True)

    assert advisor.main(json.dumps(_payload(_minimal_report()))) == 0

    decision = json.loads(capsys.readouterr().out)
    assert decision["decision"] == "block"
    assert "Complete or receive every child agent before stopping" in decision["reason"]


def test_unregistered_agent_with_pending_child_keeps_existing_approval(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(advisor, "read_state", lambda _session_id: {})
    monkeypatch.setattr(advisor, "has_pending_agent_launches", lambda *_args: True)

    assert advisor.main(json.dumps(_payload("中間報告"))) == 0
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


def test_codex_does_not_consult_transcript(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Codexでは子エージェント検査へtranscriptを渡さない。"""

    def _fail(*_args: object) -> bool:
        raise AssertionError("Codexではtranscriptを完了判定へ使わない")

    monkeypatch.setattr(advisor, "has_pending_agent_launches", _fail)

    assert advisor.main(json.dumps({**_payload("完了"), "turn_id": "turn-1"})) == 0
    assert capsys.readouterr().out == ""


def test_invalid_payload_fails_open(capsys: pytest.CaptureFixture[str]) -> None:
    assert advisor.main("not json") == 0
    assert advisor.main("[]") == 0
    assert capsys.readouterr().out == ""
