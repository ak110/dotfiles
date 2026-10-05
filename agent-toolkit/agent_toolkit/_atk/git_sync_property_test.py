"""Git同期の分岐判定と回復操作を独立モデルと比較する。"""

# pylint: disable=protected-access

from __future__ import annotations

import pathlib
import subprocess

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_toolkit._atk import git_sync


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
    expected = [("reset", "--keep", "@{u}")] if redundant and not dirty else []
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
        expected = ("reset", "--keep", "@{u}") if redundant else ("rebase", "@{u}")
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
        expected = ("reset", "--keep", "@{u}") if redundant else ("rebase", "@{u}")
        assert expected in fake.mutations
