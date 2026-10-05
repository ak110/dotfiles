"""worktree固有refと共有stashの操作列を独立モデルと比較する。"""

# pylint: disable=protected-access

from __future__ import annotations

import pathlib
import subprocess
import tempfile

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from agent_toolkit._atk import worktree_stash

_LABELS = ("alpha", "beta", "gamma")
_ORIGINAL_RUN_GIT = worktree_stash._run_git


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def _repository(parent: pathlib.Path) -> pathlib.Path:
    repo = pathlib.Path(tempfile.mkdtemp(prefix="stash-property-", dir=parent))
    _git(repo, "init", "--initial-branch=main")
    _git(repo, "config", "user.email", "property@example.com")
    _git(repo, "config", "user.name", "Property Test")
    (repo / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "base")
    (repo / "shared.txt").write_text("existing\n", encoding="utf-8")
    _git(repo, "stash", "push", "--include-untracked", "-m", "pre-existing")
    return repo


def _ref_labels(repo: pathlib.Path) -> set[str]:
    output = _git(repo, "for-each-ref", "--format=%(refname)", "refs/worktree/")
    return set(output.splitlines()) if output else set()


@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    operations=st.lists(
        st.one_of(
            st.just(("modify", "")),
            st.tuples(st.just("save"), st.sampled_from(_LABELS)),
            st.tuples(st.just("drop-ref"), st.sampled_from(_LABELS)),
            st.just(("drop-stash", "")),
        ),
        min_size=1,
        max_size=12,
    )
)
def test_stash_sequences_match_reference_model(operations: list[tuple[str, str]], tmp_path: pathlib.Path) -> None:
    """共有stashと固有refの保持・削除を固定lock内の参照モデルと一致させる。"""
    repo = _repository(tmp_path)
    dirty = False
    refs: set[str] = set()
    shared_stashes = 1
    revision = 0
    for operation, label in operations:
        if operation == "modify":
            revision += 1
            (repo / "tracked.txt").write_text(f"change {revision}\n", encoding="utf-8")
            dirty = True
        elif operation == "save":
            code = worktree_stash.save(label, cwd=repo)
            ref = f"refs/worktree/{label}"
            if dirty and ref not in refs:
                assert code == 0
                refs.add(ref)
                dirty = False
            else:
                assert code == 2
        elif operation == "drop-ref":
            ref = f"refs/worktree/{label}"
            code = worktree_stash.drop(ref, cwd=repo)
            assert code == (0 if ref in refs else 2)
            refs.discard(ref)
        else:
            code = worktree_stash.drop("stash@{0}", cwd=repo)
            assert code == (0 if shared_stashes else 2)
            shared_stashes = max(0, shared_stashes - 1)
        assert _ref_labels(repo) == refs
        stash_lines = _git(repo, "stash", "list").splitlines()
        assert len(stash_lines) == shared_stashes
        assert bool(_git(repo, "status", "--porcelain")) is dirty


def test_two_worktrees_keep_distinct_refs_and_existing_stash(tmp_path: pathlib.Path) -> None:
    """既知事例: 複数worktreeの退避は固有refに分かれ、既存の共有stashを保つ。"""
    repo = _repository(tmp_path)
    second = repo.parent / f"{repo.name}-second"
    _git(repo, "worktree", "add", "-b", "second", str(second), "HEAD")
    (repo / "tracked.txt").write_text("main\n", encoding="utf-8")
    (second / "tracked.txt").write_text("second\n", encoding="utf-8")
    assert worktree_stash.save("main-worktree", cwd=repo) == 0
    assert worktree_stash.save("second-worktree", cwd=second) == 0
    main_oid = _git(repo, "rev-parse", "refs/worktree/main-worktree")
    second_oid = _git(second, "rev-parse", "refs/worktree/second-worktree")
    assert main_oid != second_oid
    assert len(_git(repo, "stash", "list").splitlines()) == 1


@settings(max_examples=12, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(failure_stage=st.sampled_from(("stash-push", "update-ref", "stash-drop")))
def test_save_failure_preserves_recovery_identifier_and_unrelated_stash(
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
) -> None:
    """途中失敗では既存stashを保ち、残ったOIDと固有refを復旧識別子として報告する。"""
    repo = _repository(tmp_path)
    (repo / "tracked.txt").write_text("pending\n", encoding="utf-8")

    def fail_selected(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
        selected = (
            (failure_stage == "stash-push" and args[:2] == ["stash", "push"])
            or (failure_stage == "update-ref" and args[:2] == ["update-ref", "refs/worktree/failure"])
            or (failure_stage == "stash-drop" and args[:3] == ["stash", "drop", "stash@{0}"])
        )
        if selected:
            return subprocess.CompletedProcess(["git", *args], 1, "", f"forced {failure_stage}")
        return _ORIGINAL_RUN_GIT(args, cwd)

    with monkeypatch.context() as patcher:
        patcher.setattr(worktree_stash, "_run_git", fail_selected)
        assert worktree_stash.save("failure", cwd=repo) == 1
    reported = capsys.readouterr().err
    assert "stash_oid=" in reported
    assert "ref=refs/worktree/failure" in reported
    stash_lines = _git(repo, "stash", "list").splitlines()
    assert any("pre-existing" in line for line in stash_lines)
    ref_exists = bool(_git(repo, "for-each-ref", "--format=%(refname)", "refs/worktree/failure"))
    assert ref_exists is (failure_stage == "stash-drop")
    assert len(stash_lines) == (1 if failure_stage == "stash-push" else 2)
