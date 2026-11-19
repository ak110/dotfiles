"""移設した運用資料への操作前の到達を検査する。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_TOOLKIT = _ROOT / "agent-toolkit"


def test_operation_rules_reach_quality_and_verification_materials() -> None:
    """計画の有無によらず、入口の表から検証の運用と品質のスキルへ到達する。"""
    entry = (_TOOLKIT / "skills/atk-mode/SKILL.md").read_text(encoding="utf-8")
    rows = [line for line in entry.splitlines() if line.startswith("| ")]
    assert any("検証" in line and "`playbooks/verification.md`" in line for line in rows)
    assert any("`agent-basics:check-execution`" in line for line in rows)
    assert (_TOOLKIT / "skills/atk-mode/playbooks/verification.md").is_file()
    assert (_ROOT / "agent-basics/skills/check-execution/SKILL.md").is_file()
