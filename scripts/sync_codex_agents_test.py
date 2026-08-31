"""sync_codex_agentsのテスト。"""

import re
from pathlib import Path

import pytest
import sync_codex_agents as subject

_TWO_LAYER_WAIT_HEADING = "### agents_serverの二層待機"


def _root(tmp_path: Path, *, project: str = "project\n") -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "agent-toolkit/rules").mkdir(parents=True)
    (tmp_path / "agent-toolkit/share").mkdir(parents=True)
    (tmp_path / ".chezmoi-source/dot_codex").mkdir(parents=True)
    (tmp_path / "agent-toolkit/share/codex-agents-base.md").write_text("base\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text(project, encoding="utf-8")
    return tmp_path


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^{re.escape(heading)}\n(?P<body>.*?)(?=^#{{1,3}} |\Z)", text, re.MULTILINE | re.DOTALL)
    assert match is not None
    return match.group("body")


def _ordered_steps(section: str) -> list[str]:
    matches = re.findall(r"^(?P<number>\d+)\. (?P<body>.+)$", section, re.MULTILINE)
    assert [number for number, _ in matches] == ["1", "2", "3", "4", "5"]
    return [body for _, body in matches]


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


def test_two_layer_wait_contract_is_structurally_synced() -> None:
    source = (subject.REPO_ROOT / "agent-toolkit/share/codex-agents-base.md").read_text(encoding="utf-8")
    generated = (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8")
    assert generated == subject.render()

    source_section = _section(source, _TWO_LAYER_WAIT_HEADING)
    assert _section(generated, _TWO_LAYER_WAIT_HEADING) == source_section
    steps = _ordered_steps(source_section)

    assert {"`agents_server.start`", "`session_id`"} <= set(re.findall(r"`[^`]+`", steps[0]))
    assert {"`agents_server.wait`", "`timeout`"} <= set(re.findall(r"`[^`]+`", steps[1]))
    assert "timeout=" not in steps[1]
    assert {"`cell_id`", "`functions.wait`", "`agents_server.wait`"} <= set(re.findall(r"`[^`]+`", steps[2]))
    assert "再度呼ばない" in steps[2]
    assert "終端結果" in steps[3] and "同じ応答" in steps[3]
    assert {"`cell_id`", "`wait`"} <= set(re.findall(r"`[^`]+`", steps[4]))
    assert "戻り値をそのまま" in steps[4]


def test_main_help_does_not_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    """`--help`は終了コード0で生成処理へ到達しない。"""
    monkeypatch.setattr(subject, "sync", lambda: pytest.fail("syncを呼び出した"))

    with pytest.raises(SystemExit) as exc_info:
        subject.main(["--help"])

    assert exc_info.value.code == 0


def test_size_failure_does_not_replace_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _root(tmp_path, project="project\n")
    target = root / subject.TARGET
    target.write_text("old\n", encoding="utf-8")
    (root / "agent-toolkit/rules/01-a.md").write_text("large\n", encoding="utf-8")
    monkeypatch.setattr(subject, "MAX_BYTES", 1)

    with pytest.raises(ValueError, match="超える"):
        subject.sync(root)
    assert target.read_text(encoding="utf-8") == "old\n"


def test_shared_rule_references_resolve_from_codex_and_claude_distribution() -> None:
    """共有ルールの参照資料が両配布経路のplugin rootから解決できることを固定する。"""
    skill_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`")
    reference_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`の`(?P<reference>references/[A-Za-z0-9._/-]+)`")
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
