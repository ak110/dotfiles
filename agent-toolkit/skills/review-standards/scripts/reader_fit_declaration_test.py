"""実Gitの差分と読者別探索の申告を、公開CLIの返却・batchから検証する。"""

from __future__ import annotations

import json
import pathlib

import check_exec_review_evidence as check
import pytest

from agent_toolkit._testing import git_repository as git

SESSION = "11111111-2222-4333-8444-555555555555"


def _args(
    tmp_path: pathlib.Path, start: str, head: str, declaration: str, *, previous: tuple[str, str] | None = None
) -> list[str]:
    table = tmp_path / "review.tsv"
    table.touch()
    result = [
        "なし",
        "--expected-head",
        head,
        "--review-table",
        str(table),
        "--round",
        "2" if previous else "1",
        "--return-result",
        "--review-start",
        start,
        "--reader-fit-review",
        declaration,
    ]
    if previous:
        result.extend(["--previous-review-start", previous[0], "--previous-review-head", previous[1]])
    return result


@pytest.mark.parametrize(
    "invalid",
    [
        "省略: 同系統の差分",
        "文章成果物なし",
        '{"files":{}}',
        '{"files":{"new.md":{"sessions":["label"]},"template.md.tmpl":{"sessions":["label"]}}}',
    ],
)
def test_public_reader_fit_declaration_covers_markdown_changes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str
) -> None:
    """改名後・テンプレートの申告欠落と規定外の省略を拒否し、全件のUUID申告で返却する。"""
    repo = git.init_repository(tmp_path / "repo", files={"old.md": "元\n", "deleted.md": "削除\n"}, commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "old.md").rename(repo / "new.md")
    (repo / "deleted.md").unlink()
    (repo / "template.md.tmpl").write_text("生成元\n", encoding="utf-8")
    head = git.commit_all(repo, "変更")
    monkeypatch.chdir(repo)
    assert check.main(_args(tmp_path, start, head, invalid)) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert "new.md" in captured.err and "template.md.tmpl" in captured.err
    assert "起動" in captured.err and "再実行" in captured.err
    declared = json.dumps({"files": {path: {"sessions": [SESSION]} for path in ("new.md", "template.md.tmpl")}})
    assert check.main(_args(tmp_path, start, head, declared)) == 0
    result = capsys.readouterr().out
    assert f"レビュー開始時点: {start}" in result and "状態: completed" in result
    assert check.main(_args(tmp_path, head, head, "文章成果物なし")) == 0


def test_public_reader_fit_recheck_compares_diffs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """基点と行位置が違っても同じ変更は省略でき、文字と空白の変更は省略できない。"""
    repo = git.init_repository(tmp_path / "repo", files={"a.md": "前置き\n旧\n"}, commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "a.md").write_text("前置き\n新\n", encoding="utf-8")
    previous_head = git.commit_all(repo, "初回レビュー")
    git.run_git(repo, "checkout", "--detach", start)
    (repo / "a.md").write_text("追加の文脈\n前置き\n旧\n", encoding="utf-8")
    new_start = git.commit_all(repo, "上流変更")
    (repo / "a.md").write_text("追加の文脈\n前置き\n新\n", encoding="utf-8")
    head = git.commit_all(repo, "基点を変更したレビュー")
    monkeypatch.chdir(repo)
    declaration = json.dumps({"files": {"a.md": {"omission": "再点検対象なし"}}}, ensure_ascii=False)
    assert check.main(_args(tmp_path, new_start, head, declaration, previous=(start, previous_head))) == 0
    assert f"前回確認版HEAD: {previous_head}" in capsys.readouterr().out
    (repo / "a.md").write_text("追加の文脈\n前置き\n新 \n", encoding="utf-8")
    changed = git.commit_all(repo, "空白も変更")
    assert check.main(_args(tmp_path, new_start, changed, declaration, previous=(start, previous_head))) == 1
    assert "変更内容が異なる" in capsys.readouterr().err


def test_public_batch_checks_reader_fit_declaration(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """batchでも同じ対象集合を使い、完全な申告と欠落の結果を別々に返す。"""
    repo = git.init_repository(tmp_path / "repo", commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    (repo / "a.md").write_text("本文\n", encoding="utf-8")
    head = git.commit_all(repo, "文書を追加")
    monkeypatch.chdir(repo)
    table = tmp_path / "review.tsv"
    table.touch()
    item = {
        "plan": "なし",
        "evidence": "なし",
        "wi": [],
        "reviewed_head": head,
        "review_table": str(table),
        "round": 1,
        "plans": [],
        "input_records": [],
        "review_start": start,
    }
    batch = tmp_path / "batch.json"
    batch.write_text(
        json.dumps(
            {
                "version": 1,
                "reviews": [
                    {**item, "reader_fit_review": json.dumps({"files": {"a.md": {"sessions": [SESSION]}}})},
                    {**item, "reader_fit_review": "文章成果物なし"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert check.main(["--batch", str(batch)]) == 1
    result = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [item["exit_code"] for item in result] == [0, 1]
