"""公開された再レビュー影響判定を実Gitの履歴と比較記録で検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository


def _run(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], data: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
    source = tmp_path / "input.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    args = argparse.Namespace(
        script_name="review-impact", script_args=["--input", str(source), "--output-dir", str(tmp_path / "comparison")]
    )
    result = run_script.dispatch(args)
    captured = capsys.readouterr()
    assert not captured.err, captured.err
    return result, [json.loads(line) for line in captured.out.splitlines()]


@pytest.mark.parametrize("change", ["unrelated", "same-file", "rewrite", "rebase", "excluded", "invalid"])
def test_public_impact_matches_history_and_file_conditions(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    """OIDのみの変更・履歴変更・ファイル交差・明示除外を区別し、比較不能を無影響にしない。"""
    repository = git_repository.init_repository(tmp_path / "repo", initial_branch="lane", commit_message="基点")
    start = git_repository.git_output(repository, "rev-parse", "HEAD")
    (repository / "target.txt").write_text("本文\n", encoding="utf-8")
    reviewed = git_repository.commit_all(repository, "対象実装")
    data: dict[str, Any] = {
        "version": 1,
        "head": "HEAD",
        "plans": [
            {
                "plan": str(tmp_path / "計画.md"),
                "start_head": start,
                "reviewed_head": reviewed,
                "commits": [reviewed],
                "excluded_commits": [reviewed] if change == "excluded" else [],
            }
        ],
    }
    if change == "rebase":
        git_repository.git_output(repository, "switch", "-c", "upstream", start)
        (repository / "other.txt").write_text("上流\n", encoding="utf-8")
        upstream = git_repository.commit_all(repository, "上流変更")
        git_repository.git_output(repository, "switch", "lane")
        git_repository.git_output(repository, "rebase", "--onto", upstream, start)
        data["rebase_base"] = upstream
        assert git_repository.git_output(repository, "rev-parse", "HEAD") != reviewed
    elif change == "rewrite":
        git_repository.git_output(repository, "commit", "--amend", "-qm", "対象実装の件名変更")
    elif change == "invalid":
        data["plans"][0]["commits"] = [start]
    else:
        name = "other.txt" if change == "unrelated" else "target.txt"
        (repository / name).write_text("修正\n", encoding="utf-8")
        git_repository.commit_all(repository, "追加修正")
    monkeypatch.chdir(repository)
    result, rows = _run(tmp_path, capsys, data)
    assert len(rows) == 1
    if change == "invalid":
        assert result == 1 and "error" in rows[0] and "affected" not in rows[0]
        return
    assert result == 0, rows
    row = rows[0]
    assert row["affected"] is (change in {"same-file", "rewrite"})
    assert bool(row["changed_commits"]) is (change == "rewrite")
    assert bool(row["overlapping_files"]) is (change == "same-file")
    for key in ("range_diff_record", "changed_files_record"):
        record = json.loads(pathlib.Path(row[key]).read_text(encoding="utf-8"))
        assert record["exit_code"] == 0
        assert pathlib.Path(record["stdout_path"]).is_file()
        assert pathlib.Path(record["stderr_path"]).read_text(encoding="utf-8") == ""
