"""WI原稿・計画初版の書込と工程別初期値の編集で、起草・編集の規範を案内する宣言を確かめる。"""

from __future__ import annotations

import pathlib

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery_matching

_PLUGIN = pathlib.Path(__file__).resolve().parents[3]
_REPOSITORY = _PLUGIN.parent

_AWI = "# 題\n\n本文\n\n## 反映内容と反映先\n\n1. 対策\n\n## 原因分析\n\n表\n\n## 完成条件\n\n1. 試験\n"
_UWI = "# 確認\n\n質問\n\n## 選択肢と帰結\n\n- 案\n\n## 判断材料\n\n根拠\n"
_PLAN = "# 計画\n\n## 概要\n\n目的\n\n### 計画メタ情報\n\n- 起動経路: `x`\n"


def _matched(source: pathlib.Path, tool_name: str, tool_input: dict) -> set[str]:
    """現物の読込表から、操作に一致して条件も確定する行の資料名を返す。"""
    rows = read_triggers.parse_tables(source, _PLUGIN, strict=True)
    return {
        target.path.name
        for row in rows
        if read_delivery_matching.row_matches(row, tool_name, tool_input, str(_REPOSITORY))
        and read_delivery_matching.condition_confirmed(row, tool_name, tool_input, str(_REPOSITORY))
        for target in row.targets
    }


@pytest.mark.parametrize(
    ("source", "content", "expected"),
    [
        ("skills/wi-standards/SKILL.md", _AWI, {"awi-body.md", "drafting-workflow-writing.md", "analysis-records.md"}),
        ("share/read-trigger-bootstrap.md", _AWI, {"analysis-records.md"}),
        ("skills/wi-standards/SKILL.md", _UWI, {"uwi-format.md"}),
        ("skills/write-plan/SKILL.md", _PLAN, {"plan-file-standards.md", "plan-file-standards-template.md"}),
    ],
)
def test_draft_in_temporary_area_reaches_drafting_standards(source: str, content: str, expected: set[str]) -> None:
    """一時領域の原稿でも、書く時点で起草規範の行が一致し、使い捨てのコードでは一致しない。"""
    draft = managed_temp.temp_root_path() / "session-test" / "draft.md"
    assert expected <= _matched(_PLUGIN / source, "Write", {"file_path": str(draft), "content": content})
    script = draft.with_name("count.py")
    assert not expected & _matched(_PLUGIN / source, "Write", {"file_path": str(script), "content": "print(1)\n"})


def test_uwi_draft_does_not_confirm_standard_draft_finalization() -> None:
    """報告用UWIと区別できないUWI原稿の書込では、報告用UWIを除く原稿確定の行を確定させない。"""
    draft = managed_temp.temp_root_path() / "session-test" / "uwi.md"
    matched = _matched(_PLUGIN / "skills/wi-standards/SKILL.md", "Write", {"file_path": str(draft), "content": _UWI})
    assert "drafting-workflow-writing.md" not in matched


@pytest.mark.parametrize(("name", "expected"), [("config_settings.py", True), ("config.py", False)])
def test_default_model_settings_edit_reaches_trial_rule(name: str, expected: bool) -> None:
    """工程別モデルの初期値を編集する操作だけが、実作業での試行を定める候補資料へ届く。"""
    path = _PLUGIN / "agent_toolkit/_atk" / name
    edit = {"file_path": str(path), "old_string": "a", "new_string": "b"}
    matched = _matched(_REPOSITORY / ".claude/skills/agent-toolkit-edit/SKILL.md", "Edit", edit)
    assert ("process-model-settings-candidates.md" in matched) is expected
