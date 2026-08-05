"""計画レビュー用隔離作業領域CLIの公開契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import stat
import subprocess
import sys
import typing

import _review_workspace as review_workspace
import pytest

_SCRIPT = pathlib.Path(__file__).with_name("_review_workspace.py")


def _run(*args: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *(str(arg) for arg in args)],
        check=False,
        capture_output=True,
        text=True,
    )


def _repo(tmp_path: pathlib.Path) -> pathlib.Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "commit.gpgsign", "false"], check=True)
    (repo / "staged.txt").write_text("base staged\n", encoding="utf-8")
    (repo / "unstaged.bin").write_bytes(b"base\x00unstaged\n")
    subprocess.run(["git", "-C", str(repo), "add", "staged.txt", "unstaged.bin"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "initial"], check=True)
    return repo


def _create(
    repo: pathlib.Path,
    plan: pathlib.Path,
    workspace: pathlib.Path,
    *,
    conditional_source_repo: pathlib.Path | None = None,
) -> subprocess.CompletedProcess[str]:
    args: list[object] = ["create", "--source-repo", repo]
    if conditional_source_repo is not None:
        args.extend(("--conditional-source-repo", conditional_source_repo))
    args.extend(("--plan-file", plan, "--output-dir", workspace))
    return _run(*args)


def _finish(workspace: pathlib.Path) -> subprocess.CompletedProcess[str]:
    return _run("finish", "--workspace-dir", workspace)


def _payload(result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict)
    return payload


def _git_bytes(repo: pathlib.Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    ).stdout


def test_create_reproduces_index_worktree_and_untracked_state(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    (repo / "staged.txt").write_text("staged change\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "staged.txt"], check=True)
    (repo / "unstaged.bin").write_bytes(b"dirty\x00worktree\n")
    untracked = repo / "untracked.bin"
    untracked.write_bytes(b"untracked\x00content")
    untracked.chmod(0o740)
    (repo / "untracked-link").symlink_to("untracked.bin")
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    result = _create(repo, plan, workspace)

    assert result.returncode == 0, result.stderr
    payload = _payload(result)
    review_repo = pathlib.Path(str(payload["review_repo"]))
    assert _git_bytes(repo, "rev-parse", "HEAD") == _git_bytes(review_repo, "rev-parse", "HEAD")
    assert _git_bytes(repo, "diff", "--cached", "--binary", "HEAD") == _git_bytes(
        review_repo,
        "diff",
        "--cached",
        "--binary",
        "HEAD",
    )
    assert _git_bytes(repo, "diff", "--binary") == _git_bytes(review_repo, "diff", "--binary")
    assert (review_repo / "untracked.bin").read_bytes() == untracked.read_bytes()
    assert stat.S_IMODE((review_repo / "untracked.bin").stat().st_mode) == 0o740
    assert os.readlink(review_repo / "untracked-link") == "untracked.bin"
    source_common = _git_bytes(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    review_common = _git_bytes(review_repo, "rev-parse", "--path-format=absolute", "--git-common-dir").strip()
    assert source_common != review_common
    assert stat.S_IMODE(workspace.stat().st_mode) == 0o700
    assert stat.S_IMODE((workspace / "workspace.json").stat().st_mode) == 0o600


def test_finish_accepts_only_plan_copy_change_and_emits_patch(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n\n変更前\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    created = _create(repo, plan, workspace)
    assert created.returncode == 0, created.stderr
    review_plan = pathlib.Path(str(_payload(created)["review_plan"]))
    review_plan.write_text("# 計画\n\n変更後\n", encoding="utf-8")

    result = _finish(workspace)

    assert result.returncode == 0, result.stderr
    payload = _payload(result)
    assert payload["source_plan_unchanged"] is True
    assert payload["conditional_source_repo_unchanged"] is None
    assert payload["conditional_source_repo_compare"] is None
    assert payload["review_repo_unchanged"] is True
    assert payload["plan_changed"] is True
    patch = pathlib.Path(str(payload["plan_diff"]))
    assert b"-\xe5\xa4\x89\xe6\x9b\xb4\xe5\x89\x8d" in patch.read_bytes()
    assert b"+\xe5\xa4\x89\xe6\x9b\xb4\xe5\xbe\x8c" in patch.read_bytes()


def test_finish_rejects_review_repository_change(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    (repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", ".gitignore"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "ignore"], check=True)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    created = _create(repo, plan, workspace)
    assert created.returncode == 0, created.stderr
    review_repo = pathlib.Path(str(_payload(created)["review_repo"]))
    ignored = review_repo / "ignored" / "cache.bin"
    ignored.parent.mkdir()
    ignored.write_bytes(b"ignored\x00change")

    result = _finish(workspace)

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["review_repo_unchanged"] is False
    compare = payload["review_repo_compare"]
    assert isinstance(compare, dict)
    typed_compare = typing.cast(dict[str, object], compare)
    assert not typed_compare["untracked_added"]
    files_compare = payload["review_files_compare"]
    assert isinstance(files_compare, dict)
    typed_files_compare = typing.cast(dict[str, object], files_compare)
    assert typed_files_compare["added"] == ["ignored/cache.bin"]


def test_finish_reports_modified_and_removed_review_files(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    created = _create(repo, plan, workspace)
    assert created.returncode == 0, created.stderr
    review_repo = pathlib.Path(str(_payload(created)["review_repo"]))
    (review_repo / "staged.txt").write_text("modified\n", encoding="utf-8")
    (review_repo / "unstaged.bin").unlink()

    result = _finish(workspace)

    assert result.returncode == 1
    files_compare = _payload(result)["review_files_compare"]
    assert isinstance(files_compare, dict)
    typed_files_compare = typing.cast(dict[str, object], files_compare)
    assert typed_files_compare["modified"] == ["staged.txt"]
    assert typed_files_compare["removed"] == ["unstaged.bin"]


def test_finish_rejects_source_plan_change(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    created = _create(repo, plan, workspace)
    assert created.returncode == 0, created.stderr
    plan.write_text("# 別の計画\n", encoding="utf-8")

    result = _finish(workspace)

    assert result.returncode == 1
    assert _payload(result)["source_plan_unchanged"] is False


def test_finish_rejects_source_repository_change(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    created = _create(repo, plan, workspace)
    assert created.returncode == 0, created.stderr
    (repo / "new.txt").write_text("changed\n", encoding="utf-8")

    result = _finish(workspace)

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["source_repo_unchanged"] is False
    compare = payload["source_repo_compare"]
    assert isinstance(compare, dict)
    typed_compare = typing.cast(dict[str, object], compare)
    assert typed_compare["untracked_added"] == ["new.txt"]


def test_finish_rejects_conditional_source_repository_change(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    conditional_source = tmp_path / "conditional"
    subprocess.run(["git", "clone", "-q", str(repo), str(conditional_source)], check=True)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    created = _create(repo, plan, workspace, conditional_source_repo=conditional_source)
    assert created.returncode == 0, created.stderr
    created_payload = _payload(created)
    assert created_payload["conditional_source_repo"] == str(conditional_source)
    (conditional_source / "new.txt").write_text("changed\n", encoding="utf-8")

    result = _finish(workspace)

    assert result.returncode == 1
    payload = _payload(result)
    assert payload["conditional_source_repo_unchanged"] is False
    compare = payload["conditional_source_repo_compare"]
    assert isinstance(compare, dict)
    typed_compare = typing.cast(dict[str, object], compare)
    assert typed_compare["untracked_added"] == ["new.txt"]


@pytest.mark.skipif(os.name == "nt", reason="umaskはPOSIX環境でだけmodeへ影響する")
def test_create_preserves_untracked_mode_under_restrictive_umask(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    untracked = repo / "executable"
    untracked.write_text("#!/bin/sh\n", encoding="utf-8")
    untracked.chmod(0o751)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    previous_umask = os.umask(0o077)
    try:
        result = _create(repo, plan, workspace)
    finally:
        os.umask(previous_umask)

    assert result.returncode == 0, result.stderr
    review_repo = pathlib.Path(str(_payload(result)["review_repo"]))
    assert stat.S_IMODE((review_repo / "executable").stat().st_mode) == 0o751


def test_create_does_not_require_fchmod_outside_posix(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = _repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    monkeypatch.setattr(review_workspace, "_USE_POSIX_MODE", False)
    monkeypatch.setattr(
        review_workspace.os,
        "fchmod",
        lambda *_args: pytest.fail("POSIX外でfchmodを呼び出した"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            str(_SCRIPT),
            "create",
            "--source-repo",
            str(repo),
            "--plan-file",
            str(plan),
            "--output-dir",
            str(workspace),
        ],
    )

    with pytest.raises(SystemExit) as exit_info:
        review_workspace.main()

    assert exit_info.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    assert payload["workspace_dir"] == str(workspace)


def test_create_rejects_output_inside_source_repository(tmp_path: pathlib.Path) -> None:
    repo = _repo(tmp_path)
    plan = tmp_path / "plan.md"
    plan.write_text("# 計画\n", encoding="utf-8")

    result = _create(repo, plan, repo / "workspace")

    assert result.returncode == 2
    assert "検査対象リポジトリ内" in result.stderr
