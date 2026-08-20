"""sync_codex_agentsのテスト。"""

import re
from pathlib import Path

import pytest
import sync_codex_agents as subject


def _root(tmp_path: Path, *, project: str = "project\n") -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "agent-toolkit/rules").mkdir(parents=True)
    (tmp_path / ".chezmoi-source/dot_codex").mkdir(parents=True)
    (tmp_path / "scripts/codex-agents-base.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(project, encoding="utf-8")
    return tmp_path


def test_render_preserves_rules_in_sorted_order(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/02-b.md").write_text("second\n\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("first\n", encoding="utf-8")

    content = subject.render(root)

    assert content.startswith(subject.GENERATED_MARKER + "\n\nbase\n")
    assert content.index("BEGIN: agent-toolkit/rules/01-a.md") < content.index("BEGIN: agent-toolkit/rules/02-b.md")
    assert "BEGIN: agent-toolkit/rules/01-a.md -->\nfirst\n<!-- END:" in content
    assert content.endswith("<!-- END: agent-toolkit/rules/02-b.md -->\n")


def test_render_excludes_claude_code_specific_rule(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-agent.md").write_text("first\n", encoding="utf-8")
    (root / "agent-toolkit/rules/02-agent-operations.md").write_text("second\n", encoding="utf-8")
    (root / "agent-toolkit/rules/99-claude-code.md").write_text("claude only\n", encoding="utf-8")

    content = subject.render(root)

    assert "BEGIN: agent-toolkit/rules/01-agent.md" in content
    assert "BEGIN: agent-toolkit/rules/02-agent-operations.md" in content
    assert "BEGIN: agent-toolkit/rules/99-claude-code.md" not in content
    assert "END: agent-toolkit/rules/99-claude-code.md" not in content
    assert "claude only" not in content


def test_sync_is_idempotent(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "agent-toolkit/rules/01-a.md").write_text("rule\n", encoding="utf-8")
    assert subject.sync(root) is True
    mtime = (root / subject.TARGET).stat().st_mtime_ns
    assert subject.sync(root) is False
    assert (root / subject.TARGET).stat().st_mtime_ns == mtime


def test_size_failure_does_not_replace_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path, project="project\n")
    target = root / subject.TARGET
    target.write_text("old\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("large\n", encoding="utf-8")
    monkeypatch.setattr(subject, "MAX_BYTES", 1)

    with pytest.raises(ValueError, match="超える"):
        subject.sync(root)
    assert target.read_text(encoding="utf-8") == "old\n"


def test_current_output_is_synced() -> None:
    content = subject.render()
    shared_agent = content.split("<!-- BEGIN: agent-toolkit/rules/01-agent.md -->", maxsplit=1)[1].split(
        "<!-- END: agent-toolkit/rules/01-agent.md -->", maxsplit=1
    )[0]
    shared_operations = content.split("<!-- BEGIN: agent-toolkit/rules/02-agent-operations.md -->", maxsplit=1)[1].split(
        "<!-- END: agent-toolkit/rules/02-agent-operations.md -->", maxsplit=1
    )[0]
    shared_rules = shared_agent + shared_operations
    operations_source = (subject.REPO_ROOT / "agent-toolkit/rules/02-agent-operations.md").read_text(encoding="utf-8")
    assert (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8") == content
    assert "99-claude-code.md" not in content
    for stable_command in (
        "atk managed-temp create --prefix <用途>",
        "atk managed-temp cleanup --path <検収済み絶対パス>",
        "atk watch --worktree",
        "出力予算内に収まる組へ分割",
        "読了として扱わない",
    ):
        assert stable_command in content
    for version_dependent_command in (
        "<plugin root>/scripts/_managed_temp.py create",
        "<plugin root>/scripts/_managed_temp.py cleanup",
        "<plugin root>/scripts/atk.py watch",
    ):
        assert version_dependent_command not in content
    for claude_specific in (
        "`subagent_type`",
        "`Explore`",
        "`name`パラメーター",
        "`run_in_background`",
        "`haiku`",
        "`sonnet`",
        "`opus`",
        "atk managed-temp",
        "Claude Code",
        "idle_notification",
        "記録ファイル直接読み取り",
        "自動的に背景実行へ転換",
        "foreground実行では完了報告",
        "~/.claude/plans/",
        "subagents/*.meta.json",
    ):
        assert claude_specific not in shared_rules
    for shared_contract in (
        "1つの作業ツリーへ書き込む主体は同時に1つだけ",
        "担当外差分は保持して委譲元へ報告",
        "自身が起動して識別子（PID）を確認したものに限る",
    ):
        assert shared_contract in operations_source
        assert shared_contract in shared_operations

    for skill_invocation in (
        "`agent-toolkit:add-feedback`をSkill機能で起動",
        "`agent-toolkit:plan-and-add-feedback`の実行中",
        "`agent-toolkit:bugfix`が定義する",
        "`agent-toolkit:process-feedbacks`の起動中",
        "`agent-toolkit:reviewee-standards`を起動",
        "`agent-toolkit:delegation`を正本とし",
    ):
        assert skill_invocation in shared_rules
    assert "../skills/" not in shared_rules
    assert "agent-toolkit/skills/" not in shared_rules


def test_shared_rule_references_resolve_from_codex_and_claude_distribution() -> None:
    """共有ルールの参照資料が両配布経路のplugin rootから解決できることを固定する。"""
    skill_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`")
    reference_pattern = re.compile(
        r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`スキルの`SKILL\.md`を含むディレクトリを基準に"
        r"相対解決した`(?P<reference>references/[A-Za-z0-9._/-]+)`"
    )
    rule_paths = sorted((subject.REPO_ROOT / "agent-toolkit/rules").glob("*.md"))
    source_rules = "\n".join(path.read_text(encoding="utf-8") for path in rule_paths)
    generated = subject.render()

    for distribution_text in (source_rules, generated):
        assert "../skills/" not in distribution_text
        for skill_name in set(skill_pattern.findall(distribution_text)):
            assert (subject.REPO_ROOT / "agent-toolkit/skills" / skill_name / "SKILL.md").is_file(), skill_name
        matches = reference_pattern.findall(distribution_text)
        assert matches
        for skill_name, reference in matches:
            skill_root = subject.REPO_ROOT / "agent-toolkit/skills" / skill_name
            assert (skill_root / "SKILL.md").is_file(), skill_name
            assert (skill_root / reference).is_file(), reference
