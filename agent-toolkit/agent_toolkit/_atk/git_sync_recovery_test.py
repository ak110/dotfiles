# pylint: disable=protected-access

import datetime
import os
import pathlib
import re
import shlex
import subprocess

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import background_sync, frontmatter
from agent_toolkit._atk.wi import mutations as _atk_wi_mutations
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.layout import entry_path, entry_state_of
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.git_sync_support import (  # noqa: F401  # pylint: disable=unused-import
    REAL_GIT_CPU,
    _agent_output_fixture,
    _remote_sync_fixture,
)


def _init_diverged_mq_repos(
    tmp_path: pathlib.Path, *, filename: str = "20260831-101752-001.md"
) -> tuple[pathlib.Path, pathlib.Path]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    local = tmp_path / "local"
    peer = tmp_path / "peer"
    origin.mkdir()
    git_repository.init_bare_repository(origin)
    seed.mkdir()
    git_repository.init_repository(seed, initial_branch="main")
    git_repository.run_git(seed, "config", "user.name", "sync-test")
    git_repository.run_git(seed, "config", "user.email", "sync-test@example.invalid")
    source = entry_path(seed, filename, fallback_date=datetime.date(2026, 8, 31))
    source.parent.mkdir(parents=True)
    source.write_text("---\ntype: awi\nstate: processing\n---\n\n同じ項目\n", encoding="utf-8")
    git_repository.run_git(seed, "add", ".")
    git_repository.run_git(seed, "commit", "-m", "base")
    git_repository.run_git(seed, "remote", "add", "origin", str(origin))
    git_repository.run_git(seed, "push", "-u", "origin", "main")
    for clone in (local, peer):
        git_repository.run_git(tmp_path, "clone", "--branch", "main", str(origin), str(clone))
        git_repository.run_git(clone, "config", "user.name", "sync-test")
        git_repository.run_git(clone, "config", "user.email", "sync-test@example.invalid")
    return local, peer


def _finish_entry(
    repo: pathlib.Path, timestamp: str, *, note: str | None = None, filename: str = "20260831-101752-001.md"
) -> None:
    source = entry_path(repo, filename)
    parsed = frontmatter.parse_frontmatter(source.read_text(encoding="utf-8"))
    assert parsed is not None
    metadata, body = parsed
    metadata["state"] = "adopted"
    body += f"\n## 処理結果\n\n- 採否: adopted\n- 処理日時: {timestamp}\n"
    if note is not None:
        body += f"- メモ: {note}\n"
    source.write_text(frontmatter.serialize_frontmatter(metadata, body), encoding="utf-8")
    git_repository.run_git(repo, "add", "--all")
    git_repository.run_git(repo, "commit", "-m", "chore: process 1 entry (adopted)")


def _create_matching_tree_divergence(local: pathlib.Path, peer: pathlib.Path) -> str:
    for repo, message in ((local, "local equivalent"), (peer, "peer equivalent")):
        (repo / "equivalent.txt").write_text("same\n", encoding="utf-8")
        git_repository.run_git(repo, "add", "equivalent.txt")
        git_repository.run_git(repo, "commit", "-m", message)
    git_repository.run_git(peer, "push")
    return git_repository.run_git(peer, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.usefixtures("remote_sync")
def test_push_pending_defers_diverged_history_when_worktree_is_dirty(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """履歴分岐と無関係なdirty差分が同時にある場合、rebaseせずpushを保留する。"""
    repo = tmp_path / "repo with spaces"
    repo.mkdir()
    calls: list[list[str]] = []

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del forward_error_output
        del cwd
        calls.append(args)
        if args == ["push"]:
            raise subprocess.CalledProcessError(1, ["git", *args])
        if args == ["merge-base", "--is-ancestor", "HEAD", "@{u}"]:
            raise subprocess.CalledProcessError(1, ["git", *args])
        if args == ["merge-base", "--is-ancestor", "@{u}", "HEAD"]:
            raise subprocess.CalledProcessError(1, ["git", *args])

    def result_runner(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        if args == ["diff", "--quiet", "HEAD", "@{u}"]:
            return subprocess.CompletedProcess(["git", *args], 1, "", "")
        if args == ["status", "--porcelain"]:
            return subprocess.CompletedProcess(["git", *args], 0, " M unrelated.txt\n", "")
        if args == ["rev-list", "--left-right", "--count", "HEAD...@{u}"]:
            return subprocess.CompletedProcess(["git", *args], 0, "1\t1\n", "")
        if args == ["rev-list", "--count", "@{u}..HEAD"]:
            return subprocess.CompletedProcess(["git", *args], 0, "1\n", "")
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    with _atk_git_sync.repo_lock(repo):
        _atk_git_sync.push_pending_commits(repo, run_git=run_git, result_runner=result_runner)

    assert ["-c", "submodule.recurse=false", "rebase", "@{u}"] not in calls
    stderr = capsys.readouterr().err
    assert "Git履歴が分岐している" in stderr
    lines = stderr.splitlines()
    assert lines[0].startswith(f"警告: {_atk_git_sync.PUSH_DEFERRED_MESSAGE}")
    assert "ローカルのみ1件、upstreamのみ1件" in stderr
    assert "未push commit: 1件" in stderr
    assert "失敗: " not in stderr
    assert lines[-1].startswith("次の操作: ")
    commands = [shlex.split(text) for text in re.findall(r"`(git -C [^`]+)`", lines[-1])]
    assert commands == [["git", "-C", str(repo), "status"]]
    assert "`atk wi commit`を実行してください。" in lines[-1]
    assert "元の`atk`操作" not in lines[-1]


@REAL_GIT_CPU
def test_wi_hold_defers_dirty_divergence_and_commit_recovers(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """状態変更の成功・push保留・dirtyの失敗・clean後の回復を公開CLIで判定する。"""
    local, peer = _init_diverged_mq_repos(tmp_path)
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(local))
    monkeypatch.chdir(tmp_path)
    original_run = _wi_sync.run_git
    injected = False
    dirty = local / "unrelated.tmp"

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True, **_kwargs: object) -> None:
        nonlocal injected
        if args[0] == "commit" and not injected:
            injected = True
            (peer / "peer.txt").write_text("他のcloneの変更\n", encoding="utf-8")
            git_repository.run_git(peer, "add", "peer.txt")
            git_repository.run_git(peer, "commit", "-m", "peer update")
            git_repository.run_git(peer, "push")
            dirty.write_text("無関係な未追跡差分\n", encoding="utf-8")
        original_run(args, cwd, forward_error_output=forward_error_output)

    monkeypatch.setattr(_wi_sync, "run_git", run_git)
    monkeypatch.setattr(background_sync, "schedule", lambda _notes: None)
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "hold", "20260831-101752-001.md"], home=tmp_path)
    held = capsys.readouterr()
    assert held.out.splitlines()[0].startswith("成功: ")
    assert "失敗: " not in held.err
    assert background_sync.run(local) is False
    assert background_sync.read_status(local)["state"] == "recovery-required"
    assert entry_state_of(entry_path(local, "20260831-101752-001.md")) == "hold"

    with pytest.raises(SystemExit, match="1"):
        atk.main(["wi", "commit"], home=tmp_path)
    blocked = capsys.readouterr()
    assert blocked.err.count("失敗: ") == 1
    next_action = blocked.err.rsplit("次の操作: ", 1)[1]
    assert next_action.strip()
    assert dirty.is_file()
    assert not _atk_git_sync.is_rebase_in_progress(local)

    dirty.unlink()
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "commit"], home=tmp_path)
    assert "1件のcommitをpushした" in capsys.readouterr().out
    assert git_repository.run_git(local, "rev-list", "--left-right", "--count", "HEAD...@{u}").stdout.split() == ["0", "0"]


@pytest.mark.usefixtures("remote_sync")
def test_push_suppresses_output_of_recovered_first_push(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初のpush失敗をfast-forwardと再pushで解消した場合はその出力を表示しない。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[list[str], bool]] = []

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del cwd
        calls.append((args, forward_error_output))
        if args == ["push"] and len([call for call, _forward in calls if call == ["push"]]) == 1:
            raise subprocess.CalledProcessError(
                1,
                ["git", *args],
                output="rejected stdout\n",
                stderr="rejected stderr\n",
            )
        if args == ["merge-base", "--is-ancestor", "@{u}", "HEAD"]:
            raise subprocess.CalledProcessError(1, ["git", *args])

    with _atk_git_sync.repo_lock(repo):
        _atk_git_sync.push_pending_commits(repo, run_git=run_git)

    assert calls[0] == (["push"], False)
    assert calls[-2:] == [(["merge", "--ff-only", "@{u}"], True), (["push"], True)]
    assert capsys.readouterr().err == ""


@pytest.mark.usefixtures("remote_sync")
def test_push_reports_output_of_unresolved_first_push(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初のpush失敗を解消できない場合は保持した出力を表示する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    original_error = subprocess.CalledProcessError(
        1,
        ["git", "push"],
        output="rejected stdout\n",
        stderr="rejected stderr\n",
    )

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del cwd, forward_error_output
        if args == ["push"]:
            raise original_error
        raise subprocess.CalledProcessError(1, ["git", *args])

    with _atk_git_sync.repo_lock(repo), pytest.raises(subprocess.CalledProcessError) as exc_info:
        _atk_git_sync.push_pending_commits(repo, run_git=run_git)

    assert exc_info.value is original_error
    stderr = capsys.readouterr().err
    assert "rejected stdout\nrejected stderr\n" in stderr
    assert "private-notesのpushに失敗した" in stderr


@pytest.mark.usefixtures("remote_sync")
def test_pull_suppresses_output_of_recovered_first_merge(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初のff-only失敗をrebaseで解消した場合はその出力を表示しない。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    calls: list[tuple[list[str], bool]] = []

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del cwd
        calls.append((args, forward_error_output))
        if args == ["merge", "--ff-only", "@{u}"]:
            raise subprocess.CalledProcessError(
                1,
                ["git", *args],
                output="merge stdout\n",
                stderr="merge stderr\n",
            )
        if args[:2] == ["merge-base", "--is-ancestor"]:
            raise subprocess.CalledProcessError(1, ["git", *args])

    def result_runner(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        return subprocess.CompletedProcess(["git", *args], 1 if args[:2] == ["diff", "--quiet"] else 0, "", "")

    with _atk_git_sync.repo_lock(repo):
        _atk_git_sync.pull(repo, run_git=run_git, result_runner=result_runner)

    assert (["merge", "--ff-only", "@{u}"], False) in calls
    assert calls[-1] == (["-c", "submodule.recurse=false", "rebase", "@{u}"], True)
    assert capsys.readouterr().err == ""


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("agent_environment", [False, True])
@REAL_GIT_CPU
def test_sync_recovers_matching_tree_divergence(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    dirty: bool,
    monkeypatch: pytest.MonkeyPatch,
    agent_environment: bool,
) -> None:
    if agent_environment:
        monkeypatch.setenv("AI_AGENT", "1")
    local, peer = _init_diverged_mq_repos(tmp_path)
    upstream = _create_matching_tree_divergence(local, peer)
    if dirty:
        source = entry_path(local, "20260831-101752-001.md")
        source.write_text(source.read_text(encoding="utf-8") + "\nstaged\n", encoding="utf-8")
        git_repository.run_git(local, "add", str(source.relative_to(local)))
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    status_before = git_repository.run_git(local, "status", "--porcelain").stdout

    with _atk_git_sync.repo_lock(local):
        if operation == "pull":
            _wi_sync.pull(local)
        else:
            _atk_git_sync.push_pending_commits(local)

    assert git_repository.run_git(local, "rev-parse", "HEAD").stdout.strip() == upstream
    assert git_repository.run_git(local, "status", "--porcelain").stdout == status_before
    assert not capsys.readouterr().err


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.parametrize("agent_environment", [False, True])
@pytest.mark.parametrize(
    "filename",
    [
        "20260831-101752-001.md",
        "日本語 space.md",
        pytest.param(
            "日本語 space\n改行.md",
            marks=pytest.mark.skipif(os.name == "nt", reason="Windowsのファイル名は改行を許可しない"),
        ),
    ],
)
@REAL_GIT_CPU
def test_mq_sync_recovers_duplicate_terminal_commit(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
    agent_environment: bool,
    filename: str,
) -> None:
    """処理日時だけが異なる同一終了はpull・pushのどちらでもupstreamへ揃える。"""
    if agent_environment:
        monkeypatch.setenv("AI_AGENT", "1")
    local, peer = _init_diverged_mq_repos(tmp_path, filename=filename)
    _finish_entry(local, "2026-08-31T20:42:20+00:00", filename=filename)
    _finish_entry(peer, "2026-08-31T20:48:40+00:00", filename=filename)
    git_repository.run_git(peer, "push")
    upstream = git_repository.run_git(peer, "rev-parse", "HEAD").stdout.strip()

    if operation == "pull":
        with _atk_git_sync.repo_lock(local):
            _wi_sync.pull(local)
    else:
        assert _atk_wi_mutations.commit_entries(local).changed is False

    assert git_repository.run_git(local, "rev-parse", "HEAD").stdout.strip() == upstream
    assert git_repository.run_git(local, "status", "--porcelain").stdout == ""
    assert not capsys.readouterr().err


@REAL_GIT_CPU
def test_mq_pull_rebases_clean_divergence(
    tmp_path: pathlib.Path,
) -> None:
    local, peer = _init_diverged_mq_repos(tmp_path)
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    git_repository.run_git(local, "add", "local.txt")
    git_repository.run_git(local, "commit", "-m", "local change")
    (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
    git_repository.run_git(peer, "add", "remote.txt")
    git_repository.run_git(peer, "commit", "-m", "remote change")
    git_repository.run_git(peer, "push")
    upstream = git_repository.run_git(peer, "rev-parse", "HEAD").stdout.strip()

    with _atk_git_sync.repo_lock(local):
        _wi_sync.pull(local)

    assert git_repository.run_git(local, "merge-base", "--is-ancestor", upstream, "HEAD").returncode == 0
    assert git_repository.run_git(local, "log", "-1", "--format=%s").stdout.strip() == "local change"
    assert git_repository.run_git(local, "status", "--porcelain").stdout == ""


@REAL_GIT_CPU
def test_mq_pull_reports_dirty_divergence_without_rewriting(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notes_root = tmp_path / "notes with spaces"
    notes_root.mkdir()
    local, peer = _init_diverged_mq_repos(notes_root)
    (local / "local.txt").write_text("local\n", encoding="utf-8")
    git_repository.run_git(local, "add", "local.txt")
    git_repository.run_git(local, "commit", "-m", "local change")
    local_head = git_repository.run_git(local, "rev-parse", "HEAD").stdout.strip()
    (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
    git_repository.run_git(peer, "add", "remote.txt")
    git_repository.run_git(peer, "commit", "-m", "remote change")
    git_repository.run_git(peer, "push")
    (local / "untracked.txt").write_text("dirty\n", encoding="utf-8")

    with _atk_git_sync.repo_lock(local), pytest.raises(subprocess.CalledProcessError):
        _wi_sync.pull(local)

    assert git_repository.run_git(local, "rev-parse", "HEAD").stdout.strip() == local_head
    assert not _atk_git_sync.is_rebase_in_progress(local)
    stderr = capsys.readouterr().err
    assert "Git履歴が分岐している（ローカルのみ1件、upstreamのみ1件）" in stderr
    assert "local.txt" in stderr
    assert "remote.txt" in stderr
    commands = [shlex.split(text) for text in re.findall(r"`(git -C [^`]+)`", stderr)]
    assert commands and all(argv[:3] == ["git", "-C", str(local)] for argv in commands)
    assert [argv[-1] for argv in commands] == [
        "status",
        "HEAD...@{u}",
        "@{u}",
        "<競合解消済みパス>",
        "--continue",
        "push",
        "--skip",
        "--abort",
    ]
    assert "untracked.txt" in git_repository.git_output(peer, *commands[0][1:])
    # 分岐の報告に汎用の同期失敗行を重ねず、原因別の失敗行1本にする。
    assert stderr.count("失敗: ") == 1
    assert "private-notesのpullに失敗した" not in stderr
    lines = stderr.splitlines()
    failure_end = next(index for index, line in enumerate(lines) if line.startswith("次の操作: "))
    assert "rebase" in lines[failure_end] and "を実行してください。" in lines[failure_end]


@pytest.mark.parametrize(
    "filename",
    [
        "20260831-101752-001.md",
        "日本語 space.md",
        pytest.param(
            "日本語 space\n改行.md",
            marks=pytest.mark.skipif(os.name == "nt", reason="Windowsのファイル名は改行を許可しない"),
        ),
    ],
)
@REAL_GIT_CPU
def test_mq_pull_reports_rebase_failure_and_preserves_state(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    filename: str,
) -> None:
    """rebase競合時は中間状態を保持し、競合解消手順を表示する。"""
    notes_root = tmp_path / "notes with spaces"
    notes_root.mkdir()
    local, peer = _init_diverged_mq_repos(notes_root, filename=filename)
    _finish_entry(local, "2026-08-31T20:42:20+00:00", note="local", filename=filename)
    local_head = git_repository.run_git(local, "rev-parse", "HEAD").stdout.strip()
    _finish_entry(peer, "2026-08-31T20:48:40+00:00", note="remote", filename=filename)
    git_repository.run_git(peer, "push")

    with _atk_git_sync.repo_lock(local), pytest.raises(subprocess.CalledProcessError) as exc_info:
        _wi_sync.pull(local)

    assert exc_info.value.cmd[-3:] == ["merge", "--ff-only", "@{u}"]
    assert _atk_git_sync.is_rebase_in_progress(local)
    assert git_repository.run_git(local, "rev-parse", "ORIG_HEAD").stdout.strip() == local_head
    stderr = capsys.readouterr().err
    assert "rebaseに失敗したため、rebase状態を保持した" in stderr
    relative = entry_path(local, filename).relative_to(local).as_posix()
    assert f"競合ファイル: {relative}\n" in stderr
    assert "Git履歴が分岐している" not in stderr
    assert stderr.count("失敗: ") == 1
    assert "rebase --continue" in next(line for line in stderr.splitlines() if line.startswith("次の操作: "))
    commands = [shlex.split(text) for text in re.findall(r"`(git -C [^`]+)`", stderr)]
    assert commands and all(argv[:3] == ["git", "-C", str(local)] for argv in commands)
    assert [argv[-1] for argv in commands] == ["<競合解消済みパス>", "--continue", "push", "--skip", "--abort"]
    peer_head = git_repository.git_output(peer, "rev-parse", "HEAD")
    git_repository.run_git(peer, *commands[-1][1:])
    assert not _atk_git_sync.is_rebase_in_progress(local)
    assert git_repository.git_output(local, "rev-parse", "HEAD") == local_head
    assert git_repository.git_output(peer, "rev-parse", "HEAD") == peer_head


@REAL_GIT_CPU
def test_conflicting_divergence_keeps_rebase_and_rejects_next_mutation(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Webの同期で競合したrebaseは状態と変更を保持し、次のWebの更新を変更前に拒否する。

    Webの保存はremote同期を省略するため、変更前に確かめないと、rebase中の作業ツリーへ書き込んでから
    commit時に拒否し、競合の解消中の作業ツリーへ無関係な変更を残す。
    """
    local, peer = _init_diverged_mq_repos(tmp_path)
    original = "---\ntype: awi\nstate: inbox\ntarget_repo: github.com/example/repo\n---\n\n別の項目\n"
    other = entry_path(local, "20260831-101800-001.md")
    other.parent.mkdir(parents=True, exist_ok=True)
    other.write_text(original, encoding="utf-8")
    git_repository.run_git(local, "add", "--all")
    git_repository.run_git(local, "commit", "-m", "add other entry")
    git_repository.run_git(local, "push")
    git_repository.run_git(peer, "pull")
    _finish_entry(local, "2026-08-31T20:42:20+00:00", note="local")
    local_head = git_repository.run_git(local, "rev-parse", "HEAD").stdout.strip()
    _finish_entry(peer, "2026-08-31T20:48:40+00:00", note="remote")
    git_repository.run_git(peer, "push")

    with pytest.raises(subprocess.CalledProcessError):
        _wi_sync.synchronize(local, lock_timeout=5)

    stderr = capsys.readouterr().err
    _, _, next_action = stderr.partition("次の操作: ")
    assert "競合を解消した後" in next_action
    commands = [shlex.split(text) for text in re.findall(r"`(git -C [^`]+)`", next_action)]
    assert [argv[-1] for argv in commands[:3]] == ["<競合解消済みパス>", "--continue", "push"]
    assert _atk_git_sync.is_rebase_in_progress(local)
    status = git_repository.run_git(local, "status", "--porcelain=v1").stdout
    with pytest.raises(_atk_git_sync.RebaseInProgressError):
        _atk_wi_mutations.edit_entry_content(
            local,
            state="inbox",
            filename=other.name,
            content="変更後の本文\n",
            expected_content=original,
            storage=_wi_sync.Storage(local_only=True),
        )

    assert other.read_text(encoding="utf-8") == original
    assert git_repository.run_git(local, "status", "--porcelain=v1").stdout == status
    assert _atk_git_sync.is_rebase_in_progress(local)
    assert git_repository.run_git(local, "rev-parse", "ORIG_HEAD").stdout.strip() == local_head
