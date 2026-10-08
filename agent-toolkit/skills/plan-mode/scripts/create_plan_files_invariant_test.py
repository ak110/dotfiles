"""計画作成処理とエージェント向け文書の横断契約を検証する。"""

import argparse
import json
import pathlib
import re

import create_plan_files
import pytest
from create_plan_files_test import (  # pylint: disable=import-error,unused-import
    _make_repo,
    _source,
)

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository

_DOCUMENTED_BUG_REFERENCE_PATTERN = re.compile(r"計画ファイル（バグ）: `([^`]+)`")


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """構造の判定に使うGitリポジトリを準備する。"""
    return _make_repo(tmp_path, monkeypatch)


def _documented_bug_reference() -> str:
    """計画ファイル作成基準が示す計画ファイル（バグ）の参照値を返す。"""
    standards = pathlib.Path(__file__).resolve().parents[3] / "skills/plan-mode/references/plan-file-standards.md"
    references = _DOCUMENTED_BUG_REFERENCE_PATTERN.findall(standards.read_text(encoding="utf-8"))
    assert len(references) == 1, "計画ファイル（バグ）の参照値の記載例が1件に定まらない"
    return references[0]


def test_documented_bug_reference_is_accepted_without_substitution(repo: pathlib.Path, tmp_path: pathlib.Path) -> None:
    """計画ファイル作成基準の記載例を置換せず転記した本文を受理する。"""
    reference = _documented_bug_reference()
    assert reference.startswith(create_plan_files.PLAN_ADJUNCT_REFERENCE_PREFIX)
    source, bug_source = _source(repo, tmp_path, bug=True, bug_reference=reference)

    paths = create_plan_files.create_plan_files(
        source,
        "13-0217_記載例の転記",
        bug_source=bug_source,
        home=tmp_path / "home",
        work_dir=repo,
    )

    assert [path.name for path in paths] == ["13-0217_記載例の転記.md", "13-0217_記載例の転記.bugs.md"]
    assert all(create_plan_files.PLAN_STEM_PLACEHOLDER not in path.read_text(encoding="utf-8") for path in paths)


def _lane_plan_creation_step() -> str:
    """レーン担当が読む計画作成手順の本文を返す。"""
    task_path = pathlib.Path(__file__).resolve().parents[3] / "skills/process-wi/references/lane-planning.md"
    content = task_path.read_text(encoding="utf-8")
    steps = re.split(r"\n(?=\d+\. )", content)
    matches = [step for step in steps if re.match(r"\d+\. ", step) and "plan-create" in step]
    assert len(matches) == 1
    return matches[0]


@pytest.mark.parametrize("bug", [False, True])
def test_lane_plan_creation_step_arguments_are_accepted_by_current_cli(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    bug: bool,
) -> None:
    """計画作成手順が指示する引数名を現行CLIが受理する。"""
    step = _lane_plan_creation_step()
    source, bug_source = _source(repo, tmp_path, bug=bug)
    placeholders = {"--main-source": str(source), "--lane": "lane-02"}
    if bug:
        placeholders["--bugs-source"] = str(bug_source)
    argv: list[str] = []
    for option, value in placeholders.items():
        assert f"{option} <" in step, f"計画作成手順が{option}を指示していない"
        argv.extend([option, value])
    argv.extend(["--home", str(tmp_path / "home"), "--work-dir", str(repo)])

    result = create_plan_files.main(argv)

    captured = capsys.readouterr()
    assert result == 0, captured.err
    created = [pathlib.Path(line) for line in captured.out.splitlines() if line]
    assert len(created) == (2 if bug else 1)
    assert all(path.exists() for path in created)


@pytest.mark.parametrize("refactoring", [True, False])
def test_document_template_supports_create_progress_and_commit_lookup(
    repo: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    refactoring: bool,
) -> None:
    """実在文書の雛形を記入し、公開操作だけで作成、進捗と対応の保存、取得まで進む。"""
    document = pathlib.Path(__file__).resolve().parents[1] / "references" / "plan-file-standards.md"
    template = (
        document.read_text(encoding="utf-8").split("## 初回起草の雛形\n", 1)[1].split("```md\n", 1)[1].split("\n```", 1)[0]
    )
    content = re.sub(r"<[^>]+>", "案件の記入内容", template).replace("/absolute/repository/path", str(repo))
    if not refactoring:
        start = content.index("| 対象 | 現状の問題 | 対応 |")
        end = content.index("## 変更履歴", start)
        content = content[:start] + "\n" + content[end:]
    notes = tmp_path / "private-notes"
    (notes / "processing").mkdir(parents=True)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    wi = "20260831-000000-001.md"
    (notes / "processing" / wi).write_text(
        "---\nsource: test\n---\n# 変更\n\n## 適用範囲\n\n対象の公開契約。\n", encoding="utf-8"
    )
    source = tmp_path / "template.md"
    source.write_text(content + "\n", encoding="utf-8")
    args = argparse.Namespace(
        script_name="plan-create",
        script_args=[
            "--main-source",
            str(source),
            "--name",
            "template",
            "--work-dir",
            str(repo),
            "--home",
            str(tmp_path / "home"),
        ],
    )
    assert run_script.dispatch(args) == 0
    capsys.readouterr()
    (plan,) = (tmp_path / "home" / ".claude" / "plans").glob("*.md")
    git_repository.run_git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "base"
    )
    previous = git_repository.run_git(repo, "rev-parse", "HEAD").stdout.strip()
    git_repository.run_git(
        repo, "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "change"
    )
    args.script_name = "plan-progress"
    args.script_args = [
        str(plan),
        "--completed-step",
        "実装",
        "--result",
        "成功",
        "--commit",
        "HEAD",
        "--previous-head",
        previous,
        "--worktree",
        str(repo),
        "--awi",
        wi,
    ]
    assert run_script.dispatch(args) == 0
    assert "実装 | 成功" in plan.read_text(encoding="utf-8")
    capsys.readouterr()
    args.script_name = "plan-commits"
    args.script_args = [str(plan), "--worktree", str(repo), "--awi", wi]
    assert run_script.dispatch(args) == 0
    assert json.loads(capsys.readouterr().out)["awi"] == wi
