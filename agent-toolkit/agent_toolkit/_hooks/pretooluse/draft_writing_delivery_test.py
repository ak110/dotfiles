"""WI原稿・計画初版の書込と工程別初期値の編集で、起草・編集の規範を案内することを確かめる。

宣言の一致は現物の読込表で、担当へ届く案内は公開のPreToolUse hookの出力で確かめる。
"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import read_delivery_matching
from agent_toolkit._testing.document_delivery import run_hook

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


@pytest.fixture(autouse=True)
def _editable_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """リポジトリ保護は未登録のリポジトリの書込を拒否して案内の生成へ進まないため、対象リポジトリを編集許可として登録する。"""
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps({"github.com/ak110/dotfiles": {"edit": "allow"}}))


def _guided(tool_name: str, tool_input: dict) -> str:
    """公開のPreToolUse hookへ操作を渡し、拒否されずに担当へ届く追加文脈を返す。"""
    payload = {
        "session_id": "draft-writing",
        "cwd": str(_REPOSITORY),
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
    }
    result = run_hook("pretooluse", payload)
    assert result.returncode == 0 and not result.stderr
    output = json.loads(result.stdout).get("hookSpecificOutput", {}) if result.stdout else {}
    # 拒否時は案内の生成まで進まず、追加文脈だけを見ると案内の欠落と区別できない。
    assert output.get("permissionDecision") != "deny", output.get("permissionDecisionReason")
    return output.get("additionalContext", "")


def _read_step(path: pathlib.Path) -> str:
    return f"Readで`{path}`を取得せよ"


@pytest.mark.parametrize(
    ("content", "documents"),
    [
        (_AWI, ("skills/wi-standards/references/awi-body.md", "skills/wi-standards/references/analysis-records.md")),
        (_UWI, ("skills/wi-standards/references/uwi-format.md",)),
        (_PLAN, ("skills/write-plan/references/plan-file-standards.md",)),
    ],
)
def test_public_hook_guides_drafting_standards_for_temporary_drafts(content: str, documents: tuple[str, ...]) -> None:
    """一時領域の原稿を書く操作で、公開hookが起草規範の取得を案内し、使い捨てのコードでは案内しない。"""
    draft = managed_temp.temp_root_path() / "session-test" / "draft.md"
    guided = _guided("Write", {"file_path": str(draft), "content": content})
    assert all(_read_step(_PLUGIN / document) in guided for document in documents)
    script = draft.with_name("count.py")
    unrelated = _guided("Write", {"file_path": str(script), "content": "print(1)\n"})
    assert not any(str(_PLUGIN / document) in unrelated for document in documents)


def test_public_hook_guides_trial_rule_for_default_model_settings_edit() -> None:
    """工程別モデルの初期値を編集する操作で、公開hookが実作業での試行を定める候補資料の取得を案内する。"""
    path = _PLUGIN / "agent_toolkit/_atk/config_settings.py"
    guided = _guided("Edit", {"file_path": str(path), "old_string": "a", "new_string": "b"})
    assert _read_step(_PLUGIN / "skills/delegation/references/process-model-settings-candidates.md") in guided
