"""規範から構成した調査表を公開検査へ渡し、定義の片側改訂を検出する。"""

import argparse
import pathlib
import re

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._plan.structure import sections_bugs
from agent_toolkit._testing import git_repository, plan_fixture


def _documented_investigation() -> str:
    """列・系統・調査行はanalysis-records.mdだけから導出する。"""
    document = pathlib.Path(__file__).resolve().with_name("analysis-records.md").read_text(encoding="utf-8")
    cause = re.search(r"原因分析表は、(.+?)の\d+列で構成し、(.+?)の\d+行を", document)
    investigation = re.search(r"調査表は、?(.+?)の\d+列で構成し", document)
    assert cause is not None and investigation is not None
    cause_columns = re.findall(r"`([^`]+)`", cause[1])
    factors = re.findall(r"`([^`]+)`", cause[2])
    columns = re.findall(r"`([^`]+)`", investigation[1])
    table = document.split("| 行名 | 記載内容 |\n", 1)[1].split("\n\n", 1)[0]
    labels = [line.split("|")[1].strip() for line in table.splitlines()[1:]]
    assert cause_columns and factors and columns and labels
    cause_table = ["| " + " | ".join(cause_columns) + " |", "| " + " | ".join("---" for _ in cause_columns) + " |"]
    cause_table.extend(
        "| " + " | ".join([factor, *["原因を確認した。" for _ in cause_columns[1:]]]) + " |" for factor in factors
    )
    rows = ["| " + " | ".join(columns) + " |", "| " + " | ".join("---" for _ in columns) + " |"]
    rows.extend(f"| {label} | 対象と結果を確認した。 |" for label in labels)
    return "# 原因分析\n\n### 対象の不整合\n\n" + "\n".join(cause_table) + "\n\n" + "\n".join(rows) + "\n"


@pytest.mark.parametrize("case", ["documented", "explanation", "missing-row", "receiver-change"])
def test_documented_investigation_is_checked_by_public_command(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    """規範由来の表と説明追記を受理し、行欠落と受理側だけの変更を拒否する。"""
    repo = git_repository.init_repository(tmp_path / "repo")
    path = tmp_path / "plan.md"
    bug = path.with_suffix(".bugs.md")
    content = _documented_investigation()
    if case == "explanation":
        content = content.replace("# 原因分析\n", "# 原因分析\n\n分析を利用する担当者向けの補足説明。\n", 1)
    elif case == "missing-row":
        lines = content.splitlines()
        content = "\n".join(lines[:-1]) + "\n"
    elif case == "receiver-change":
        monkeypatch.setattr(sections_bugs, "PLAN_BUG_TABLE_ROWS", (*sections_bugs.PLAN_BUG_TABLE_ROWS, "未合意の行"))
    bug.write_text(content, encoding="utf-8")
    # メインは調査ファイルを公開検査へ渡すための運搬役であり、検査対象の表はfixtureから作らない。
    path.write_text(plan_fixture.current_plan(repo=repo, work_type="バグ対応", bug_reference=str(bug)), encoding="utf-8")
    before = path.read_bytes(), bug.read_bytes()
    result = run_script.dispatch(
        argparse.Namespace(
            script_name="plan-check", script_args=[str(path), "--work-dir", str(repo), "--reject-migration-warnings"]
        )
    )
    output = capsys.readouterr()
    assert result == (1 if case in {"missing-row", "receiver-change"} else 0), output.err
    if result:
        assert "調査表" in output.err
    assert (path.read_bytes(), bug.read_bytes()) == before
