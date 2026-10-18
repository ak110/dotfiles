"""投入・本文編集の公開CLIでorigin以外への誤投入を防ぐ契約を検証する。"""

import collections.abc
import os
import pathlib
import subprocess
import sys

import pytest

from agent_toolkit._testing import git_repository
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY
from agent_toolkit.atk_test import _setup_notes

_ORIGIN = "github.com/example/current"
_UPSTREAM = "github.com/example/template"


def _invoke(
    repo: pathlib.Path,
    notes: pathlib.Path,
    argv: list[str],
    host_environ: collections.abc.Callable[[], dict[str, str]],
) -> subprocess.CompletedProcess[str]:
    """隔離を維持した実Pythonプロセスでcwdを固定して公開CLIを実行する。"""
    env = dict(os.environ)
    env["PATH"] = host_environ()["PATH"]
    env["AGENT_TOOLKIT_PRIVATE_NOTES"] = str(notes)
    return subprocess.run(
        [sys.executable, "-m", "agent_toolkit.atk", *argv],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


def _setup(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    """実リモートへ接続しないテスト用のworktreeと保存先を準備する。"""
    repo = tmp_path / "project"
    git_repository.init_repository(
        repo, origin=f"https://{_ORIGIN}.git", files={"README.md": "テスト\n"}, commit_message="初期状態"
    )
    git_repository.run_git(repo, "remote", "add", "upstream", "git@github.com:example/template.git")
    notes = _setup_notes(tmp_path)
    git_repository.init_repository(notes, files={"README.md": "テスト\n"}, commit_message="初期状態")
    return repo, notes


@pytest.mark.parametrize("mode", ["option", "frontmatter", "batch", "edit"])
def test_non_origin_target_is_rejected_without_saving(
    tmp_path: pathlib.Path,
    host_environ: collections.abc.Callable[[], dict[str, str]],
    mode: str,
) -> None:
    """引数・frontmatter・一括投入・編集が登録済みupstreamのURLを同じ診断で拒否する。"""
    repo, notes = _setup(tmp_path)
    body = tmp_path / "body.md"
    before_head = git_repository.git_output(notes, "rev-parse", "HEAD")
    content = f"---\ntarget_repo: {_UPSTREAM}\ntype: awi\nsource: agent\n---\n\n{AGENT_AWI_BODY}\n"
    if mode == "option":
        body.write_text(AGENT_AWI_BODY, encoding="utf-8")
        argv = ["wi", "add", "--dry-run", "--source=agent", f"--target-repo={_UPSTREAM}"]
    elif mode == "batch":
        body.write_text(f"# awi\n## target_repo: {_UPSTREAM}\n### item.md [inbox]\n{content}", encoding="utf-8")
        argv = ["wi", "add", "--batch"]
    elif mode == "edit":
        saved = notes / "inbox" / "item.md"
        saved.write_text(content.replace(_UPSTREAM, _ORIGIN), encoding="utf-8")
        git_repository.commit_all(notes, "既存WI")
        before_head = git_repository.git_output(notes, "rev-parse", "HEAD")
        body.write_text(content, encoding="utf-8")
        argv = ["wi", "edit", "item.md", "--dry-run", f"--target-repo={_ORIGIN}"]
    else:
        body.write_text(content.replace("type: awi\n", ""), encoding="utf-8")
        argv = ["wi", "add", "--dry-run", f"--target-repo={_ORIGIN}"]
    before_files = {p.name: p.read_bytes() for p in (notes / "inbox").glob("*.md")}
    result = _invoke(repo, notes, [*argv, "--body-file", str(body)], host_environ)
    assert result.returncode != 0, result.stdout + result.stderr
    assert "upstream" in result.stderr
    assert _ORIGIN in result.stderr
    assert "--target-repo" in result.stderr
    assert "ローカル作業ツリーのパス" in result.stderr
    assert git_repository.git_output(notes, "rev-parse", "HEAD") == before_head
    assert {p.name: p.read_bytes() for p in (notes / "inbox").glob("*.md")} == before_files


@pytest.mark.parametrize("target", ["origin", "path", "unregistered", "same-origin-alias", "outside-git", "no-origin"])
def test_target_url_guard_keeps_supported_inputs(
    tmp_path: pathlib.Path,
    host_environ: collections.abc.Callable[[], dict[str, str]],
    target: str,
) -> None:
    """origin・ローカルパス・未登録URLと検査対象外cwdを受理する。"""
    repo, notes = _setup(tmp_path)
    value = _ORIGIN
    if target == "path":
        value = str(repo)
    elif target == "unregistered":
        value = "github.com/example/another"
    elif target == "same-origin-alias":
        git_repository.run_git(repo, "remote", "set-url", "upstream", "git@github.com:example/current.git")
    elif target == "outside-git":
        repo = tmp_path / "outside"
        repo.mkdir()
        value = _UPSTREAM
    elif target == "no-origin":
        git_repository.run_git(repo, "remote", "remove", "origin")
        value = _UPSTREAM
    body = tmp_path / "body.md"
    body.write_text(AGENT_AWI_BODY, encoding="utf-8")
    result = _invoke(
        repo,
        notes,
        ["wi", "add", "--dry-run", "--source=agent", f"--target-repo={value}", "--body-file", str(body)],
        host_environ,
    )
    assert result.returncode == 0, result.stdout + result.stderr
