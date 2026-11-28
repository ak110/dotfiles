# pylint: disable=protected-access

import os
import pathlib
import re
import shlex
import subprocess
import sys
import threading
import time
from typing import Any

import filelock
import pytest

from agent_toolkit import atk
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk import git_sync_lock
from agent_toolkit._atk.wi import background_sync
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._atk.wi.layout import iter_entry_paths
from agent_toolkit._common import process_tree
from agent_toolkit._git import command as _git_command
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.git_sync_support import (  # noqa: F401  # pylint: disable=unused-import
    REAL_GIT_CPU,
    _agent_output_fixture,
    _remote_sync_fixture,
)
from agent_toolkit._testing.wi_bodies import AGENT_AWI_BODY


@pytest.mark.parametrize("agent", [False, True], ids=["human", "agent"])
@pytest.mark.usefixtures("remote_sync")
def test_sync_reports_progress_and_lock_wait(monkeypatch, tmp_path, capsys, agent):
    """ロックとremote処理を解放する前に対象と状態が届く。"""
    monkeypatch.setattr(_atk_git_sync, "is_human_terminal", lambda: not agent)
    monkeypatch.setattr(git_sync_lock, "is_human_terminal", lambda: not agent)
    held = threading.Event()
    release = threading.Event()

    def hold_lock():
        with _atk_git_sync.repo_lock(tmp_path):
            held.set()
            assert release.wait(30)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert held.wait(30)
    capsys.readouterr()
    original_acquire = filelock.FileLock.acquire
    observed: list[Any] = []

    def acquire(lock, *args, **kwargs):

        if kwargs.get("blocking") is not False and not observed:
            observed.append(capsys.readouterr())
            release.set()
        return original_acquire(lock, *args, **kwargs)

    monkeypatch.setattr(filelock.FileLock, "acquire", acquire)

    def remote(args, cwd, **_kwargs):
        assert cwd == tmp_path
        if args[0] not in {"fetch", "push"}:
            return
        captured = capsys.readouterr()
        assert not captured.out
        if agent:
            assert not captured.err
        else:
            assert "同期中" in captured.err and str(tmp_path) in captured.err

    try:
        with _atk_git_sync.repo_lock(tmp_path):
            _atk_git_sync.pull(tmp_path, run_git=remote)
            _atk_git_sync.push_pending_commits(tmp_path, run_git=remote)
    finally:
        release.set()
        holder.join(30)
    assert not holder.is_alive()
    assert observed and not observed[0].out
    if agent:
        assert not observed[0].err
    else:
        assert "ロック" in observed[0].err and str(tmp_path) in observed[0].err


def test_uncontended_lock_does_not_report_waiting(monkeypatch, tmp_path, capsys):
    """人の端末でも、他の主体が保持していないロックの取得では待機中と表示しない。"""
    monkeypatch.setattr(git_sync_lock, "is_human_terminal", lambda: True)
    with _atk_git_sync.repo_lock(tmp_path):
        pass
    assert "ロック" not in capsys.readouterr().err


def test_lock_timeout_records_wait_to_observer(tmp_path: pathlib.Path) -> None:
    """取得できずに時間切れとなった待ちも、観測先へロック待ちとして加算する。"""

    class Observer:
        def __init__(self) -> None:
            self.waits: list[float] = []
            self.holds: list[float] = []

        def add_lock_wait(self, seconds: float) -> None:
            self.waits.append(seconds)

        def add_lock_hold(self, seconds: float) -> None:
            self.holds.append(seconds)

    observer = Observer()
    with _atk_git_sync.repo_lock(tmp_path):
        token = _atk_git_sync.LOCK_OBSERVER.set(observer)
        try:
            with pytest.raises(filelock.Timeout), _atk_git_sync.repo_lock(tmp_path, timeout=0.2):
                pass
        finally:
            _atk_git_sync.LOCK_OBSERVER.reset(token)
    assert len(observer.waits) == 1
    assert observer.waits[0] >= 0.2
    assert not observer.holds


_LOCK_HOLDER_SCRIPT = """
import os
import pathlib
import sys
import time

from agent_toolkit._atk import git_sync

with git_sync.repo_lock(pathlib.Path(sys.argv[1])):
    ready = pathlib.Path(sys.argv[2])
    # 読む側が書きかけの値を読まないよう、別名で書いてから置き換える。
    partial = ready.with_name(ready.name + ".partial")
    partial.write_text(str(os.getpid()), encoding="utf-8")
    os.replace(partial, ready)
    deadline = time.monotonic() + 60
    while not ready.with_suffix(".release").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
"""

# Windowsの仮想環境の`python.exe`と同じく、起動したプロセスとロックを取得するプロセスを別のPIDにする中継役。
_LOCK_HOLDER_LAUNCHER = (
    "import subprocess, sys; sys.exit(subprocess.run([sys.executable, *sys.argv[1:]], check=False).returncode)"
)


@REAL_GIT_CPU
def test_wi_add_stops_at_lock_wait_limit_and_names_holder(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """別プロセスがロックを保持し続けると、`atk wi add`は待機上限で失敗し、保持者のPIDとコマンドラインを示す。

    待機上限の省略時の値が無期限の場合、保持者が終わる60秒後まで戻らず、失敗もしない。
    保持者は中継役を経由して起動する。起動したプロセスのPIDとロックを取得したプロセスのPIDが異なる構成でも、
    失敗行が後者を示すことを確かめる。保持側は正常終了させ、中継役が子を回収してから後始末を終える。
    """
    notes = tmp_path / "private-notes"
    git_repository.init_repository(notes, initial_branch="main", files={"README.md": "notes\n"}, commit_message="base")
    script = tmp_path / "hold_lock.py"
    script.write_text(_LOCK_HOLDER_SCRIPT, encoding="utf-8")
    ready = tmp_path / "ready"
    body = tmp_path / "body.md"
    body.write_text(AGENT_AWI_BODY, encoding="utf-8")
    monkeypatch.setattr(_wi_sync, "ensure_environment", lambda _home: notes)
    monkeypatch.setattr(git_sync_lock, "LOCK_WAIT_TIMEOUT_SECONDS", 1.0)
    environ = os.environ.copy()
    package_root = pathlib.Path(_atk_git_sync.__file__).resolve().parents[2]
    environ["PYTHONPATH"] = os.pathsep.join(filter(None, [str(package_root), environ.get("PYTHONPATH")]))
    command = [sys.executable, "-c", _LOCK_HOLDER_LAUNCHER, str(script), str(notes), str(ready)]
    with subprocess.Popen(command, env=environ) as holder:
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert holder.poll() is None, "保持者がロックの取得前に終了した"
                assert time.monotonic() < deadline, "保持者のロック取得を観測できない"
                time.sleep(0.05)
            holder_pid = int(ready.read_text(encoding="utf-8"))

            started = time.monotonic()
            with pytest.raises(SystemExit) as exc_info:
                atk.main(
                    [
                        "wi",
                        "add",
                        "--source",
                        "session-review",
                        "--target-repo",
                        "github.com/example/repo",
                        "--body-file",
                        str(body),
                    ],
                    home=tmp_path,
                )
            elapsed = time.monotonic() - started
        finally:
            ready.with_suffix(".release").touch()
            try:
                holder.wait(timeout=5)
            finally:
                residual = process_tree.stop_process_tree(holder) if holder.poll() is None else []

    assert not residual
    assert holder_pid != holder.pid
    with _atk_git_sync.repo_lock(notes, timeout=0):
        pass
    assert exc_info.value.code == 1
    assert elapsed < 30
    error = capsys.readouterr().err
    failure = next(line for line in error.splitlines() if line.startswith("失敗: "))
    assert f"PID {holder_pid}、" in failure
    assert "hold_lock.py" in failure
    assert str(_atk_git_sync.repo_lock_path(notes)) in failure
    assert any(line.startswith("次の操作: ") for line in error.splitlines())


def test_lock_records_holder_while_held_and_bounds_git_operations(tmp_path: pathlib.Path) -> None:
    holder_file = pathlib.Path(str(_atk_git_sync.repo_lock_path(tmp_path)) + ".holder")
    limits: list[float] = []

    def record(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        limits.append(kwargs["timeout"])
        return subprocess.CompletedProcess(command, 0, b"", b"")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(_git_command, "_launch", record)
        # 再入は同じロックオブジェクトで行う。別のオブジェクトは同じプロセスでも互いに待つ。
        lock = _atk_git_sync.repo_lock(tmp_path)
        with lock:
            assert f'"pid": {os.getpid()}' in holder_file.read_text(encoding="utf-8")
            with lock:
                _git_command.run(["fetch"], tmp_path)
            assert holder_file.exists()
        _git_command.run(["fetch"], tmp_path)

    assert not holder_file.exists()
    assert 0 < limits[0] <= _atk_git_sync.LOCK_HOLD_TIMEOUT_SECONDS
    assert limits[1] == _git_command.TRANSFER_TIMEOUT_SECONDS


def test_pull_reports_git_timeout_as_single_sync_failure(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], remote_sync: None
) -> None:
    """pullのgitが時間切れになると、打ち切ったコマンド・上限秒数・作業ディレクトリを持つ失敗行を1本だけ出す。"""
    del remote_sync

    def time_out(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del forward_error_output
        error = subprocess.TimeoutExpired(_git_command.command_line(args), 120)

        setattr(error, "git_cwd", str(cwd))  # noqa: B010
        raise error

    with _atk_git_sync.repo_lock(tmp_path), pytest.raises(subprocess.TimeoutExpired) as raised:
        _atk_git_sync.pull(tmp_path, run_git=time_out)

    assert _atk_git_sync.is_reported(raised.value)
    failures = [line for line in capsys.readouterr().err.splitlines() if line.startswith("失敗: ")]
    assert len(failures) == 1
    assert "private-notesのpullに失敗した" in failures[0]
    assert "`git fetch`" in failures[0]
    assert "120秒" in failures[0]
    assert str(tmp_path) in failures[0]


def _init_repo(root: pathlib.Path) -> None:
    root.mkdir()
    git_repository.init_repository(root, initial_branch="main")
    git_repository.run_git(root, "config", "user.name", "sync-test")
    git_repository.run_git(root, "config", "user.email", "sync-test@example.invalid")
    (root / "target.txt").write_text("before\n", encoding="utf-8")
    (root / "unrelated.txt").write_text("before\n", encoding="utf-8")
    git_repository.run_git(root, "add", ".")
    git_repository.run_git(root, "commit", "-m", "base")


@REAL_GIT_CPU
def test_run_git_suppresses_success_output(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """同期基盤のGit実行は成功時に標準出力と標準エラーへ書かない。"""
    _atk_git_sync._run_git(["init", "--initial-branch=main"], tmp_path)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@REAL_GIT_CPU
def test_commit_and_push_keeps_unrelated_staged_change_out_of_commit(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    origin = tmp_path / "origin.git"
    origin.mkdir()
    git_repository.init_bare_repository(origin)
    git_repository.run_git(repo, "remote", "add", "origin", str(origin))
    git_repository.run_git(repo, "push", "-u", "origin", "main")
    (repo / "target.txt").write_text("after\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("unrelated\n", encoding="utf-8")
    git_repository.run_git(repo, "add", "unrelated.txt")

    with _atk_git_sync.repo_lock(repo):
        _atk_git_sync.commit_and_push(repo, "target only", ["target.txt"], skip_push=True)

    committed = git_repository.run_git(repo, "show", "--format=", "--name-only", "HEAD").stdout.splitlines()
    staged = git_repository.run_git(repo, "diff", "--cached", "--name-only").stdout.splitlines()
    assert committed == ["target.txt"]
    assert staged == ["unrelated.txt"]
    assert _atk_git_sync.pending_commit_count(repo) == 1
    captured = capsys.readouterr()
    assert not captured.out
    assert not captured.err


@REAL_GIT_CPU
def test_worktree_dirty_can_limit_status_to_target_paths(tmp_path: pathlib.Path) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "unrelated.txt").write_text("changed\n", encoding="utf-8")

    assert not _atk_git_sync.is_worktree_dirty(repo, paths=["target.txt"])
    assert _atk_git_sync.is_worktree_dirty(repo, paths=["unrelated.txt"])
    assert _atk_git_sync.is_worktree_dirty(repo)


@REAL_GIT_CPU
def test_pending_commit_count_distinguishes_remote_and_upstream_states(tmp_path: pathlib.Path) -> None:
    local_only = tmp_path / "local-only"
    _init_repo(local_only)
    assert _atk_git_sync.pending_commit_count(local_only) == 0

    unresolved = tmp_path / "unresolved"
    _init_repo(unresolved)
    assert _atk_git_sync.pending_commit_count(unresolved) == 0

    origin = tmp_path / "origin.git"
    origin.mkdir()
    git_repository.init_bare_repository(origin)
    repo = tmp_path / "repo"
    _init_repo(repo)
    git_repository.run_git(repo, "remote", "add", "origin", str(origin))
    git_repository.run_git(repo, "push", "-u", "origin", "main")
    (repo / "target.txt").write_text("after\n", encoding="utf-8")
    git_repository.run_git(repo, "add", "target.txt")
    git_repository.run_git(repo, "commit", "-m", "local")

    assert _atk_git_sync.pending_commit_count(repo) == 1


@pytest.mark.parametrize("operation", ["pull", "push"])
@pytest.mark.usefixtures("remote_sync")
def test_sync_failure_reports_recovery_steps_and_preserves_exception(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    """pullとpushの失敗は対応手順を示して元の例外を送出する。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    expected = subprocess.CalledProcessError(1, ["git", operation])

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del forward_error_output
        del cwd
        if operation == "pull" or args == ["push"]:
            raise expected
        raise subprocess.CalledProcessError(1, ["git", *args])

    sync = _atk_git_sync.pull if operation == "pull" else _atk_git_sync.push_pending_commits
    with _atk_git_sync.repo_lock(repo), pytest.raises(subprocess.CalledProcessError) as exc_info:
        sync(repo, run_git=run_git)

    assert exc_info.value is expected
    stderr = capsys.readouterr().err
    assert f"private-notesの{operation}に失敗した: {repo.resolve()}" in stderr
    assert f"確認: `git -C {repo.resolve()} status`" in stderr
    assert "失敗した`atk`操作を再実行" in stderr


@pytest.mark.parametrize(
    ("git_stderr", "expected_cause", "expected_operation"),
    [
        ("fatal: Authentication failed for 'https://example.invalid/repo.git/'\n", "認証", "remote -v"),
        ("ssh: Could not resolve hostname example.invalid\n", "接続", "ネットワーク接続"),
        (" ! [rejected]        main -> main (fetch first)\n", "upstream", "atk wi commit"),
    ],
)
@pytest.mark.usefixtures("remote_sync")
def test_sync_failure_classifies_git_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    git_stderr: str,
    expected_cause: str,
    expected_operation: str,
) -> None:
    """同期失敗はgitの出力から原因を分類し、原因別の次の操作を示す。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    expected = subprocess.CalledProcessError(1, ["git", "fetch"], output="", stderr=git_stderr)

    def run_git(args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del args, cwd, forward_error_output
        raise expected

    with _atk_git_sync.repo_lock(repo), pytest.raises(subprocess.CalledProcessError) as exc_info:
        _atk_git_sync.pull(repo, run_git=run_git)

    assert _atk_git_sync.is_reported(exc_info.value)
    lines = capsys.readouterr().err.splitlines()
    failure = next(line for line in lines if line.startswith("失敗: "))
    next_action = lines[lines.index(failure) + 1]
    assert expected_cause in failure
    assert next_action.startswith("次の操作: ")
    assert expected_operation in next_action


def test_require_upstream_names_set_upstream_command(tmp_path: pathlib.Path) -> None:
    """upstreamを解決できない場合は、上流を設定するコマンドを次の操作として持つ。"""

    def result_runner(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        return subprocess.CompletedProcess(["git", *args], 128, "", "fatal: no upstream configured")

    with pytest.raises(_atk_git_sync.GitSyncError) as exc_info:
        _atk_git_sync.require_upstream(tmp_path, result_runner=result_runner)

    assert "branch --set-upstream-to=<remote>/<branch>" in exc_info.value.next_action


@REAL_GIT_CPU
def test_rebase_in_progress_error_keeps_reason_and_next_action(tmp_path: pathlib.Path) -> None:
    """rebase中の拒否は理由だけを`str()`で返し、競合解消の手順を次の操作として持つ。"""
    _init_repo(tmp_path / "repo")
    repo = tmp_path / "repo"
    rebase_dir = pathlib.Path(git_repository.run_git(repo, "rev-parse", "--git-path", "rebase-merge").stdout.strip())
    (rebase_dir if rebase_dir.is_absolute() else repo / rebase_dir).mkdir(parents=True)

    with pytest.raises(_atk_git_sync.RebaseInProgressError) as exc_info:
        _atk_git_sync.ensure_not_rebasing(repo)

    assert "rebase --continue" not in str(exc_info.value)
    commands = [shlex.split(text) for text in re.findall(r"`(git -C [^`]+)`", exc_info.value.next_action)]
    assert commands and all(argv[:3] == ["git", "-C", str(repo)] for argv in commands)
    assert [argv[-1] for argv in commands] == ["<競合解消済みパス>", "--continue", "push", "--abort"]


@pytest.mark.parametrize("configuration", ["local", "remote", "upstream"])
@REAL_GIT_CPU
def test_notes_cli_with_upstream_states(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    configuration: str,
) -> None:
    """実Gitの3構成で、公開CLIの保存、同期結果、注記、remoteへの到達を確かめる。"""
    notes = tmp_path / "notes"
    _init_repo(notes)
    remote = tmp_path / "remote.git"
    if configuration != "local":
        remote.mkdir()
        git_repository.init_bare_repository(remote)
        git_repository.run_git(notes, "remote", "add", "origin", str(remote))
        if configuration == "upstream":
            git_repository.run_git(notes, "push", "-u", "origin", "main")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    monkeypatch.setattr(background_sync, "schedule", lambda _notes: None)
    source = tmp_path / "body.md"
    source.write_text(AGENT_AWI_BODY, encoding="utf-8")

    def invoke(*args: str) -> str:
        with pytest.raises(SystemExit) as result:
            atk.main(list(args), home=tmp_path, _output_capture_active=True)
        assert result.value.code == 0
        captured = capsys.readouterr()
        if args[1] in {"add", "edit"}:
            assert "upstreamが無いため" not in captured.err
        return captured.out

    previous = git_repository.run_git(notes, "rev-parse", "HEAD").stdout
    invoke("wi", "add", "--source", "agent", "--body-file", str(source), "--target-repo", "github.com/example/repo")
    added = git_repository.run_git(notes, "rev-parse", "HEAD").stdout
    assert added != previous
    (entry,) = list(iter_entry_paths(notes))
    source.write_text(AGENT_AWI_BODY.replace("本文", "変更後の本文", 1), encoding="utf-8")
    invoke("wi", "edit", entry.name, "--body-file", str(source), "--target-repo", "github.com/example/repo")
    edited = git_repository.run_git(notes, "rev-parse", "HEAD").stdout
    assert edited != added
    invoke("wi", "list", "--summary-only")
    committed = invoke("wi", "commit")
    if configuration != "upstream":
        assert "remoteかupstreamが無いため" in committed
    if configuration == "upstream":
        assert git_repository.run_git(remote, "rev-parse", "main").stdout == edited
    elif configuration == "remote":
        absent = subprocess.run(["git", "-C", str(remote), "rev-parse", "--verify", "main"], capture_output=True, check=False)
        assert absent.returncode != 0
