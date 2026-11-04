"""WI保存本文の種別を、公開コマンドの引数から区別する。"""

import pathlib
import shlex

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._hooks.pretooluse import operation_skills, read_delivery
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.mark.parametrize("subcommand", ["add", "edit item.md"])
@pytest.mark.parametrize(
    "frontmatter,report",
    [
        ("---\nrun_skill_report: true\n---\n", True),
        ("---\nrun_skill_report: false\n---\n", False),
        ("", False),
        ("---\nquestion_type: post-approval\n---\n", False),
    ],
)
def test_save_kind_is_declared_by_body_frontmatter(tmp_path, subcommand, frontmatter, report):
    """報告の識別は質問種別や本文の語句に依存しない。"""
    body = tmp_path / "body with spaces.md"
    body.write_text(frontmatter + "# 本文\nrun_skill_report: true\n", encoding="utf-8")
    inputs = {"command": f"atk wi {subcommand} --body-file={shlex.quote(str(body))}"}
    assert operation_skills.operation_matches("wi-report-save", "Bash", inputs, str(tmp_path)) is report
    assert operation_skills.operation_matches("wi-standard-save", "Bash", inputs, str(tmp_path)) is not report


@pytest.mark.parametrize("arguments", ["", "--body-file missing.md"])
def test_unspecified_or_unreadable_body_is_standard_save(tmp_path, arguments):
    """本文を取得できない保存にも通常保存の既存基準を案内する。"""
    inputs = {"command": f"atk wi add {arguments}"}
    assert operation_skills.operation_matches("wi-standard-save", "Bash", inputs, str(tmp_path))
    assert not operation_skills.operation_matches("wi-report-save", "Bash", inputs, str(tmp_path))


def test_help_does_not_select_save_requirements(tmp_path):
    """保存しない公開ヘルプ照会は保存種別に含めない。"""
    inputs = {"command": "atk wi add --help"}
    assert not operation_skills.operation_matches("wi-report-save", "Bash", inputs, str(tmp_path))
    assert not operation_skills.operation_matches("wi-standard-save", "Bash", inputs, str(tmp_path))


@pytest.mark.parametrize("report", [False, True])
def test_public_save_guidance_uses_declared_body_kind(tmp_path, monkeypatch, report):
    """実読込表と保存本文を公開hookへ接続し、報告と通常保存の資料を区別する。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    sources = [root / "skills" / name / "SKILL.md" for name in ("wi-standards", "add-awi-by-user", "report-completion")]
    rows = [
        row
        for source in sources
        for row in read_triggers.parse_tables(source, root)
        if {"wi-report-save", "wi-standard-save"} & set(row.operations)
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    body = tmp_path / "body.md"
    body.write_text(f"---\nrun_skill_report: {str(report).lower()}\n---\n# 保存本文\n", encoding="utf-8")
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": "save-guidance",
            "cwd": str(tmp_path),
            "tool_name": "Bash",
            "tool_input": {"command": f"atk wi add --body-file={shlex.quote(str(body))}"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    for name in ("report-formats.md", "uwi-format.md"):
        assert (name in result.stdout) is report
    assert ("drafting-workflow.md" in result.stdout) is not report
    assert "wi-standards/SKILL.md" in result.stdout or "`agent-toolkit:wi-standards`" in result.stdout
