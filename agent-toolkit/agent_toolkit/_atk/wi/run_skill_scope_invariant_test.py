"""一般UWIの保存・取込scopeが、起動目的と文書の起動単位へ一致することを検査する。"""

import pathlib
import re
import subprocess

import pytest

from agent_toolkit._atk import run_skill
from agent_toolkit._atk.wi import entries, frontmatter
from agent_toolkit._atk.wi.uwi_test import _invoke_uwi_add, _make_uwi_add_fake, _setup_notes
from agent_toolkit._common import skill_modes


@pytest.mark.parametrize(
    "skill,arguments,other_arguments",
    [
        ("agent-toolkit:session-review", "focus.md", "batch auto"),
        ("agent-toolkit:session-review", "batch auto", "focus.md"),
        ("agent-toolkit:process-wi", "focus.md", "single-lane 追加"),
        ("agent-toolkit:process-wi", "single-lane 追加", "all-repos"),
        ("agent-toolkit:process-wi", "all-repos", "single-lane 追加"),
    ],
)
def test_general_run_skill_uwi_uses_documented_scope_for_the_same_mode(
    tmp_path, monkeypatch, skill, arguments, other_arguments
):
    """文書のscope例を一般UWIへ保存し、同じモードの次回だけが取り込む境界を検査する。"""
    goal = run_skill.build_goal(skill, arguments)
    named_skill = re.search(r"スキル`([^`]+)`", goal)
    assert named_skill is not None
    mode = re.search(r"^起動モード: (\S+)$", goal, re.MULTILINE)
    document = pathlib.Path(__file__).resolve().parents[3] / "skills/wi-standards/references/origin-and-approval.md"
    rows = [
        [cell.strip().strip("`") for cell in line.strip("|").split("|")]
        for line in document.read_text(encoding="utf-8").splitlines()
        if line.startswith("| `agent-toolkit:")
    ]
    scope = next(row[2] for row in rows if row[:2] == [named_skill[1], mode[1] if mode else "なし"])
    notes = _setup_notes(tmp_path)
    repository = tmp_path / "myrepo"
    repository.mkdir()
    monkeypatch.setattr(subprocess, "run", _make_uwi_add_fake(repository))
    assert (
        _invoke_uwi_add(
            tmp_path, str(repository), "--source", "run-skill", "--scope", scope, body="この操作の事前承認を求める。"
        ).code
        == 0
    )
    selected = entries.select_entries(
        notes, status="active", target_repo="github.com/example/myrepo", entry_type="uwi", answered="all", source="run-skill"
    )
    assert len(selected) == 1
    path, _repo, text, _state, _kind = selected[0]
    parsed = frontmatter.parse_frontmatter(text)
    assert parsed is not None
    saved = parsed[0]
    assert saved["scope"] == skill_modes.invocation_scope(skill, arguments)
    assert saved["source"] == "run-skill" and "run_skill_report" not in saved
    assert saved["question_type"] == "yes-no"
    assert saved["scope"] == skill_modes.invocation_scope(skill, arguments.split()[0] + " 次回の自由記述")
    assert saved["scope"] != skill_modes.invocation_scope(skill, other_arguments)
    assert (saved["scope"] == skill_modes.invocation_scope(skill, None)) is (mode is None)
    assert path.read_text(encoding="utf-8") == text
