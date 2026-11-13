# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""rules_context_compactの契約テスト。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._common.read_triggers import ReadTarget
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import read_delivery
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.rules_context_support import _disable_review_model_start as _disable_review_model_start
from agent_toolkit._testing.rules_context_support import _output


@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
@pytest.mark.parametrize("inside", [False, True])
def test_acquisition_failure_uses_host_acquisition(host: str, tmp_path: pathlib.Path, inside: bool) -> None:
    skill = tmp_path / "project/.claude/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 固有スキル本文\n", encoding="utf-8")
    cwd = str(tmp_path / ("project" if inside else "outside"))
    loadable = host == "claude" and inside
    failure = rules_context.acquisition_failure(ReadTarget(skill), ValueError("読取失敗"), shell=host != "claude", cwd=cwd)
    assert ("`Skill`で`local`" in failure) is loadable
    assert ("cat --" in failure) is not loadable


@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
@pytest.mark.parametrize("waiting", [False, True])
@pytest.mark.parametrize("history", [False, True])
def test_compact_public_notice_uses_current_stage_without_history_paths(tmp_path, monkeypatch, capsys, host, waiting, history):
    """履歴と待受けに依存せず、最初の判断前に工程文書と読込表へ戻す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context, "_compaction_notice", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    document = tmp_path / "past-material.md"
    document.write_text("# 過去の資料\n", encoding="utf-8")
    if history:
        read_delivery._select_targets("stage", [ReadTarget(document)], "main")
        read_delivery._mark("stage", document, "main")
    if waiting:
        rules_context.update_state("stage", lambda current: {**current, "waiting": {"target": "child"}})
    assert (
        rules_context.main(
            json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "stage"}), host=host
        )
        == 0
    )
    output = _output(capsys)
    assert str(document) not in output
    assert "工程を定めるスキルと役割文書を全文読み直し" in output
    assert "agent-toolkit/share/compaction-recovery.md`を全文読め" in output
    assert "読込表から次の判断に必要な資料を選んで読め" in output
    assert "復元先:" in output
    assert not read_delivery.target_ready(
        read_state("stage").get(rules_context.OPERATION_SKILL_READY_KEY, {}), ReadTarget(document), "main"
    )


def test_compact_skill_becomes_ready_only_after_skill_success(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: [])
    skill = tmp_path / ".claude" / "skills" / "local" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 固有スキル本文\n", encoding="utf-8")
    target = ReadTarget(skill)
    read_delivery._select_targets("compact-skill", [target], "main")
    read_delivery._mark("compact-skill", skill, "main")
    rules_context.main(
        json.dumps(
            {"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-skill", "cwd": str(tmp_path)}
        )
    )
    output = _output(capsys)
    assert "固有スキル本文" not in output and "`Skill`で`local`" not in output
    records = read_state("compact-skill").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert not read_delivery.target_ready(records, target, "main")
    read_delivery.observe(
        {"hook_event_name": "PostToolUseFailure", "tool_response": "失敗"}, "Skill", {"skill": "local"}, "compact-skill"
    )
    assert not read_delivery.target_ready(
        read_state("compact-skill").get(rules_context.OPERATION_SKILL_READY_KEY, {}), target, "main"
    )
    read_delivery.observe(
        {"hook_event_name": "PostToolUse", "tool_response": "成功"}, "Skill", {"skill": "local"}, "compact-skill"
    )
    assert read_delivery.target_ready(
        read_state("compact-skill").get(rules_context.OPERATION_SKILL_READY_KEY, {}), target, "main"
    )


@pytest.mark.parametrize(
    ("selected_agent", "acquired", "heading", "expected"),
    [
        (None, False, "", None),
        ("main", False, "", None),
        ("main", True, "", "全文の基準"),
        ("main", True, "「必要範囲」", "指定範囲の基準"),
        ("sub-1", True, "", None),
    ],
)
def test_compact_restores_selected_material_only_for_the_same_main_agent(
    selected_agent: str | None,
    acquired: bool,
    heading: str,
    expected: str | None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """過去の取得を通知へ列挙せず、再取得した同一主体の本文だけを取得済みに戻す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "changed-rule.md"
    document.write_text(
        "# 変更規範\n\n全文の基準\n\n## 「必要範囲」\n\n指定範囲の基準\n\n## 「別範囲」\n\n配送しない本文\n",
        encoding="utf-8",
    )
    target = ReadTarget(document, heading)
    if selected_agent is not None:
        read_delivery._select_targets("compact-recovery", [target], selected_agent)
        if acquired:
            read_delivery._mark("compact-recovery", document, selected_agent)

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-recovery"}))

    output = _output(capsys)
    records = read_state("compact-recovery").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    if expected is None:
        assert "全文の基準" not in output
        assert not read_delivery.target_ready(records, target, "main")
    else:
        assert expected not in output
        assert str(document) not in output
        assert not read_delivery.target_ready(records, target, "main")
        read_delivery.observe({"tool_response": document.read_text()}, "Read", {"file_path": str(document)}, "compact-recovery")
        records = read_state("compact-recovery").get(rules_context.OPERATION_SKILL_READY_KEY, {})
        assert read_delivery.target_ready(records, target, "main")
        if heading:
            assert "配送しない本文" not in output


def test_compact_keeps_large_target_unread_without_listing_it(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """大きい履歴資料でも通知へ列挙せず、未取得状態と通知予算を保つ。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "large-rule.md"
    document.write_text("# 大きい変更規範\n\n" + "本文" * rules_context.CLAUDE_CODE_OUTPUT_LIMIT, encoding="utf-8")
    target = ReadTarget(document)
    read_delivery._select_targets("compact-budget", [target], "main")
    read_delivery._mark("compact-budget", document, "main")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-budget"}))

    output = _output(capsys)
    records = read_state("compact-budget").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert str(document) not in output
    assert "工程を定めるスキルと役割文書" in output
    assert not read_delivery.target_ready(records, target, "main")
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


def test_compact_restores_acquired_material_for_a_delegated_independent_session(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """独立した委譲sessionも履歴を列挙せず、自身のmain主体の取得状態をリセットする。"""
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "delegated-rule.md"
    document.write_text("# 委譲規範\n\n委譲先の取得済み基準\n", encoding="utf-8")
    target = ReadTarget(document)
    read_delivery._select_targets("delegated-compact", [target], "main")
    read_delivery._mark("delegated-compact", document, "main")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "delegated-compact"}))

    output = _output(capsys)
    records = read_state("delegated-compact").get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert "委譲先の取得済み基準" not in output
    assert str(document) not in output
    assert not read_delivery.target_ready(records, target, "main")


@pytest.mark.parametrize("between", ["clear", "compact"])
def test_compact_discards_undelivered_recovery_target_before_a_later_context_boundary(
    between: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """繰り返す文脈境界でも履歴一覧を通知へ戻さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "external-state")
    document = tmp_path / "large-rule.md"
    document.write_text("# 大きい変更規範\n\n" + "本文" * rules_context.CLAUDE_CODE_OUTPUT_LIMIT, encoding="utf-8")
    target = ReadTarget(document)
    session_id = f"stale-{between}"
    read_delivery._select_targets(session_id, [target], "main")
    read_delivery._mark(session_id, document, "main")

    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}))
    assert str(document) not in _output(capsys)

    if between == "clear":
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "clear", "session_id": session_id}))
        assert str(document) not in _output(capsys)
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}))
    else:
        rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": session_id}))

    output = _output(capsys)
    records = read_state(session_id).get(rules_context.OPERATION_SKILL_READY_KEY, {})
    assert str(document) not in output
    assert not read_delivery.target_ready(records, target, "main")
