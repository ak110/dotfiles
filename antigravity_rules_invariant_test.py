"""実配布の常時規範予算と、移設した運用資料への操作前の到達を検査する。"""

from pathlib import Path

from agent_toolkit._common import read_triggers

from pytools._internal import antigravity_plugin

_ROOT = Path(__file__).resolve().parent
_TOOLKIT = _ROOT / "agent-toolkit"


def test_current_rules_fit_budget_without_losing_text() -> None:
    """現物の規範が既存の予算内に収まり、分割後にも全文が同じ順序で残る。"""
    assert antigravity_plugin.RULE_BUDGET_TOKENS == 20_000
    outputs = {path: data for path, (data, _) in antigravity_plugin.outputs(_ROOT).items() if path.parts[0] == "rules"}
    ordered = sorted(outputs.items(), key=lambda item: (item[0].name != "AGENTS.md", item[0].name))
    estimated = sum((len(data) + 1) // 2 for _, data in ordered)
    assert estimated <= antigravity_plugin.RULE_BUDGET_TOKENS
    assert all(len(data) <= antigravity_plugin.RULE_FILE_BYTES for _, data in ordered)
    actual = "".join(data.decode().removeprefix("---\ntrigger: always_on\n---\n\n") for _, data in ordered)
    expected = "".join(path.read_text().rstrip("\n") + "\n" for path in sorted((_TOOLKIT / "rules").glob("*.md")))
    assert actual == expected


def test_operation_rules_reach_quality_and_verification_materials() -> None:
    """計画の有無によらず、操作前の入口から品質と検証運用の全文へ到達する。"""
    rules = (_TOOLKIT / "rules/02-agent-operations.md").read_text()
    assert "`agent-toolkit:/share/read-trigger-bootstrap.md`の条件表" in rules
    assert "該当する判断や操作の前に行う" in rules
    rows = read_triggers.parse_tables(_TOOLKIT / "share/read-trigger-bootstrap.md", _TOOLKIT, strict=True)
    targets = {path for row in rows if "verification" in row.operations for path in row.documents}
    assert _ROOT / "agent-basics/skills/check-execution/SKILL.md" in targets
    assert _TOOLKIT / "share/check-execution-operations.md" in targets
    assert _TOOLKIT / "share/verification-operations.md" in targets


def test_shared_verification_references_resolve_after_migration() -> None:
    """share資料は廃止したスキル本体への相対的な案内を残さず、実在する定義元を指す。"""
    source = _TOOLKIT / "share/verification-operations.md"
    text = source.read_text()
    assert "本スキルの`SKILL.md`" not in text
    assert "`agent-toolkit:plan-mode/references/plan-file-standards.md`「計画した検証の実行」" in text
    assert "`check-execution-operations.md`「保存した実行出力の取得」" in text
    rows = read_triggers.parse_tables(source, _TOOLKIT, strict=True)
    read_triggers.validate_reference_routes(source, _TOOLKIT, rows)
