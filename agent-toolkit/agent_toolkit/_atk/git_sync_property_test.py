"""Git同期の分岐判定と回復操作を独立モデルと比較する。"""

# pylint: disable=protected-access

from __future__ import annotations

import ast
import pathlib
import subprocess
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_toolkit._atk import git_sync
from agent_toolkit._testing import git_repository


def _direct_git_init_lines(source: str) -> list[int]:
    """共通補助を経由しない、リテラルargvによるGit初期化の行を返す。"""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call) or not node.args or not isinstance(node.args[0], (ast.List, ast.Tuple)):
            continue
        argv = node.args[0].elts
        if (
            len(argv) >= 2
            and isinstance(argv[0], ast.Constant)
            and argv[0].value == "git"
            and any(isinstance(word, ast.Constant) and word.value == "init" for word in argv[1:])
        ):
            lines.append(node.lineno)
    return lines


def test_git_initialization_is_owned_by_shared_test_helper() -> None:
    """テスト内の直接初期化の混入を、呼び出す関数名によらず検出する。"""
    assert _direct_git_init_lines('subprocess.run(["git", "init", "repo"])') == [1]
    assert _direct_git_init_lines('runner(["git", "-C", "repo", "init", "--bare"])') == [1]
    assert not _direct_git_init_lines("git_repository.init_repository(path)")
    root = pathlib.Path(__file__).resolve().parents[2]
    violations = [
        f"{path.relative_to(root)}:{line}"
        for path in root.rglob("*_test.py")
        for line in _direct_git_init_lines(path.read_text(encoding="utf-8"))
    ]
    assert not violations, "初期化は_testing/git_repository.pyへ集約する: " + ", ".join(violations)


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(choices=st.lists(st.integers(min_value=0, max_value=100), min_size=8, max_size=18))
def test_generated_operations_preserve_history_and_uncommitted_content(tmp_path: pathlib.Path, choices: list[int]) -> None:
    """有効な操作列を独立した変更集合モデルと実Gitへ順に適用し、各遷移の内容と履歴を比較する。"""
    with tempfile.TemporaryDirectory(dir=tmp_path) as directory:
        root = pathlib.Path(directory)
        remote = git_repository.init_bare_repository(root / "origin.git")
        local = git_repository.init_repository(root / "local", initial_branch="main", origin=str(remote))
        (local / "base.txt").write_text("base\n", encoding="utf-8")
        git_repository.commit_all(local, "base")
        git_repository.run_git(local, "push", "-u", "origin", "main")
        actor = root / "actor"
        git_repository.run_git(root, "clone", "--branch", "main", str(remote), str(actor))
        git_repository.run_git(actor, "config", "user.name", "Test")
        git_repository.run_git(actor, "config", "user.email", "test@example.invalid")
        local_changes: set[str] = {"base.txt"}
        remote_changes = set(local_changes)
        dirty = False
        # 固定の操作列で分岐・保留・再試行を通し、以後の順序は生成値から選ぶ。
        prefix = ["local", "remote", "edit", "push", "pull", "clean", "pull", "push"]
        operations: list[str | int] = [*prefix, *choices]
        with git_sync.repo_lock(local):
            for step, choice in enumerate(operations):
                valid = ["local", "remote", "pull", "push", "clean" if dirty else "edit"]
                operation = choice if isinstance(choice, str) else valid[choice % len(valid)]
                if operation in {"local", "remote"}:
                    target = local if operation == "local" else actor
                    if operation == "remote":
                        git_repository.run_git(actor, "fetch", "origin")
                        git_repository.run_git(actor, "reset", "--hard", "origin/main")
                    name = f"{operation}-{step}.txt"
                    (target / name).write_text(name, encoding="utf-8")
                    git_repository.run_git(target, "add", "--", name)
                    git_repository.run_git(target, "commit", "-m", name)
                    if operation == "local":
                        local_changes.add(name)
                    else:
                        git_repository.run_git(actor, "push", "origin", "main")
                        remote_changes.add(name)
                elif operation in {"edit", "clean"}:
                    dirty = operation == "edit"
                    (local / "base.txt").write_text("uncommitted\n" if dirty else "base\n", encoding="utf-8")
                else:
                    diverged = bool(local_changes - remote_changes) and bool(remote_changes - local_changes)
                    blocked = dirty and diverged
                    if operation == "pull":
                        if blocked:
                            with pytest.raises(subprocess.CalledProcessError):
                                git_sync.pull(local)
                        else:
                            git_sync.pull(local)
                            local_changes |= remote_changes
                    else:
                        git_sync.push_pending_commits(local)
                        if not blocked:
                            local_changes |= remote_changes
                            remote_changes = set(local_changes)
                        assert git_sync.push_was_deferred(local) is blocked
                observed = set(git_repository.git_output(local, "ls-tree", "--name-only", "HEAD").splitlines())
                published = set(git_repository.git_output(remote, "ls-tree", "--name-only", "main").splitlines())
                assert observed == local_changes
                assert published == remote_changes
                assert (local / "base.txt").read_text(encoding="utf-8") == ("uncommitted\n" if dirty else "base\n")
                subjects = set(git_repository.git_output(local, "log", "--format=%s").splitlines())
                assert local_changes - {"base.txt"} <= subjects
            if dirty:
                (local / "base.txt").write_text("base\n", encoding="utf-8")
            git_sync.pull(local)
            git_sync.push_pending_commits(local)
            # 実際の競合を起こしてrebase中間状態を作り、生成した同期操作が状態を変えないことも確かめる。
            git_repository.run_git(actor, "fetch", "origin")
            git_repository.run_git(actor, "reset", "--hard", "origin/main")
            for target, value in ((actor, "remote conflict\n"), (local, "local conflict\n")):
                (target / "base.txt").write_text(value, encoding="utf-8")
                git_repository.commit_all(target, value.strip())
            git_repository.run_git(actor, "push", "origin", "main")
            with pytest.raises(subprocess.CalledProcessError):
                git_sync.pull(local)
            assert git_sync.is_rebase_in_progress(local)
            before = (git_repository.git_output(local, "rev-parse", "HEAD"), (local / "base.txt").read_bytes())
            for choice in choices:
                with pytest.raises(git_sync.RebaseInProgressError):
                    (git_sync.pull if choice % 2 else git_sync.push_pending_commits)(local)
                assert before == (git_repository.git_output(local, "rev-parse", "HEAD"), (local / "base.txt").read_bytes())
            git_repository.run_git(local, "rebase", "--abort")
            assert not git_sync.is_rebase_in_progress(local)


class _GitDouble:
    def __init__(self, *, local_ancestor: bool, remote_ancestor: bool, tree_matches: bool, dirty: bool) -> None:
        self.local_ancestor = local_ancestor
        self.remote_ancestor = remote_ancestor
        self.tree_matches = tree_matches
        self.dirty = dirty
        self.mutations: list[tuple[str, ...]] = []

    def run(self, args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        del cwd, forward_error_output
        command = tuple(args)
        if command[:2] == ("merge-base", "--is-ancestor"):
            succeeds = self.local_ancestor if command[2:] == ("HEAD", "@{u}") else self.remote_ancestor
            if not succeeds:
                raise subprocess.CalledProcessError(1, ["git", *args])
            return
        self.mutations.append(command)

    def result(self, args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        command = tuple(args)
        if command[:3] == ("diff", "--quiet", "HEAD"):
            return subprocess.CompletedProcess(["git", *args], 0 if self.tree_matches else 1, "", "")
        if command[:2] == ("status", "--porcelain"):
            return subprocess.CompletedProcess(["git", *args], 0, " M item.md\n" if self.dirty else "", "")
        raise AssertionError(command)


@settings(max_examples=80, deadline=None)
@given(
    local_ancestor=st.booleans(),
    remote_ancestor=st.booleans(),
    tree_matches=st.booleans(),
    dirty=st.booleans(),
    redundant=st.booleans(),
)
def test_divergence_recovery_matches_reference_model(
    local_ancestor: bool,
    remote_ancestor: bool,
    tree_matches: bool,
    dirty: bool,
    redundant: bool,
) -> None:
    """祖先関係、tree一致、dirtyと冗長分岐から安全な回復操作だけを選ぶ。"""
    fake = _GitDouble(
        local_ancestor=local_ancestor,
        remote_ancestor=remote_ancestor,
        tree_matches=tree_matches,
        dirty=dirty,
    )
    path = pathlib.Path("/model/repository")
    diverged = git_sync._history_has_diverged(path, run_git=fake.run)
    assert diverged is (not local_ancestor and not remote_ancestor)

    matching = git_sync._recover_matching_tree_divergence(path, run_git=fake.run, result_runner=fake.result)
    assert matching is tree_matches
    if matching:
        assert fake.mutations == [("reset", "--soft", "@{u}")]
        return

    recovered = git_sync._recover_redundant_divergence(
        path,
        redundant_divergence=lambda _path: redundant,
        run_git=fake.run,
        result_runner=fake.result,
    )
    assert recovered is (redundant and not dirty)
    expected = [("-c", "submodule.recurse=false", "reset", "--keep", "@{u}")] if redundant and not dirty else []
    assert fake.mutations == expected


def test_matching_tree_regression_preserves_index_with_soft_reset() -> None:
    """既知事例: 履歴が分岐してもtreeが一致するなら未commit差分を保つ。"""
    fake = _GitDouble(local_ancestor=False, remote_ancestor=False, tree_matches=True, dirty=True)
    assert git_sync._recover_matching_tree_divergence(pathlib.Path("/repo"), run_git=fake.run, result_runner=fake.result)
    assert fake.mutations == [("reset", "--soft", "@{u}")]


def test_rebase_in_progress_is_reported_before_mutation(tmp_path: pathlib.Path) -> None:
    """既知事例: rebase中間状態は新しい同期操作で正常化しない。"""
    (tmp_path / ".git" / "rebase-merge").mkdir(parents=True)

    def runner(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        del cwd
        name = args[-1]
        return subprocess.CompletedProcess(["git", *args], 0, f".git/{name}\n", "")

    assert git_sync.is_rebase_in_progress(tmp_path, result_runner=runner)


def test_dirty_content_divergence_keeps_history_unchanged() -> None:
    """既知事例: dirtyかつ内容差がある分岐はresetせず保留する。"""
    fake = _GitDouble(local_ancestor=False, remote_ancestor=False, tree_matches=False, dirty=True)
    recovered = git_sync._recover_redundant_divergence(
        pathlib.Path("/repo"),
        redundant_divergence=lambda _path: True,
        run_git=fake.run,
        result_runner=fake.result,
    )
    assert not recovered
    assert not fake.mutations


class _SyncDouble(_GitDouble):
    def __init__(
        self,
        *,
        local_ancestor: bool,
        remote_ancestor: bool,
        tree_matches: bool,
        dirty: bool,
        fast_forward: bool,
        initial_push_succeeds: bool,
    ) -> None:
        super().__init__(
            local_ancestor=local_ancestor,
            remote_ancestor=remote_ancestor,
            tree_matches=tree_matches,
            dirty=dirty,
        )
        self.fast_forward = fast_forward
        self.initial_push_succeeds = initial_push_succeeds
        self.pushes = 0

    def run(self, args: list[str], cwd: pathlib.Path, *, forward_error_output: bool = True) -> None:
        command = tuple(args)
        if command == ("merge", "--ff-only", "@{u}") and not self.fast_forward:
            raise subprocess.CalledProcessError(1, ["git", *args], stderr="not fast-forward")
        if command == ("push",):
            self.pushes += 1
            if self.pushes == 1 and not self.initial_push_succeeds:
                raise subprocess.CalledProcessError(1, ["git", *args], stderr="rejected")
        super().run(args, cwd, forward_error_output=forward_error_output)

    def result(self, args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        if tuple(args) == ("rev-list", "--count", "@{u}..HEAD"):
            return subprocess.CompletedProcess(["git", *args], 0, "2\n", "")
        if tuple(args) == ("rev-list", "--left-right", "--count", "HEAD...@{u}"):
            return subprocess.CompletedProcess(["git", *args], 0, "1\t1\n", "")
        if tuple(args) == ("diff", "--name-status", "HEAD", "@{u}"):
            return subprocess.CompletedProcess(["git", *args], 0, "M\titem.md\n", "")
        return super().result(args, cwd)


@settings(max_examples=50, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    local_ancestor=st.booleans(),
    remote_ancestor=st.booleans(),
    tree_matches=st.booleans(),
    dirty=st.booleans(),
    redundant=st.booleans(),
    fast_forward=st.booleans(),
)
def test_pull_event_sequence_matches_reference_model(
    local_ancestor: bool,
    remote_ancestor: bool,
    tree_matches: bool,
    dirty: bool,
    redundant: bool,
    fast_forward: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch後のfast-forward、保留、回復とrebaseを独立した分岐表に合わせる。"""
    monkeypatch.setattr(git_sync, "assert_repo_lock_held", lambda _path: None)
    monkeypatch.setattr(git_sync, "ensure_not_rebasing", lambda _path: None)
    monkeypatch.setattr(git_sync, "has_remote", lambda _path: True)
    fake = _SyncDouble(
        local_ancestor=local_ancestor,
        remote_ancestor=remote_ancestor,
        tree_matches=tree_matches,
        dirty=dirty,
        fast_forward=fast_forward,
        initial_push_succeeds=True,
    )
    diverged = not local_ancestor and not remote_ancestor
    should_fail = not fast_forward and (not diverged or (not tree_matches and dirty))
    if should_fail:
        with pytest.raises(subprocess.CalledProcessError):
            git_sync._pull_impl(
                pathlib.Path("/repo"),
                run_git=fake.run,
                result_runner=fake.result,
                redundant_divergence=lambda _path: redundant,
            )
    else:
        git_sync._pull_impl(
            pathlib.Path("/repo"),
            run_git=fake.run,
            result_runner=fake.result,
            redundant_divergence=lambda _path: redundant,
        )
    if not fast_forward and diverged and not tree_matches and not dirty:
        expected = (
            ("-c", "submodule.recurse=false", "reset", "--keep", "@{u}")
            if redundant
            else ("-c", "submodule.recurse=false", "rebase", "@{u}")
        )
        assert expected in fake.mutations


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    local_ancestor=st.booleans(),
    remote_ancestor=st.booleans(),
    tree_matches=st.booleans(),
    dirty=st.booleans(),
    redundant=st.booleans(),
)
def test_rejected_push_sequence_matches_reference_model(
    local_ancestor: bool,
    remote_ancestor: bool,
    tree_matches: bool,
    dirty: bool,
    redundant: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """push拒否後は祖先関係と作業ツリー状態に応じた1つの回復操作だけを選ぶ。"""
    monkeypatch.setattr(git_sync, "assert_repo_lock_held", lambda _path: None)
    monkeypatch.setattr(git_sync, "ensure_not_rebasing", lambda _path: None)
    monkeypatch.setattr(git_sync, "has_remote", lambda _path: True)
    fake = _SyncDouble(
        local_ancestor=local_ancestor,
        remote_ancestor=remote_ancestor,
        tree_matches=tree_matches,
        dirty=dirty,
        fast_forward=True,
        initial_push_succeeds=False,
    )
    path = pathlib.Path("/repo")
    diverged = not local_ancestor and not remote_ancestor
    must_raise = remote_ancestor or (local_ancestor and remote_ancestor)
    if must_raise:
        with pytest.raises(subprocess.CalledProcessError):
            git_sync._push_pending_commits_impl(
                path,
                run_git=fake.run,
                result_runner=fake.result,
                redundant_divergence=lambda _path: redundant,
            )
        return
    result = git_sync._push_pending_commits_impl(
        path,
        run_git=fake.run,
        result_runner=fake.result,
        redundant_divergence=lambda _path: redundant,
    )
    assert result in {0, 2}
    if diverged and not tree_matches and not dirty:
        expected = (
            ("-c", "submodule.recurse=false", "reset", "--keep", "@{u}")
            if redundant
            else ("-c", "submodule.recurse=false", "rebase", "@{u}")
        )
        assert expected in fake.mutations
