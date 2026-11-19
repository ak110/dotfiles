"""移設した運用資料への操作前の到達を検査する。"""

from pathlib import Path

from agent_toolkit._common import read_triggers
from agent_toolkit._testing.normative_contract import read_contract

_ROOT = Path(__file__).resolve().parent
_TOOLKIT = _ROOT / "agent-toolkit"


def test_operation_rules_reach_quality_and_verification_materials() -> None:
    """計画の有無によらず、検証の操作の前に共有の読込表から品質と検証運用の全文へ到達する。"""
    rows = read_triggers.parse_tables(_TOOLKIT / "share/read-trigger-bootstrap.md", _TOOLKIT, strict=True)
    targets = {path for row in rows if "verification" in row.operations for path in row.documents}
    assert _ROOT / "agent-basics/skills/check-execution/SKILL.md" in targets
    assert _TOOLKIT / "skills/atk-mode/playbooks/verification.md" in targets


def test_verification_playbook_reaches_planned_verification_definition() -> None:
    """検証の手順書は、計画した検証の実行を実在する計画の形式の定義で指し、その全文を本文の名指しから取得できる。"""
    source = _TOOLKIT / "skills/atk-mode/playbooks/verification.md"
    text = source.read_text()
    assert "`agent-toolkit:atk-mode/references/plan-format.md`「計画した検証の実行」" in text
    assert "本書「保存した実行出力の取得」" in text and "## 保存した実行出力の取得" in text
    plan = read_contract(source, _TOOLKIT, ("../references/plan-format.md", "本文がこの資料の基準を使う場面"))
    assert "## 計画した検証の実行" in plan
    rows = read_triggers.parse_tables(source, _TOOLKIT, strict=True)
    read_triggers.validate_reference_routes(source, _TOOLKIT, rows)
