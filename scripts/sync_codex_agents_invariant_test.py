"""Codex向けに生成する規範（`sync_codex_agents.py`の出力）が原本と一致し、2層待機の契約を`agent-toolkit:delegation`だけが持ち、共有ルールのスキル参照が両配布経路で解決できることを検証する。"""

import re

import sync_codex_agents as subject
from _scripts_test_helpers import TWO_LAYER_WAIT_HEADING, markdown_section
from agent_toolkit._testing.normative_contract import read_contract


def test_two_layer_wait_contract_is_owned_by_delegation_skill() -> None:
    source = (subject.REPO_ROOT / "agent-toolkit/share/rules/rules-common.codex.md").read_text(encoding="utf-8")
    plugin = subject.REPO_ROOT / "agent-toolkit"
    reference = read_contract(
        plugin / "skills/delegation/references/codex-runtime.md",
        plugin,
        ("codex-runtime-waiting.md", "agents_serverの二層待機"),
    )
    generated = (subject.REPO_ROOT / subject.TARGET).read_text(encoding="utf-8")
    assert generated == subject.render()

    assert TWO_LAYER_WAIT_HEADING not in source
    assert TWO_LAYER_WAIT_HEADING not in generated
    section = markdown_section(reference, TWO_LAYER_WAIT_HEADING)
    assert {"`functions.exec`", "`atk agents wait`", "`cell_id`", "`functions.wait`"} <= set(re.findall(r"`[^`]+`", section))
    assert "タスク固有timeoutを渡さず" in section
    assert "対象が未終了なら" in section
    assert "`waiting-and-monitoring-responses.md`「`atk agents wait`の応答の扱い」" in section
    waiting = read_contract(
        plugin / "skills/delegation/references/waiting-and-monitoring.md",
        plugin,
        ("waiting-and-monitoring-responses.md", "応答の扱い"),
    )
    response_section = markdown_section(waiting, "## `atk agents wait`の応答の扱い")
    assert "前景のCLIが本文を返した後の逐次待機は新しいrunへ進む" in response_section
    assert "配信済みの`foreground-delivered`または`consumed`なら本文を再出力せず" in response_section
    assert "未配送の`published`なら保存済み本文を回収し" in response_section


def test_shared_rule_references_resolve_from_codex_and_claude_distribution() -> None:
    """共有ルールの参照資料が両配布経路のplugin rootから解決できることを固定する。"""
    skill_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`")
    reference_pattern = re.compile(r"`agent-toolkit:(?P<skill>[a-z0-9-]+)`の`(?P<reference>references/[A-Za-z0-9._/-]+)`")
    rule_paths = sorted((subject.REPO_ROOT / "agent-toolkit/share/rules/common").glob("*.md"))
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
