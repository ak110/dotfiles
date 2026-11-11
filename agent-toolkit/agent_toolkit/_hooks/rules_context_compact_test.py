# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""rules_context_compactの契約テスト。"""

from __future__ import annotations

import hashlib
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
def test_compact_skill_guidance_uses_host_acquisition(host: str, tmp_path: pathlib.Path, inside: bool) -> None:
    skill = tmp_path / "project/.claude/skills/local/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("# 固有スキル本文\n", encoding="utf-8")
    digest = hashlib.sha256(skill.read_bytes()).hexdigest()
    cwd = str(tmp_path / ("project" if inside else "outside"))
    output = rules_context._compact_recovery_context(host, 10000, [(skill, "", digest, False)], cwd=cwd)
    if host == "claude" and inside:
        assert output is None
        return
    assert output is not None
    assert "固有スキル本文" not in output
    assert str(skill) in output
    loadable = host == "claude" and inside
    assert ("`Skill`で`local`" in output) is loadable
    assert ("cat --" in output) is not loadable
    failure = rules_context.acquisition_failure(ReadTarget(skill), ValueError("読取失敗"), shell=host != "claude", cwd=cwd)
    assert ("`Skill`で`local`" in failure) is loadable
    assert ("cat --" in failure) is not loadable


def test_compact_guidance_presents_candidates_for_remaining_steps(tmp_path: pathlib.Path) -> None:
    """圧縮前の選択資料は候補として示し、残る工程の判断に使う資料だけを使う前に全文再取得させる。"""
    reference = tmp_path / "reference.md"
    reference.write_text("# 見出し\n本文\n", encoding="utf-8")
    digest = hashlib.sha256(reference.read_bytes()).hexdigest()
    output = rules_context._compact_recovery_context("claude", 10000, [(reference, "", digest, False)])
    assert output is not None
    assert "候補" in output and "残る工程の判断に使う資料だけ" in output and "使う前に" in output
    assert "全文再取得" in output
    assert "選択済み資料を再取得せよ" not in output


@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
def test_compact_missing_heading_guides_full_acquisition(host: str, tmp_path: pathlib.Path) -> None:
    reference = tmp_path / "reference.md"
    reference.write_text("# 実見出し\n固有本文\n", encoding="utf-8")
    digest = hashlib.sha256(reference.read_bytes()).hexdigest()
    output = rules_context._compact_recovery_context(host, 10000, [(reference, "不存在", digest, False)])
    assert output is not None
    assert "不存在" in output and "見出し" in output
    assert "固有本文" not in output
    assert ("cat --" in output) is (host in {"codex", "agy"})
    assert ("Readで全文読" in output) is (host == "claude")


def test_compact_missing_document_guides_repair(tmp_path: pathlib.Path) -> None:
    reference = tmp_path / "missing.md"
    output = rules_context._compact_recovery_context("claude", 10000, [(reference, "", "old-hash", False)])
    assert output is not None
    assert str(reference) in output and "存在しない" in output and "復旧" in output
    assert "委譲元" in output and "ユーザー" in output
    assert "Readで全文読" not in output


@pytest.mark.parametrize("host", ["claude", "codex", "agy"])
def test_compact_public_session_start_reports_missing_material(tmp_path, monkeypatch, capsys, host):
    missing = tmp_path / "missing.md"
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(rules_context, "compose_session_start", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context, "update_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(rules_context, "mark_user_confirmation_pending", lambda *_args: None)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    monkeypatch.setattr(
        rules_context, "reset_main_operation_skill_ready", lambda *_args, **_kwargs: [(missing, "対象", "old-hash", False)]
    )
    payload = {"hook_event_name": "SessionStart", "source": "compact", "session_id": "missing"}
    assert rules_context.main(json.dumps(payload), host=host) == 0
    output = _output(capsys)
    assert str(missing) in output and "存在しない" in output and "復旧" in output
    assert "委譲元" in output and "ユーザー" in output


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
    """compact直後は、同一主体が選択済みの必要範囲だけを次のPreToolUseより先に回復する。"""
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
        assert str(document) in output
        assert not read_delivery.target_ready(records, target, "main")
        read_delivery.observe({"tool_response": document.read_text()}, "Read", {"file_path": str(document)}, "compact-recovery")
        records = read_state("compact-recovery").get(rules_context.OPERATION_SKILL_READY_KEY, {})
        assert read_delivery.target_ready(records, target, "main")
        if heading:
            assert "配送しない本文" not in output


def test_compact_keeps_target_unread_and_shows_its_range_when_recovery_budget_is_insufficient(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """本文がSessionStart残予算へ収まらない場合、取得済みへ戻さず読取先と必要範囲を示す。"""
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
    assert str(document) in output
    assert "Read" in output
    assert not read_delivery.target_ready(records, target, "main")
    assert len(output) <= rules_context.CLAUDE_CODE_OUTPUT_LIMIT


def test_compact_restores_acquired_material_for_a_delegated_independent_session(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """独立した委譲sessionも、自身のmain主体が取得済みだった資料をcompact後に回復する。"""
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
    assert str(document) in output
    assert not read_delivery.target_ready(records, target, "main")


@pytest.mark.parametrize("between", ["clear", "compact"])
def test_compact_discards_undelivered_recovery_target_before_a_later_context_boundary(
    between: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """予算超過後の復旧候補をclearまたは次のcompactへ持ち越さない。"""
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
    assert str(document) in _output(capsys)

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
