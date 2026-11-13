"""WI保存本文の種別を、公開コマンドの引数から区別する。"""

import pathlib
import shlex

import pytest

from agent_toolkit._common import read_triggers
from agent_toolkit._common.read_triggers_model import WI_SAVE_OPERATIONS
from agent_toolkit._hooks.pretooluse import operation_skills, read_delivery
from agent_toolkit._hooks.pretooluse.read_delivery_cache import HOOK_PAYLOAD
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process
from agent_toolkit._testing.read_delivery_support import _write_transcript


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
    assert operation_skills.operation_matches("wi-report-commit", "Bash", inputs, str(tmp_path)) is report
    assert operation_skills.operation_matches("wi-standard-commit", "Bash", inputs, str(tmp_path)) is not report


@pytest.mark.parametrize("arguments", ["", "--body-file missing.md"])
def test_unspecified_or_unreadable_body_is_standard_save(tmp_path, arguments):
    """本文を取得できない保存にも通常保存の既存基準を案内する。"""
    inputs = {"command": f"atk wi add {arguments}"}
    assert operation_skills.operation_matches("wi-standard-commit", "Bash", inputs, str(tmp_path))
    assert not operation_skills.operation_matches("wi-report-commit", "Bash", inputs, str(tmp_path))


def test_help_does_not_select_save_requirements(tmp_path):
    """保存しない公開ヘルプ照会は保存種別に含めない。"""
    inputs = {"command": "atk wi add --help"}
    assert not operation_skills.operation_matches("wi-report-commit", "Bash", inputs, str(tmp_path))
    assert not operation_skills.operation_matches("wi-standard-commit", "Bash", inputs, str(tmp_path))


@pytest.mark.parametrize("report", [False, True])
def test_public_save_guidance_uses_declared_body_kind(tmp_path, monkeypatch, report):
    """実読込表と保存本文を公開hookへ接続し、報告と通常保存の資料を区別する。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    sources = [root / "skills" / name / "SKILL.md" for name in ("wi-standards", "add-awi-by-user", "report-completion")]
    rows = [
        row
        for source in sources
        for row in read_triggers.parse_tables(source, root)
        if WI_SAVE_OPERATIONS & set(row.operations)
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
    for name in ("report-formats-uwi.md", "uwi-format.md"):
        assert (name in result.stdout) is report
    for name in (
        "draft-semantic-review.md",
        "drafting-workflow-writing.md",
        "drafting-workflow-validation.md",
        "drafting-completion-validation.md",
        "drafting-workflow-review-route.md",
        "drafting-workflow-saving.md",
        "drafting-completion-duplicates.md",
    ):
        assert (name in result.stdout) is not report
    for row in rows:
        if ("wi-report-commit" if report else "wi-standard-commit") in row.operations:
            assert all(not target.heading and target.body().strip() for target in row.targets)
    assert ("wi-standards/SKILL.md" in result.stdout) is report


@pytest.mark.parametrize("route", ["main", "付随型", "専念型", "omitted"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_author_route_and_save_stage_select_required_documents(tmp_path, monkeypatch, route, dry_run):
    """付随型の検証は起草資料だけ、本保存は保存資料も案内する。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    rows = [
        row
        for row in read_triggers.parse_tables(root / "skills/wi-standards/SKILL.md", root)
        if any(operation.startswith("wi-standard-") for operation in row.operations)
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    body = tmp_path / "body.md"
    body.write_text("# 原稿\n", encoding="utf-8")
    log = tmp_path / "launch.jsonl"
    launch = "通常の依頼"
    if route != "main":
        launch = f"次の文書の手順を実行せよ（出所: {root / 'share/add-wi.subagent.md'}）。\n入力:\n"
        if route != "omitted":
            launch += f"投入経路: {route}\n"
    _write_transcript(log, '<atk-auto source="agents-server" kind="delivery">\n' + launch + "\n</atk-auto>")
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": f"stage-{route}-{dry_run}",
            "cwd": str(tmp_path),
            "transcript_path": str(log),
            "tool_name": "Bash",
            "tool_input": {"command": f"atk wi add {'--dry-run' if dry_run else ''} --body-file {body}"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    for name in ("drafting-workflow-writing.md", "drafting-workflow-validation.md", "drafting-completion-validation.md"):
        assert name in result.stdout
    for name in ("drafting-workflow-saving.md", "drafting-completion-duplicates.md"):
        assert (name in result.stdout) is not dry_run
    for name in ("drafting-workflow-review-route.md", "draft-semantic-review.md"):
        assert (name in result.stdout) is (route in {"main", "専念型"})
    assert not HOOK_PAYLOAD.get()
