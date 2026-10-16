"""過去の退避を一括整理し、生成差分だけを回収する契約を実Gitで確認する。"""

import pathlib
import unittest.mock
from typing import Any

import pytest
import update_dotfiles_stash
from agent_toolkit._testing import git_repository


def _repository(path: pathlib.Path) -> pathlib.Path:
    return git_repository.init_repository(
        path, initial_branch="main", files={"mise.lock": "base\n", "kept.txt": "base\n"}, commit_message="基準"
    )


def _save_old(repository: pathlib.Path, label: str) -> tuple[str, str]:
    git_repository.run_git(repository, "stash", "push", "--include-untracked")
    oid = git_repository.git_output(repository, "rev-parse", "refs/stash")
    ref = f"refs/worktree/update-dotfiles-{label}"
    git_repository.run_git(repository, "update-ref", ref, oid)
    git_repository.run_git(repository, "stash", "drop")
    return ref, oid


@pytest.mark.parametrize("protected", [False, True])
@pytest.mark.parametrize("change_kind", ["unstaged", "index-only", "untracked"])
def test_generated_only_old_stash_is_collected(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], protected: bool, change_kind: str
) -> None:
    """保護の有無によらずmise.lockだけの退避を回収し、現在の作業内容を保持する。"""
    repository = _repository(tmp_path / "repo")
    if change_kind == "untracked":
        git_repository.run_git(repository, "rm", "--cached", "mise.lock")
        git_repository.run_git(repository, "commit", "-m", "lock未管理の基準")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    if change_kind == "index-only":
        git_repository.run_git(repository, "add", "mise.lock")
        (repository / "mise.lock").write_text("base\n", encoding="utf-8")
    ref, oid = _save_old(repository, "generated")
    shared_ref = ref.replace("refs/worktree/", "refs/atk/worktree-stash/main/")
    if protected:
        git_repository.run_git(repository, "update-ref", shared_ref, oid)
    (repository / "kept.txt").write_text("current unsaved data\n", encoding="utf-8")

    assert update_dotfiles_stash.protect_old(cwd=repository) == 0

    assert not git_repository.git_output(repository, "for-each-ref", "refs/worktree/", "refs/atk/worktree-stash/")
    assert (repository / "kept.txt").read_text(encoding="utf-8") == "current unsaved data\n"
    assert "mise.lockだけの過去の退避1件を削除" in capsys.readouterr().out
    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert not capsys.readouterr().out


@pytest.mark.parametrize("kind", ["tracked", "index-only", "untracked", "nested-lock"])
def test_user_content_is_protected_and_not_reported_again(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    """indexだけ・未追跡・別ディレクトリの変更も保全し、保護済みは繰り返し表示しない。"""
    repository = _repository(tmp_path / "repo")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    if kind in {"tracked", "index-only"}:
        (repository / "kept.txt").write_text("saved user data\n", encoding="utf-8")
        if kind == "index-only":
            git_repository.run_git(repository, "add", "kept.txt")
            (repository / "kept.txt").write_text("base\n", encoding="utf-8")
    else:
        path = repository / ("untracked.txt" if kind == "untracked" else "nested/mise.lock")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("saved user data\n", encoding="utf-8")
    ref, oid = _save_old(repository, kind)

    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert git_repository.git_output(repository, "rev-parse", ref) == oid
    shared_ref = ref.replace("refs/worktree/", "refs/atk/worktree-stash/main/")
    assert git_repository.git_output(repository, "rev-parse", shared_ref) == oid
    capsys.readouterr()
    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert not capsys.readouterr().out
    git_repository.run_git(repository, "stash", "apply", "--index", ref)
    if kind == "index-only":
        assert git_repository.git_output(repository, "show", ":kept.txt") == "saved user data"
        assert (repository / "kept.txt").read_text(encoding="utf-8") == "base\n"
    elif kind == "tracked":
        assert (repository / "kept.txt").read_text(encoding="utf-8") == "saved user data\n"
    else:
        path = repository / ("untracked.txt" if kind == "untracked" else "nested/mise.lock")
        assert path.read_text(encoding="utf-8") == "saved user data\n"


def test_generated_stash_preserves_otherwise_unreachable_local_commit(tmp_path: pathlib.Path) -> None:
    """生成差分だけでも、退避元のローカルcommitが唯一の参照ならその履歴を保持する。"""
    repository = _repository(tmp_path / "repo")
    git_repository.run_git(repository, "switch", "-c", "local-only")
    (repository / "committed.txt").write_text("local committed data\n", encoding="utf-8")
    commit = git_repository.commit_all(repository, "ローカルcommit")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, _oid = _save_old(repository, "local-history")
    git_repository.run_git(repository, "switch", "main")
    git_repository.run_git(repository, "branch", "-D", "local-only")

    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert git_repository.git_output(repository, "rev-parse", f"{ref}^1") == commit
    assert git_repository.git_output(repository, "show", f"{commit}:committed.txt") == "local committed data"


def test_old_stash_processing_has_constant_git_process_count(tmp_path: pathlib.Path) -> None:
    """退避が増えても外部Gitの起動数を増やさず、全件を正しく保護する。"""
    run = update_dotfiles_stash.git_command.run
    counts: list[int] = []
    for number in (1, 40):
        repository = _repository(tmp_path / f"repo-{number}")
        (repository / "kept.txt").write_text("saved user data\n", encoding="utf-8")
        first_ref, oid = _save_old(repository, "0")
        (repository / "kept.txt").write_text("saved user data\n", encoding="utf-8")
        for index in range(1, number):
            distinct_oid = git_repository.git_output(repository, "stash", "create", f"退避-{index}")
            git_repository.run_git(repository, "update-ref", f"refs/worktree/update-dotfiles-{index}", distinct_oid)
        with unittest.mock.patch.object(update_dotfiles_stash.git_command, "run", wraps=run) as recorded_run:
            assert update_dotfiles_stash.protect_old(cwd=repository) == 0
        counts.append(recorded_run.call_count)
        shared = git_repository.git_output(repository, "for-each-ref", "--format=%(refname)", "refs/atk/worktree-stash/")
        assert len(shared.splitlines()) == number
        assert git_repository.git_output(repository, "rev-parse", first_ref) == oid
    assert counts[0] == counts[1]


def test_inconsistent_protection_preserves_both_values(tmp_path: pathlib.Path) -> None:
    """共有保護と固有退避が不一致なら、自動生成差分でも両方を保持して止まる。"""
    repository = _repository(tmp_path / "repo")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, oid = _save_old(repository, "inconsistent")
    head = git_repository.git_output(repository, "rev-parse", "HEAD")
    shared_ref = ref.replace("refs/worktree/", "refs/atk/worktree-stash/main/")
    git_repository.run_git(repository, "update-ref", shared_ref, head)

    assert update_dotfiles_stash.protect_old(cwd=repository) == 1
    assert git_repository.git_output(repository, "rev-parse", ref) == oid
    assert git_repository.git_output(repository, "rev-parse", shared_ref) == head


def test_queue_repository_is_not_changed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """private-notesでは生成差分を含む旧退避も変更しない。"""
    repository = _repository(tmp_path / "repo")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, oid = _save_old(repository, "queue")
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(repository))

    assert update_dotfiles_stash.protect_old(cwd=repository) == 2
    assert git_repository.git_output(repository, "rev-parse", ref) == oid


def test_changed_ref_aborts_entire_cleanup_transaction(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ロックを共有しない手動更新が途中に入っても、旧値を条件にして全回収を取り消す。"""
    repository = _repository(tmp_path / "repo")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    first, old_oid = _save_old(repository, "first")
    second = "refs/worktree/update-dotfiles-second"
    git_repository.run_git(repository, "update-ref", second, old_oid)
    (repository / "kept.txt").write_text("new user data\n", encoding="utf-8")
    new_oid = git_repository.git_output(repository, "stash", "create")
    git_repository.run_git(repository, "update-ref", "refs/manual/race", new_oid)
    run = update_dotfiles_stash.git_command.run

    def concurrent_run(arguments: list[str], *args: Any, **kwargs: Any) -> Any:
        if arguments == ["update-ref", "--stdin"]:
            git_repository.run_git(repository, "update-ref", first, new_oid)
        return run(arguments, *args, **kwargs)

    monkeypatch.setattr(update_dotfiles_stash.git_command, "run", concurrent_run)

    assert update_dotfiles_stash.protect_old(cwd=repository) == 1
    assert git_repository.git_output(repository, "rev-parse", first) == new_oid
    assert git_repository.git_output(repository, "rev-parse", second) == old_oid
    assert (repository / "kept.txt").read_text(encoding="utf-8") == "new user data\n"


def test_linked_worktree_uses_its_own_protection_and_cleanup(tmp_path: pathlib.Path) -> None:
    """同labelのmain・linked退避を区別し、現在worktreeの整理だけを行う。"""
    repository = _repository(tmp_path / "repo")
    linked = tmp_path / "linked"
    git_repository.run_git(repository, "worktree", "add", "-b", "linked", str(linked))
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, main_oid = _save_old(repository, "same")
    main_shared = ref.replace("refs/worktree/", "refs/atk/worktree-stash/main/")
    git_repository.run_git(repository, "update-ref", main_shared, main_oid)
    (linked / "kept.txt").write_text("linked user data\n", encoding="utf-8")
    linked_ref, linked_oid = _save_old(linked, "same")

    assert update_dotfiles_stash.protect_old(cwd=linked) == 0

    git_dir = pathlib.Path(git_repository.git_output(linked, "rev-parse", "--absolute-git-dir"))
    linked_shared = f"refs/atk/worktree-stash/linked/{git_dir.name.encode('utf-8').hex()}/update-dotfiles-same"
    assert git_repository.git_output(linked, "rev-parse", linked_shared) == linked_oid
    assert git_repository.git_output(repository, "rev-parse", ref) == main_oid
    assert git_repository.git_output(repository, "rev-parse", main_shared) == main_oid
    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert git_repository.run_git(repository, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 1
    assert git_repository.git_output(linked, "rev-parse", linked_ref) == linked_oid
    assert git_repository.git_output(linked, "rev-parse", linked_shared) == linked_oid


def test_retention_branch_change_aborts_cleanup(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """履歴の保持先branchが判定後に移動しても、退避元commitの唯一の参照を削除しない。"""
    repository = _repository(tmp_path / "repo")
    initial = git_repository.git_output(repository, "rev-parse", "HEAD")
    (repository / "committed.txt").write_text("local committed data\n", encoding="utf-8")
    commit = git_repository.commit_all(repository, "ローカルcommit")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, oid = _save_old(repository, "moving-branch")
    run = update_dotfiles_stash.git_command.run

    def concurrent_run(arguments: list[str], *args: Any, **kwargs: Any) -> Any:
        if arguments == ["update-ref", "--stdin"]:
            git_repository.run_git(repository, "update-ref", "refs/heads/main", initial)
        return run(arguments, *args, **kwargs)

    monkeypatch.setattr(update_dotfiles_stash.git_command, "run", concurrent_run)

    assert update_dotfiles_stash.protect_old(cwd=repository) == 1
    assert git_repository.git_output(repository, "rev-parse", ref) == oid
    assert git_repository.git_output(repository, "show", f"{commit}:committed.txt") == "local committed data"


def test_symbolic_remote_head_does_not_lock_same_ref_twice(tmp_path: pathlib.Path) -> None:
    """origin/HEADの別名と実体を区別し、通常cloneの回収を二重ロックで止めない。"""
    repository = _repository(tmp_path / "repo")
    git_repository.run_git(repository, "update-ref", "refs/remotes/origin/main", "HEAD")
    git_repository.run_git(repository, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, _oid = _save_old(repository, "remote-alias")

    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert git_repository.run_git(repository, "show-ref", "--verify", "--quiet", ref, check=False).returncode == 1


def test_user_diff_configuration_does_not_hide_saved_gitlink(tmp_path: pathlib.Path) -> None:
    """ユーザーのdiff設定でsubmodule差分が省略されても、保存したGitlink変更を捨てない。"""
    repository = _repository(tmp_path / "repo")
    module = git_repository.init_repository(repository / "module", files={"data.txt": "base\n"}, commit_message="基準")
    git_repository.commit_all(repository, "Gitlinkの基準")
    (module / "data.txt").write_text("user committed data\n", encoding="utf-8")
    module_commit = git_repository.commit_all(module, "保存する変更")
    git_repository.run_git(repository, "add", "module")
    (repository / "mise.lock").write_text("generated\n", encoding="utf-8")
    ref, oid = _save_old(repository, "gitlink")
    git_repository.run_git(repository, "config", "diff.ignoreSubmodules", "all")

    assert update_dotfiles_stash.protect_old(cwd=repository) == 0
    assert git_repository.git_output(repository, "rev-parse", ref) == oid
    assert git_repository.git_output(repository, "rev-parse", f"{ref}^2:module") == module_commit
