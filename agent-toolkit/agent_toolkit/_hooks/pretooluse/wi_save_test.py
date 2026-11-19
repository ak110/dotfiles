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
    rows = [
        row
        for row in read_triggers.parse_tables(root / "share" / "read-trigger-bootstrap.md", root)
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
    for name in ("references/report-formats.md", "uwi-format.md"):
        assert (name in result.stdout) is report
    for name in ("wi-drafting.md", "draft-semantic-review.md", "states-and-dependencies.md", "origin-and-approval.md"):
        assert (name in result.stdout) is not report
    assert "playbooks/wi-saving.md" in result.stdout
    for row in rows:
        if ("wi-report-commit" if report else "wi-standard-commit") in row.operations:
            assert all(not target.heading and target.body().strip() for target in row.targets)


@pytest.mark.parametrize("route", ["main", "付随型", "専念型", "omitted"])
@pytest.mark.parametrize("dry_run", [True, False])
def test_author_route_and_save_stage_select_required_documents(tmp_path, monkeypatch, route, dry_run):
    """付随型の検証は起草資料だけ、本保存は保存資料も案内する。"""
    root = pathlib.Path(__file__).resolve().parents[3]
    # 起草経路ごとの案内を確かめる。検証の段階の資料集合全体は別の試験で期待値と比べる。
    sources = ("share/read-trigger-bootstrap.md", "skills/atk-mode/roles/add-wi.subagent.md")
    rows = [
        row
        for source in sources
        for row in read_triggers.parse_tables(root / source, root)
        if any(operation.startswith("wi-standard-") for operation in row.operations)
    ]
    monkeypatch.setattr(read_delivery, "declarations", lambda *_args, **_kwargs: rows)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_PLUGIN_ROOT", raising=False)
    body = tmp_path / "body.md"
    body.write_text("# 原稿\n\n## 反映内容と反映先\n", encoding="utf-8")
    log = tmp_path / "launch.jsonl"
    launch = "通常の依頼"
    if route != "main":
        launch = f"次の文書の手順を実行せよ（出所: {root / 'skills/atk-mode/roles/add-wi.subagent.md'}）。\n入力:\n"
        if route != "omitted":
            launch += f"投入経路: {route}\n"
    _write_transcript(log, '<atk-auto source="agents-server" kind="delivery">\n' + launch + "\n</atk-auto>")
    result = _run_hook_in_process(
        "pretooluse",
        {
            "session_id": f"stage-{route.encode().hex()}-{dry_run}",
            "cwd": str(tmp_path),
            "transcript_path": str(log),
            "tool_name": "Bash",
            "tool_input": {"command": f"atk wi add {'--dry-run' if dry_run else ''} --body-file {body}"},
        },
    )
    assert result.returncode == 0 and not result.stderr
    # 起草と、回答済みUWIとの整合確認を含む保存前の確認は、原稿の内容を左右するため検証の段階でも届ける。
    for name in ("playbooks/wi-drafting.md", "playbooks/wi-saving.md"):
        assert name in result.stdout
    # 独立した意味の確認は、付随型のWI投入担当では委譲元が行う。
    assert ("draft-semantic-review.md" in result.stdout) is (route in {"main", "専念型"})
    # 保存先・状態の資料と、確認の要否・由来の資料は本保存で届ける。
    for name in ("states-and-dependencies.md", "playbooks/confirmation.md", "origin-and-approval.md"):
        assert (name in result.stdout) is not dry_run
    assert not HOOK_PAYLOAD.get()


@pytest.mark.cpu_limit(
    2.0,
    reason="追加された宣言元を漏らさないよう全宣言元の読込表を解析する。Linux CPU合計0.96秒を基準とする。",
)
def test_dry_run_stage_documents_match_expected_set():
    """検証の段階で届ける資料の集合を、全宣言元から集めて期待値と比べる。

    新しく検証の段階を付けた行は、この期待値の更新として表面化させる。
    追加する前に`share/read-trigger-contract-declarations.md`のWI保存の基準を満たすか確かめる。
    """
    root = pathlib.Path(__file__).resolve().parents[3]
    actual = {
        (str(row.source.relative_to(root)), str(target.path.relative_to(root)))
        for source in read_triggers.document_sources(root, str(root))
        for row in read_triggers.parse_tables(source, root)
        if "wi-standard-dry-run" in row.operations
        for target in row.targets
    }
    playbooks = ("skills/atk-mode/playbooks/wi-drafting.md", "skills/atk-mode/playbooks/wi-saving.md")
    assert actual == {
        (source, name)
        for source in ("share/read-trigger-bootstrap.md", "skills/atk-mode/roles/add-wi.subagent.md")
        for name in playbooks
    }
