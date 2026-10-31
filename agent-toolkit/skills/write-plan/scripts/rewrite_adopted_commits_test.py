"""採用CLIから履歴書換え・保存・showまでの終了AWIの受入シナリオ。"""

import argparse
import json
import pathlib
import typing

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import run_script
from agent_toolkit._atk.wi import sync
from agent_toolkit._plan import commit_mapping
from agent_toolkit._testing import git_repository

WI_A = "20261020-234950-001.md"
WI_B = "20261020-234950-002.md"


def _commit(repo: pathlib.Path, subject: str) -> str:
    git_repository.git_output(repo, "commit", "--allow-empty", "-m", subject)
    return git_repository.git_output(repo, "rev-parse", "HEAD")


def _cli(home: pathlib.Path, arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as captured:
        atk.main(arguments, home=home)
    assert captured.value.code == 0


@pytest.mark.parametrize("failure", ["none", "push", "body-mismatch"])
@pytest.mark.parametrize("multiple", [False, True])
def test_adopt_multiple_then_rewrite_split_and_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failure: str, multiple: bool
) -> None:
    """複数commit採用、AWI別分割、無関係項目の保持、保存失敗後の再実行を公開操作で確かめる。"""
    notes, repo, remote = (tmp_path / name for name in ("notes", "repo", "remote"))
    for directory in (notes, repo, remote):
        directory.mkdir()
        git_repository.init_repository(directory, initial_branch="main")
    git_repository.git_output(remote, "config", "receive.denyCurrentBranch", "ignore")
    git_repository.git_output(notes, "remote", "add", "origin", str(remote))
    git_repository.git_output(repo, "remote", "add", "origin", "https://github.com/example/target.git")
    root = _commit(repo, "土台")
    old = _commit(repo, "旧実装")
    extra = _commit(repo, "共通処理")
    paths = []
    for name in (WI_A, WI_B):
        path = notes / "2026/10" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "---\nstate: processing\ntarget_repo: github.com/example/target\ntype: awi\nsource: test\n---\n\n要求\n",
            encoding="utf-8",
        )
        paths.append(path)
    git_repository.git_output(notes, "add", ".")
    git_repository.git_output(notes, "commit", "-m", "要求")
    git_repository.git_output(notes, "push", "-u", "origin", "main")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    commits = ["--commit", old, "--commit", extra] if multiple else ["--commit", old]
    _cli(tmp_path, ["wi", "adopt", WI_A, "--target-repo", str(repo), *commits, "--note", "説明"])
    _cli(tmp_path, ["wi", "adopt", WI_B, "--target-repo", str(repo), "--commit", root])
    _cli(tmp_path, ["wi", "show", WI_A, "--skip-pull"])
    shown = capsys.readouterr().out
    assert "- 対応commit件名: 旧実装" in shown
    if multiple:
        assert "- 対応commit件名: 共通処理" in shown
    original_a, original_b = (path.read_text(encoding="utf-8") for path in paths)
    assert original_a.count("- 対応commit:") == (2 if multiple else 1)
    handoff = tmp_path / "handoff.md"
    handoff.write_text("# 引き継ぎ\n", encoding="utf-8")
    commit_mapping.append_event(handoff, commit_mapping.commit_event(repo, extra, root, [WI_A, WI_B], {WI_A, WI_B}))
    git_repository.git_output(repo, "reset", "--hard", root)
    first, second, shared = (_commit(repo, subject) for subject in ("分割1", "分割2", "新共通処理"))
    rewrite = tmp_path / "rewrite.json"
    rewrite.write_text(
        json.dumps({old: [{"commit": first, "awi": [WI_A, WI_B]}, {"commit": second, "awi": [WI_A]}], extra: shared}),
        encoding="utf-8",
    )
    args = [
        "--worktree",
        str(repo),
        "--previous-head",
        extra,
        "--rewrite-map",
        str(rewrite),
        "--completed-step",
        "履歴書換え",
        "--result",
        "対応を継承",
        "--handoff",
        str(handoff),
    ]
    before_mapping = commit_mapping.mapping_path(handoff).read_bytes()
    original_push = sync.commit_and_push
    if failure == "push":
        git_repository.git_output(remote, "config", "receive.denyCurrentBranch", "refuse")
    if failure == "body-mismatch":
        # push後に別主体が本文を変えた場合を模し、保存した本文との比較を実際に通す。

        def alter_after_push(*args: typing.Any, **kwargs: typing.Any) -> int | None:
            result = original_push(*args, **kwargs)
            paths[0].write_text(paths[0].read_text(encoding="utf-8") + "外部追記\n", encoding="utf-8")
            return result

        monkeypatch.setattr(sync, "commit_and_push", alter_after_push)
    result = run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=args))
    if failure != "none":
        assert result == 1
        output = capsys.readouterr()
        assert "同じ引数" in output.err
        assert commit_mapping.mapping_path(handoff).read_bytes() == before_mapping
        if failure == "push":
            git_repository.git_output(remote, "config", "receive.denyCurrentBranch", "ignore")
        else:
            monkeypatch.setattr(sync, "commit_and_push", original_push)
            paths[0].write_text(paths[0].read_text(encoding="utf-8").removesuffix("外部追記\n"), encoding="utf-8")
        assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=args)) == 0
    else:
        assert result == 0
    updated = paths[0].read_text(encoding="utf-8")
    assert updated.count("- 対応commit:") == (3 if multiple else 2)
    assert "- メモ: 説明\n" in updated
    assert next(line for line in updated.splitlines() if line.startswith("- 処理日時:")) in original_a
    expected_commits = [(first, "分割1"), (second, "分割2")]
    if multiple:
        expected_commits.append((shared, "新共通処理"))
    for oid, subject in expected_commits:
        short = git_repository.git_output(repo, "rev-parse", "--short", oid)
        assert f"- 対応commit: {short}\n- 対応commit件名: {subject}\n" in updated
        git_repository.git_output(repo, "merge-base", "--is-ancestor", oid, "HEAD")
    assert paths[1].read_text(encoding="utf-8") == original_b
    remote_a = git_repository.git_output(remote, "show", f"main:2026/10/{WI_A}")
    assert remote_a == updated.rstrip()
    _cli(tmp_path, ["wi", "show", WI_A, "--skip-pull"])
    assert "- 対応commit件名: 分割1" in capsys.readouterr().out
    before = (updated, commit_mapping.mapping_path(handoff).read_bytes(), handoff.read_bytes())
    assert run_script.dispatch(argparse.Namespace(script_name="plan-rewrite", script_args=args)) == 0
    assert before == (
        paths[0].read_text(encoding="utf-8"),
        commit_mapping.mapping_path(handoff).read_bytes(),
        handoff.read_bytes(),
    )
    assert not git_repository.git_output(notes, "status", "--porcelain")
