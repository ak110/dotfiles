"""自動振り返りの短い起動目的と、親子の終了契約への到達を検査する。"""

import io
import json
import pathlib
import sys

import pytest

from agent_toolkit import hook
from agent_toolkit._atk.run_skill_test import _engine_env
from agent_toolkit._atk.wi.run_skill_scope_invariant_test import _skill_invocation

# pytestがモジュールの属性から取得する共有fixtureを公開する。
__all__ = ["_engine_env", "_skill_invocation"]


@pytest.mark.parametrize("mode", [None, "batch"])
def test_goal_separates_child_completion_from_launcher_verification(mode, skill_invocation, monkeypatch, capsys):
    """短い最終目的と起動入力から、親子の終了責務を所有する文書へ到達する。"""
    scope = "agent-toolkit:review-session" + (" batch" if mode else "")
    data = skill_invocation("agent-toolkit:review-session", mode, scope)
    assert data["mode"] == mode
    with monkeypatch.context() as scoped:
        scoped.setenv("AGENT_TOOLKIT_SESSION_START_INPUT", json.dumps(data))
        payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"}).encode("utf-8")
        scoped.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(payload)))
        assert hook.main(["rules_context"]) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    root = pathlib.Path(__file__).resolve().parents[2] / "skills"
    assert "agent-toolkit:report-completion" in context
    report = (root / "report-completion/SKILL.md").read_text(encoding="utf-8")
    assert "報告の同期検収は、子セッションの終了後" in report
    assert "子自身の完了条件へ含めない" in report
    assert ("references/batch.md" in context) is (mode == "batch")
    if mode == "batch":
        batch = (root / "review-session/references/batch.md").read_text(encoding="utf-8")
        assert "atk run-script session-review-state -- finish" in batch
        assert "起動元run-skillがbatchセッションの終了後に報告の同期を検収" in batch
        assert "処理済みの確定はbatchセッション自身の完了条件に含めず" in batch
        assert "失敗時はfinishを実行せず" in batch
