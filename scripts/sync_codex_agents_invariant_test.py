"""既存成果物と、生成・登録される契約との整合を検証する。"""

import re

import sync_codex_agents as subject

from scripts.sync_codex_agents_test import _TWO_LAYER_WAIT_HEADING, _section


def test_two_layer_wait_contract_is_owned_by_delegation_skill() -> None:
    source = (subject.REPO_ROOT / "agent-toolkit/share/rules-main.codex.md").read_text(encoding="utf-8")
    reference = (subject.REPO_ROOT / "agent-toolkit/skills/delegation/references/codex-runtime.md").read_text(encoding="utf-8")
    generated = (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8")
    assert generated == subject.render()

    assert _TWO_LAYER_WAIT_HEADING not in source
    assert _TWO_LAYER_WAIT_HEADING not in generated
    section = _section(reference, _TWO_LAYER_WAIT_HEADING)
    assert {"`functions.exec`", "`atk agents wait`", "`cell_id`", "`functions.wait`"} <= set(re.findall(r"`[^`]+`", section))
    assert "タスク固有timeoutを渡さず" in section
    assert "対象が未終端なら" in section
    assert "前景のCLIが本文を返した後の逐次待機は新しいrunへ進む" in section
    assert "先行CLIが稼働中にlock競合した後発待機だけが、先行runの本文を1回回収する" in section


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
