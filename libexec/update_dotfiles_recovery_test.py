# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""update_dotfilesのrecoveryの試験。"""

# pylint: disable=protected-access

import json
import os
import pathlib
import subprocess
import sys

import pytest
from agent_toolkit._testing import git_repository

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import update_dotfiles  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_git  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_process  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_runtime  # noqa: E402  # pylint: disable=wrong-import-position
import update_dotfiles_stashing  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import (  # noqa: E402  # pylint: disable=wrong-import-position
    _REAL_PROCESS_ITER,
    _REAL_UPDATE_GIT_WITH_RECOVERY,
    _assert_saved_changes_survive_gc,
    _create_git_pair,
    _dirty_snapshot,
    _fake_popen,
    _fake_run,
    _git,
    _patch_real_pull,
    _prepare_dirty,
    _TimeoutPopen,
    _track_mise_lock,
    _UnreadableGitProcess,
)
from update_dotfiles_test_helpers import _isolate_sync_report as _isolate_sync_report  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _logs_environment as _logs_environment  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _no_codex_processes as _no_codex_processes  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _own_git_process_scan as _own_git_process_scan  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _real_stash_runtime as _real_stash_runtime  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _separate_git_recovery as _separate_git_recovery  # noqa: E402  # pylint: disable=wrong-import-position
from update_dotfiles_test_helpers import _skip_agent_toolkit_prepare as _skip_agent_toolkit_prepare  # noqa: E402  # pylint: disable=wrong-import-position


def test_save_worktree_uses_repository_launcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """worktree退避は同じPythonからcheckoutの共通実装を起動する。"""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "Popen",
        _fake_popen({}, calls, stdout_by_command={sys.executable: "refs/worktree/test\n"}),
    )

    assert update_dotfiles_stashing.save_worktree("test") == "refs/worktree/test"  # pylint: disable=protected-access
    assert calls[0][0] == sys.executable
    assert pathlib.Path(calls[0][1]) == pathlib.Path(update_dotfiles.__file__).with_name("update_dotfiles_stash.py")
    assert calls[0][2:] == ["save", "--label=test"]


def test_alternate_index_is_passed_only_to_update_git(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """本体Gitと退避だけが代替indexを受け取り、pluginとchezmoiは通常環境を使う。"""
    monkeypatch.delenv("GIT_INDEX_FILE", raising=False)
    alternate = tmp_path / "alternate-index"
    monkeypatch.setattr(update_dotfiles_runtime, "update_index", alternate)
    calls: list[list[str]] = []
    environments: list[dict[str, str]] = []
    monkeypatch.setattr(subprocess, "Popen", _fake_popen({}, calls, environments=environments))
    monkeypatch.setattr(subprocess, "run", _fake_run({}, calls, environments=environments))
    assert update_dotfiles_process.run_git_pull(1, 4) == 0
    assert update_dotfiles_stashing._run_worktree_stash("protect-old") is not None
    assert update_dotfiles_process._git_capture("status").returncode == 0
    assert update_dotfiles_process.run_git_pull(1, 4, plugin=tmp_path / "plugin") == 0
    assert update_dotfiles._run_step(2, 4, "init", ["chezmoi", "init"])[0] == 0
    assert all(env["GIT_INDEX_FILE"] == str(alternate) for env in environments[:3])
    assert all("GIT_INDEX_FILE" not in env for env in environments[3:])


def test_save_worktree_reports_launcher_failure(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """ランチャー起動不能はtracebackを抑止して文脈付き失敗にする。"""
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))

    assert update_dotfiles_stashing.save_worktree("test") is None  # pylint: disable=protected-access
    captured = capsys.readouterr()
    assert "未コミット内容の退避操作を開始できませんでした" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize("always_timeout", [False, True])
def test_old_stash_timeout_continues_update_and_records_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    always_timeout: bool,
) -> None:
    """旧退避の保護が停止しても有限回の回収で戻り、失敗を記録してpullへ進み、終了コード1で終える。"""
    ref = "refs/worktree/update-dotfiles-old"
    calls: list[list[str]] = []
    timeouts: list[int | None] = []
    process = _TimeoutPopen(
        ["atk", "worktree-stash", "protect", ref],
        calls,
        output_text="partial stdout",
        error_text="partial stderr",
        communicate_timeouts=timeouts,
        always_timeout=always_timeout,
    )
    monkeypatch.setattr(update_dotfiles_git, "update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    monkeypatch.setattr(update_dotfiles_git, "_git_operation_in_progress", lambda: False)

    def git_capture(*args: str) -> subprocess.CompletedProcess[str]:
        if args[-1] == "index":
            return subprocess.CompletedProcess([], 0, str(tmp_path / "index"), "")
        if "status" in args or "--others" in args:
            return subprocess.CompletedProcess([], 0, "", "")
        return subprocess.CompletedProcess([], 0, ref + "\n", "")

    monkeypatch.setattr(update_dotfiles_process, "_git_capture", git_capture)
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", tmp_path)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    pulls: list[int] = []

    def fake_pull(*_args: object, **_kwargs: object) -> int:
        pulls.append(1)
        return 0

    monkeypatch.setattr(update_dotfiles_process, "run_git_pull", fake_pull)
    monkeypatch.setattr(subprocess, "Popen", lambda *_args, **_kwargs: process)
    chezmoi_calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run({}, chezmoi_calls))
    killed: list[int] = []
    monkeypatch.setattr(update_dotfiles_process, "kill_process_tree", lambda child: killed.append(child.pid))

    assert update_dotfiles.main() == 1
    assert timeouts == [120, 30]
    assert killed == [process.pid]
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    assert pulls == [1]
    assert [call[1] for call in chezmoi_calls] == ["init", "status", "diff", "apply"]
    assert report["failed_stage"] == "過去の退避データの整理"
    assert "120秒以内" in report["stderr_tail"]
    captured = capsys.readouterr()
    assert "worktree-stash" not in captured.out
    assert "過去の退避データを確認できませんでしたが、更新を続けます" in captured.err
    assert "git statusとupdate-dotfiles logs" in captured.err
    log = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
    assert "退避操作開始" in log
    assert "退避操作timeout" in log
    if not always_timeout:
        assert "partial stdout" in log
        assert "partial stderr" in report["stderr_tail"]


@pytest.mark.usefixtures("real_stash_runtime")
class TestGitConflictRecovery:
    """一時Git上流でcommit競合と未コミット復元競合からの回復を検証する。"""

    @pytest.mark.parametrize("dirty", [False, True])
    def test_rebase_conflict_preserves_original_commit_and_tracks_upstream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, dirty: bool
    ) -> None:
        local, seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("local commit\n", encoding="utf-8")
        _git(local, "add", "tracked.txt")
        _git(local, "commit", "-m", "local")
        original_head = _git(local, "rev-parse", "HEAD")
        if dirty:
            (local / "staged.txt").write_text("staged\n", encoding="utf-8")
            _git(local, "add", "staged.txt")
            (local / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
            (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        (seed / "tracked.txt").write_text("upstream\n", encoding="utf-8")
        _git(seed, "add", "tracked.txt")
        _git(seed, "commit", "-m", "upstream")
        _git(seed, "push")
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        recovery_refs = _git(local, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads").splitlines()
        recovery = next(line.split()[1] for line in recovery_refs if "refs/heads/update-dotfiles-recovery-" in line)
        assert result == 0
        assert _git(local, "rev-parse", "HEAD") == _git(local, "rev-parse", "@{upstream}")
        assert recovery == original_head
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "upstream\n"
        if dirty:
            _assert_saved_changes_survive_gc(local, tmp_path)

    @pytest.mark.parametrize(
        "untracked_name",
        [
            "untracked.txt",
            "日本語 space.txt",
            pytest.param(
                " 日本語 space\n改行.txt ",
                marks=pytest.mark.skipif(os.name == "nt", reason="Windowsのファイル名は改行と末尾空白を許可しない"),
            ),
        ],
    )
    def test_dirty_restore_conflict_keeps_recoverable_ref_and_clean_upstream(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, untracked_name: str
    ) -> None:
        local, seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        (local / untracked_name).write_text("untracked\n", encoding="utf-8")
        _git(local, "add", "staged.txt")
        (seed / "tracked.txt").write_text("upstream\n", encoding="utf-8")
        _git(seed, "add", "tracked.txt")
        _git(seed, "commit", "-m", "upstream")
        _git(seed, "push")
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        worktree_refs = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree").splitlines()
        stash_ref = next(ref for ref in worktree_refs if ref.startswith("refs/worktree/update-dotfiles-"))
        saved_paths = git_repository.run_git(
            local, "stash", "show", "--include-untracked", "--name-only", "-z", stash_ref
        ).stdout.split("\0")
        assert result == 0
        assert _git(local, "status", "--porcelain=v1") == ""
        assert _git(local, "rev-parse", "HEAD") == _git(local, "rev-parse", "@{upstream}")
        assert {"tracked.txt", "staged.txt", untracked_name} <= set(saved_paths)
        _assert_saved_changes_survive_gc(local, tmp_path, untracked_name=untracked_name)

    def test_foreign_unreadable_git_process_does_not_block_recovery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """試験外の照会不能なgitプロセスがあっても、回復処理へ到達して退避と上流追従を終える。"""
        foreign = _UnreadableGitProcess(pid=-1)
        original = _REAL_PROCESS_ITER
        monkeypatch.setattr(
            sys.modules[__name__], "_REAL_PROCESS_ITER", lambda *args, **kwargs: iter([foreign, *original(*args, **kwargs)])
        )
        self.test_dirty_restore_conflict_keeps_recoverable_ref_and_clean_upstream(monkeypatch, tmp_path, "日本語 space.txt")


@pytest.mark.usefixtures("real_stash_runtime")
class TestStashLifecycle:
    """公開された更新コマンドで復元・回収・旧退避保護の寿命を確認する。"""

    @pytest.mark.parametrize("pull_code,cleanup_failure", [(0, False), (7, False), (0, True), (7, True)])
    def test_restored_changes_survive_cleanup_outcome(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
        pull_code: int,
        cleanup_failure: bool,
    ) -> None:
        local, _seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        _git(local, "add", "staged.txt")
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
        monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
        monkeypatch.setattr(update_dotfiles_git, "update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
        monkeypatch.setattr(update_dotfiles_process, "run_git_pull", lambda *_args, **_kwargs: pull_code)
        monkeypatch.setattr(update_dotfiles, "_run_step", lambda *_args, **_kwargs: (0, ""))
        monkeypatch.setattr(update_dotfiles, "_pause_codex_daemon", lambda: None)
        run_stash = update_dotfiles_stashing._run_worktree_stash

        def fail_drop(*arguments: str) -> subprocess.CompletedProcess[str] | None:
            if cleanup_failure and arguments[0] == "drop":
                return None
            return run_stash(*arguments)

        monkeypatch.setattr(update_dotfiles_stashing, "_run_worktree_stash", fail_drop)
        assert update_dotfiles.main() == (pull_code or int(cleanup_failure))
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "unstaged\n"
        assert (local / "staged.txt").read_text(encoding="utf-8") == "staged\n"
        assert (local / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        assert set(_git(local, "status", "--porcelain=v1").splitlines()) == {
            " M tracked.txt",
            "A  staged.txt",
            "?? untracked.txt",
        }
        refs = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree", "refs/atk/worktree-stash").splitlines()
        assert len(refs) == (2 if cleanup_failure else 0)
        if cleanup_failure:
            diagnostic = capsys.readouterr().err
            ref = next(ref for ref in refs if ref.startswith("refs/worktree/"))
            assert "復元は成功" in diagnostic
            assert f"atk worktree-stash drop {ref}" in diagnostic
            assert run_stash("drop", ref) is not None
            assert _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree", "refs/atk/worktree-stash") == ""

    def test_old_ref_is_protected_before_pull_gc(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        local, _seed, _remote = _create_git_pair(tmp_path)
        (local / "tracked.txt").write_text("legacy\n", encoding="utf-8")
        _git(local, "stash", "push")
        ref = "refs/worktree/update-dotfiles-old"
        _git(local, "update-ref", ref, _git(local, "rev-parse", "refs/stash"))
        _git(local, "stash", "drop")
        other = tmp_path / "other"
        _git(local, "worktree", "add", "-b", "other", str(other))
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)

        def gc_during_pull(*_args: object, **_kwargs: object) -> int:
            _git(other, "reflog", "expire", "--expire=now", "--expire-unreachable=now", "--all")
            _git(other, "gc", "--prune=now")
            return 0

        monkeypatch.setattr(update_dotfiles_process, "run_git_pull", gc_during_pull)
        assert _REAL_UPDATE_GIT_WITH_RECOVERY(1, 4, timeout=30) == 0
        _git(local, "stash", "apply", "--index", ref)
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "legacy\n"


@pytest.mark.usefixtures("real_stash_runtime")
class TestMiseLockDiscard:
    """pull前に`mise.lock`の差分を破棄し、他の未コミット内容だけを退避・復元する。"""

    def test_mise_lock_only_change_is_discarded_without_stash(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """`mise.lock`だけの差分では退避用refを作成せず、更新後の`mise.lock`が上流と一致する。"""
        local, seed, _remote = _create_git_pair(tmp_path)
        _track_mise_lock(local, seed)
        (local / "mise.lock").write_text("rewritten by mise\n", encoding="utf-8")
        _git(local, "add", "mise.lock")
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        assert result == 0
        assert _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree") == ""
        assert _git(local, "status", "--porcelain=v1") == ""
        assert (local / "mise.lock").read_text(encoding="utf-8") == "lock v2\n"
        assert "未コミット内容" not in capsys.readouterr().out

    def test_other_changes_are_stashed_and_restored_without_mise_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """他のファイルの差分（stage済み・未追跡を含む）は退避・復元し、退避内容に`mise.lock`を含めない。"""
        local, seed, _remote = _create_git_pair(tmp_path)
        _track_mise_lock(local, seed)
        (local / "mise.lock").write_text("rewritten by mise\n", encoding="utf-8")
        (local / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        (local / "staged.txt").write_text("staged\n", encoding="utf-8")
        _git(local, "add", "staged.txt")
        (local / "untracked.txt").write_text("untracked\n", encoding="utf-8")
        monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
        _patch_real_pull(monkeypatch, local)

        saved_paths: set[str] = set()
        original_restore = update_dotfiles_stashing.restore_worktree

        def observe_restore(ref: str) -> bool:
            saved_paths.update(_git(local, "stash", "show", "--include-untracked", "--name-only", ref).splitlines())
            return original_restore(ref)

        monkeypatch.setattr(update_dotfiles_stashing, "restore_worktree", observe_restore)

        result = _REAL_UPDATE_GIT_WITH_RECOVERY(1, 5, timeout=30)

        assert _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree", "refs/atk/worktree-stash") == ""
        assert result == 0
        assert {"tracked.txt", "staged.txt", "untracked.txt"} <= saved_paths
        assert "mise.lock" not in saved_paths
        assert (local / "mise.lock").read_text(encoding="utf-8") == "lock v2\n"
        assert (local / "tracked.txt").read_text(encoding="utf-8") == "dirty\n"
        assert (local / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        status = _git(local, "status", "--porcelain=v1").splitlines()
        assert "A  staged.txt" in status
        assert not [line for line in status if line.endswith("mise.lock")]


@pytest.mark.parametrize(
    "state,command",
    [
        ("MERGE_HEAD", "merge"),
        ("rebase-merge", "rebase"),
        ("rebase-apply", "rebase"),
        ("CHERRY_PICK_HEAD", "cherry-pick"),
        ("REVERT_HEAD", "revert"),
        ("sequencer", "cherry-pick"),
    ],
)
def test_update_pending_operation_explains_completion_and_abort(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    state: str,
    command: str,
) -> None:
    """操作状態による見送りが作業を保持し、手動の完了・中止手順を保存する。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    _prepare_dirty(local)
    before = _dirty_snapshot(local)
    (local / ".git" / state).touch()
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(update_dotfiles_git, "update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    assert update_dotfiles.main() == 1
    assert _dirty_snapshot(local) == before
    report = json.loads(sync_report_path.read_text(encoding="utf-8"))
    for text in ("git status", f"git {command}", "--continue", "--abort"):
        assert text in report["stderr_tail"]
        assert text in update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")


@pytest.mark.usefixtures("real_stash_runtime")
@pytest.mark.parametrize("pull_code", [0, 7])
def test_update_restore_overwrite_refusal_records_recovery(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    sync_report_path: pathlib.Path,
    pull_code: int,
) -> None:
    """実Gitの上書き拒否から、保存ログと失敗同期結果で診断・退避先へ到達できる。"""
    local, _seed, _remote = _create_git_pair(tmp_path)
    (local / "tracked.txt").write_text("保存対象\n", encoding="utf-8")
    monkeypatch.setattr(update_dotfiles_runtime, "DOTFILES_ROOT", local)
    monkeypatch.setattr(update_dotfiles_runtime, "LOCK_PATH", tmp_path / "update.lock")
    monkeypatch.setattr(update_dotfiles_git, "update_git_with_recovery", _REAL_UPDATE_GIT_WITH_RECOVERY)
    monkeypatch.setattr(update_dotfiles, "_run_step", lambda *_args, **_kwargs: (0, ""))

    def overwrite(*_args: object, **_kwargs: object) -> int:
        (local / "tracked.txt").write_text("復元を拒否する変更\n", encoding="utf-8")
        return pull_code

    monkeypatch.setattr(update_dotfiles_process, "run_git_pull", overwrite)
    assert update_dotfiles.main() == pull_code
    ref = _git(local, "for-each-ref", "--format=%(refname)", "refs/worktree").splitlines()[0]
    log = update_dotfiles_runtime.LOG_PATH.read_text(encoding="utf-8")
    assert f"git stash apply --index {ref}: exit=1" in log
    assert "would be overwritten" in log
    assert "復元に失敗" in log
    assert f"git stash apply --index {ref}" in log
    if pull_code:
        report = json.loads(sync_report_path.read_text(encoding="utf-8"))
        assert "would be overwritten" in report["stderr_tail"]
        assert "復元に失敗" in report["stderr_tail"]
        assert ref in report["stderr_tail"]
    else:
        assert "競合から回復" in log
        assert "git switch update-dotfiles-recovery-" in log
