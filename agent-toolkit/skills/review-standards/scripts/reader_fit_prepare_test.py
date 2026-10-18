"""資料準備の公開操作と申告受理が同じ対象・差分を使うことを検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest
import reader_fit_declaration

from agent_toolkit._agents_server import launch_requests
from agent_toolkit._atk import run_script
from agent_toolkit._testing import git_repository as git


def _assert_reader_launch(item: dict[str, str], repository: pathlib.Path, review_kind: str) -> None:
    """公開prepareの保存先を文書どおり渡し、起動側が通常ファイルとして読めることを確かめる。"""
    assert pathlib.Path(item["current"]) == repository / item["path"]
    materials = {"比較元本文": item["before"], "今回差分": item["patch"], "現行本文": item["current"]}
    _model, _usage, _prompt, launch_kind, _handoff = launch_requests.task_document_request(
        "reader-fit-review",
        {
            "成果物": item["current"],
            "種別": "エンドユーザー向け文書",
            "読者像": "初めて操作するエンドユーザー",
            "修正範囲": json.dumps(list(materials.values()), ensure_ascii=False),
            "比較資料": json.dumps(materials, ensure_ascii=False),
            "レビュー種別": review_kind,
            "未解決事項": "なし",
        },
    )
    assert launch_kind == "explore"


def test_public_prepare_saves_first_and_repeat_review_inputs(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """変更・新規文書の保存資料を初回・再レビューの起動入力へ渡し、差分不変の省略も申告できる。"""
    repo = git.init_repository(tmp_path / "repo", files={"a.md": "旧A\n", "b.md": "旧B\n"}, commit_message="開始")
    start = git.git_output(repo, "rev-parse", "HEAD")
    for name in ("a", "b"):
        (repo / f"{name}.md").write_text(f"新{name.upper()}\n", encoding="utf-8")
    (repo / "new.md").write_text("新規文書\n", encoding="utf-8")
    previous = git.commit_all(repo, "初回")
    common = ["--repository", str(repo), "--review-start", start, "--output-dir", str(tmp_path / "output")]
    args = argparse.Namespace(script_name="reader-fit-prepare", script_args=[*common, "--review-head", previous])
    assert run_script.dispatch(args) == 0
    first = json.loads(capsys.readouterr().out)["files"]
    assert {item["path"] for item in first} == {"a.md", "b.md", "new.md"}
    for item in first:
        _assert_reader_launch(item, repo, "初回レビュー")
        if item["path"] == "new.md":
            assert not pathlib.Path(item["before"]).read_bytes()
        else:
            assert (
                pathlib.Path(item["before"]).read_text(encoding="utf-8")
                == git.git_output(repo, "show", f"{start}:{item['path']}") + "\n"
            )
        assert (
            pathlib.Path(item["patch"]).read_text(encoding="utf-8")
            == git.git_output(repo, "diff", start, previous, "--", item["path"]) + "\n"
        )
    (repo / "a.md").write_text("再変更A\n", encoding="utf-8")
    head = git.commit_all(repo, "再レビュー")
    args.script_args = [*common, "--review-head", head, "--previous-review-start", start, "--previous-review-head", previous]
    assert run_script.dispatch(args) == 0
    repeated = json.loads(capsys.readouterr().out)["files"]
    for item in repeated:
        _assert_reader_launch(item, repo, "再レビュー")
        assert (
            pathlib.Path(item["before"]).read_text(encoding="utf-8")
            == git.git_output(repo, "show", f"{previous}:{item['path']}") + "\n"
        )
        expected = git.git_output(repo, "diff", previous, head, "--", item["path"])
        assert pathlib.Path(item["patch"]).read_text(encoding="utf-8") == (expected + "\n" if expected else "")
        assert (item.get("omission") == "再点検対象なし") == (item["path"] in {"b.md", "new.md"})
    reader_fit_declaration.check(
        json.dumps(
            {
                "files": {
                    "a.md": {"sessions": ["11111111-2222-4333-8444-555555555555"]},
                    "b.md": {"omission": "再点検対象なし"},
                    "new.md": {"omission": "再点検対象なし"},
                }
            }
        ),
        repo,
        start,
        head,
        2,
        start,
        previous,
    )
