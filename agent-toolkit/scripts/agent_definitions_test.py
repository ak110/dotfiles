"""計画ファイル名モードと直接消費文書の契約テスト。"""

import pathlib

_REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _section(text: str, heading: str, next_heading: str) -> str:
    """指定した見出しから次の見出しまでの本文を返す。"""
    start = text.index(heading)
    end = text.index(next_heading, start)
    return text[start:end]


def test_filename_mode_consumers_use_atomic_convert_to_plan_contract() -> None:
    """スキル、ガイド、設計及び方針が同じ可変長変換経路を指示する。"""
    skill = (_REPOSITORY_ROOT / "agent-toolkit/skills/plan-and-add-feedback/SKILL.md").read_text(encoding="utf-8")
    guide = (_REPOSITORY_ROOT / "docs/guide/claude-code-guide.md").read_text(encoding="utf-8")
    design = (_REPOSITORY_ROOT / "docs/development/design.md").read_text(encoding="utf-8")
    concepts = (_REPOSITORY_ROOT / "docs/development/concepts.md").read_text(encoding="utf-8")

    skill_section = _section(skill, "## ファイル名モード", "## 自然言語要件モード")
    guide_section = _section(guide, "### 通常型ファイル名を指定した計画作成", "### session-reviewのユーザーコメント")
    guide_procedure = guide_section.split("\n`atk mq reject`", 1)[0]
    design_section = _section(design, "### 計画作成状態と計画型変換", "### キュー状態と公開一覧")
    planning_policy = next(
        line for line in concepts.splitlines() if "通常型フィードバックのファイル名を指定する計画化では" in line
    )

    for section in (skill_section, guide_procedure, design_section, planning_policy):
        assert "convert-to-plan" in section
        assert "edit --plan-file" not in section
    assert "--message=<plan-feedback-body>" in skill_section
    assert "1回の`atk mq convert-to-plan`" in guide_procedure
    assert "計画の全feedback素材と一致" in design_section
    assert "`atk mq edit`の`--plan-file`互換経路と`atk mq convert-to-plan`に限定" in design_section
    assert "`atk mq convert-to-plan`による`planning`統合は正規の計画変換" in design_section
    assert "既存の計画変換の対象から除外する" not in design_section
    assert "同じcommitで除去" in planning_policy
    assert "atk mq rm" not in skill_section
    assert "atk mq rm" not in guide_procedure
    assert "atk mq rm" not in design_section
