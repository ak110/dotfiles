"""`atk wi process-loop`のセッションを起動する作業ツリーの準備と上流との同期のテスト。"""

import contextlib
import os
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Callable, Generator
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.process_loop_support import (
    DOTFILES_REPO_ID,
    fake_run_with_remote_url,
    isolate_process_loop_commands,
    raise_system_exit_0,
    set_orchestrate_model,
)
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def _prepare_process_loop_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """公開CLIテストの外部コマンド解決・private-notes同期・managed-tempの登録簿を隔離する。"""
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def _make_remote_repository(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """mainブランチを持つローカルoriginとcloneを生成する。"""
    remote = tmp_path / f"{name}-origin.git"
    seed = tmp_path / f"{name}-seed"
    local = tmp_path / name
    git_repository.init_bare_repository(remote)
    git_repository.init_repository(seed, initial_branch="main")
    git_repository.run_git(seed, "config", "user.name", "test")
    git_repository.run_git(seed, "config", "user.email", "test@example.invalid")
    (seed / "state.txt").write_text("base\n", encoding="utf-8")
    git_repository.run_git(seed, "add", "state.txt")
    git_repository.run_git(seed, "commit", "-m", "base")
    git_repository.run_git(seed, "remote", "add", "origin", str(remote))
    git_repository.run_git(seed, "push", "-u", "origin", "main")
    git_repository.run_git(tmp_path, "clone", str(remote), str(local))
    git_repository.run_git(local, "config", "user.name", "test")
    git_repository.run_git(local, "config", "user.email", "test@example.invalid")
    git_repository.run_git(local, "remote", "set-head", "origin", "-a")
    return local


def _run_public_process_loop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    local_path: pathlib.Path,
    worktree_args: list[str],
    *,
    git_override: Callable[[list[str], pathlib.Path], subprocess.CompletedProcess[Any] | None] | None = None,
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """公開CLIを1反復だけ実行し、セッションとGit呼び出しを返す。"""
    if not (tmp_path / "private-notes").exists():
        _setup_notes(tmp_path)
    session_calls: list[dict[str, Any]] = []
    git_calls: list[list[str]] = []
    real_run: Any = subprocess.run

    def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        if cmd[:1] in (["claude"], ["codex"]):
            # `claude auth status`は待機間隔をキャッシュTTLで決めるための事前照会であり、
            # 委譲セッションの起動ではないため件数へ数えない。
            if cmd[:3] != ["claude", "auth", "status"]:
                session_calls.append({"cmd": list(cmd), "cwd": kwargs.get("cwd")})
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
        if cmd[:1] == ["git"]:
            git_calls.append(list(cmd))
            if git_override is not None and "cwd" in kwargs:
                overridden = git_override(cmd, pathlib.Path(str(kwargs["cwd"])))
                if overridden is not None:
                    return overridden
        return real_run(cmd, *args, **kwargs)  # pylint: disable=subprocess-run-check  # 実Git実行へそのまま委譲する

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(_process_loop, "resolve_repo_id", lambda *_args, **_kwargs: "github.com/example/repo")
    monkeypatch.setattr(
        _pl_session,
        "select_available_orchestrator",
        lambda candidates, _env, _cwd: candidates[0],
    )
    counts = iter((1, 0))
    monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_args, **_kwargs: next(counts))

    def stop_wait(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "process-loop",
                f"--target-repo={local_path}",
                "--no-update",
                "--no-alerts",
                *worktree_args,
            ],
            home=tmp_path,
        )
    assert exc_info.value.code == 0
    return session_calls, git_calls


class TestSyncWorktreeWithUpstream:
    """反復間で再利用するworktreeを上流最新へ追随させる処理。"""

    @staticmethod
    def _make_worktree(tmp_path: pathlib.Path) -> pathlib.Path:
        """対象リポジトリ配下へworktreeディレクトリを生成し、そのパスを返す。"""
        worktree = tmp_path / "repo" / ".claude" / "worktrees" / "process-loop"
        worktree.mkdir(parents=True)
        return worktree

    def test_rebases_existing_worktree_onto_upstream(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """既存worktreeに対しfetchと上流ブランチへのrebaseを実行する。"""
        worktree = self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:2] == ["symbolic-ref"]:
                stdout = "origin/master"
            elif cmd[1:2] == ["remote"]:
                stdout = "origin\n"
            else:
                stdout = ""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "fetch", "origin"] in calls
        assert ["git", "rebase", "origin/master"] in calls
        assert worktree.is_dir()

    def test_tracking_branch_has_priority_without_origin_head(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """origin/HEADが無くても現在ブランチの追跡先を使って追随する。"""
        worktree = self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str | None:
            del cwd
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
                return "origin/feature"
            if args == ["remote"]:
                return "origin"
            if args == ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
                raise AssertionError("追跡先が解決できた場合はorigin/HEADへ後退しないこと")
            return None

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "fetch", "origin"] in calls
        assert ["git", "rebase", "origin/feature"] in calls

    def test_fetches_tracking_remote_before_rebase(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """origin以外の追跡remoteをfetchし、同じ追跡先へrebaseする。"""
        worktree = self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str | None:
            del cwd
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
                return "upstream/feature"
            if args == ["remote"]:
                return "origin\nupstream"
            raise AssertionError(args)

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "fetch", "upstream"] in calls
        assert ["git", "fetch", "origin"] not in calls
        assert ["git", "rebase", "upstream/feature"] in calls

    def test_reuses_existing_branch_when_worktree_absent(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """worktree未作成でも専用ブランチがあれば同ブランチから作成する。"""
        local_path = tmp_path / "repo"
        worktree = local_path / ".claude" / "worktrees" / "process-loop"
        calls: list[list[str]] = []

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str:
            del cwd
            return "origin" if args == ["remote"] else "origin/master"

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)
        monkeypatch.setattr(_pl_worktree, "_worktree_is_clean", lambda path: path == worktree)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:4] == ["worktree", "list", "--porcelain"]:
                return subprocess.CompletedProcess(
                    cmd,
                    returncode=0,
                    stdout="worktree /existing/worktree\nbranch refs/heads/worktree-process-loop\n",
                    stderr="",
                )
            if cmd[1:3] == ["worktree", "add"]:
                worktree.mkdir(parents=True)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "worktree", "add", str(worktree), "worktree-process-loop"] in calls
        assert ["git", "rebase", "origin/master"] in calls

    def test_aborts_rebase_and_warns_on_conflict(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rebase失敗時は中断したうえで警告を発し、実装セッションを起動しない。"""
        self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:2] == ["symbolic-ref"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="origin/master", stderr="")
            if cmd[1:2] == ["remote"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="origin\n", stderr="")
            if cmd[1:3] == ["rebase", "origin/master"]:
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="conflict")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "rebase", "--abort"] in calls
        stderr = capsys.readouterr().err
        assert "追随に失敗したため実装セッションを起動しません" in stderr
        # 手作業で競合を解消するrebaseのコマンドを次の操作として続ける。
        assert "rebase origin/master`を手作業で実行" in stderr.split("\n次の操作: ", 1)[1]

    def test_unresolved_upstream_guides_upstream_setting(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """上流ブランチを解決できない場合は、上流の設定コマンドを次の操作として返し実装セッションを起動しない。"""
        self._make_worktree(tmp_path)
        local_path = tmp_path / "repo"
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _path: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[1:2] in (["rev-parse"], ["symbolic-ref"]):
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="no upstream")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop") is None  # pylint: disable=protected-access  # noqa: SLF001

        stderr = capsys.readouterr().err
        assert "上流ブランチを解決できないため実装セッションを起動しません" in stderr
        assert f"`git -C {local_path} branch -u <remote>/<branch>`" in stderr.split("\n次の操作: ", 1)[1]


class TestPublicWorktreePreparation:
    """公開CLIからのworktree準備と失敗時の停止条件を検証する。"""

    def test_unignored_path_is_added_to_info_exclude_and_worktree_created(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """未除外の初回作成で末尾改行を補い、除外確認後にworktreeを作成する。"""
        local_path = _make_remote_repository(tmp_path, "target")
        exclude_path = local_path / ".git" / "info" / "exclude"
        exclude_path.write_text("# existing", encoding="utf-8")

        session_calls, _git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree=custom"],
        )

        worktree_path = local_path / ".claude" / "worktrees" / "custom"
        assert len(session_calls) == 1
        assert session_calls[0]["cwd"] == worktree_path
        assert "現在のHEADを`origin/main`へ反映" not in session_calls[0]["cmd"][-1]
        assert exclude_path.read_text(encoding="utf-8") == "# existing\n/.claude/worktrees/\n"
        assert exclude_path.read_text(encoding="utf-8").splitlines().count("/.claude/worktrees/") == 1
        check_ignore = git_repository.run_git(local_path, "check-ignore", "-q", ".claude/worktrees/", check=False)
        assert check_ignore.returncode == 0
        assert worktree_path.is_dir()

        second_session_calls, second_git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree=custom"],
        )
        assert len(second_session_calls) == 1
        assert second_session_calls[0]["cwd"] == worktree_path
        assert any(command[:2] == ["git", "fetch"] and command[-1] == "origin" for command in second_git_calls)
        assert exclude_path.read_text(encoding="utf-8").splitlines().count("/.claude/worktrees/") == 1

    def test_existing_ignore_is_not_changed(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """既に除外済みのリポジトリではinfo/excludeを追記しない。"""
        local_path = _make_remote_repository(tmp_path, "target")
        exclude_path = local_path / ".git" / "info" / "exclude"
        before = "/.claude/worktrees/\n"
        exclude_path.write_text(before, encoding="utf-8")

        session_calls, _git_calls = _run_public_process_loop(monkeypatch, tmp_path, local_path, [])

        assert len(session_calls) == 1
        assert exclude_path.read_text(encoding="utf-8") == before

    def test_invalid_git_ref_does_not_modify_repository_or_start_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """argparseを通過するname.lockでもGit ref検証失敗時は停止する。"""
        local_path = _make_remote_repository(tmp_path, "target")
        exclude_path = local_path / ".git" / "info" / "exclude"
        before = exclude_path.read_text(encoding="utf-8")

        session_calls, git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree=name.lock"],
        )

        assert not session_calls
        assert exclude_path.read_text(encoding="utf-8") == before
        assert not (local_path / ".claude").exists()
        assert any(command[:3] == ["git", "check-ref-format", "--branch"] for command in git_calls)
        assert not any(len(command) > 1 and command[1] == "worktree" for command in git_calls)

    @pytest.mark.parametrize("failure_kind", ["empty", "common-dir", "show-toplevel", "branch"])
    def test_existing_non_worktree_directory_is_rejected_before_fetch_or_rebase(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        failure_kind: str,
    ) -> None:
        """既存ディレクトリの照会失敗または不一致時はfetch・rebaseへ進まない。"""
        local_path = _make_remote_repository(tmp_path, "target")
        worktree_path = local_path / ".claude" / "worktrees" / "custom"
        worktree_path.mkdir(parents=True)
        local_common = local_path / ".git"

        def override(cmd: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[Any] | None:
            empty = subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            if cmd == ["git", "check-ref-format", "--branch", "worktree-custom"]:
                return empty
            if cmd == ["git", "check-ignore", "-q", ".claude/worktrees/"]:
                return empty
            if cmd == ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="origin/main", stderr="")
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                if failure_kind == "empty":
                    return empty
                common = local_common if cwd == local_path else tmp_path / "other.git"
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=str(common), stderr="")
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                top = local_path if failure_kind == "show-toplevel" else worktree_path
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=str(top), stderr="")
            if cmd == ["git", "symbolic-ref", "--short", "HEAD"]:
                branch = "main" if failure_kind == "branch" else "worktree-custom"
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=branch, stderr="")
            return None

        session_calls, git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree=custom"],
            git_override=override,
        )

        assert not session_calls
        assert not any(len(command) > 1 and command[1] in ("fetch", "rebase") for command in git_calls)

    def test_exclusion_check_failure_does_not_start_session_or_modify_info_exclude(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """check-ignoreのfatal終了時は除外設定とworktreeを変更しない。"""
        local_path = _make_remote_repository(tmp_path, "target")
        exclude_path = local_path / ".git" / "info" / "exclude"
        before = exclude_path.read_text(encoding="utf-8")

        def override(cmd: list[str], _cwd: pathlib.Path) -> subprocess.CompletedProcess[Any] | None:
            if cmd == ["git", "check-ignore", "-q", ".claude/worktrees/"]:
                return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal")
            return None

        session_calls, _git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree=custom"],
            git_override=override,
        )

        assert not session_calls
        assert exclude_path.read_text(encoding="utf-8") == before
        assert not (local_path / ".claude").exists()

    def test_exclusion_append_failure_does_not_start_session_or_create_worktree(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """info/excludeへの追記が失敗した場合はworktree準備を停止する。"""
        local_path = _make_remote_repository(tmp_path, "target")
        exclude_path = local_path / ".git" / "info" / "exclude"
        before = exclude_path.read_text(encoding="utf-8")
        real_open: Any = pathlib.Path.open
        append_attempted = False

        def fail_append(
            path: pathlib.Path,
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            nonlocal append_attempted
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == exclude_path and isinstance(mode, str) and mode.startswith("a"):
                append_attempted = True
                raise OSError("simulated info/exclude write failure")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "open", fail_append)
        session_calls, git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree=custom"],
        )

        assert append_attempted
        assert not session_calls
        assert exclude_path.read_text(encoding="utf-8") == before
        assert not (local_path / ".claude").exists()
        assert not any(len(command) > 1 and command[1] in ("fetch", "rebase", "worktree") for command in git_calls)

    def test_existing_unregistered_branch_is_not_reused_or_rebased(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """配置先不在ブランチの所有権を確認できない場合はOIDを変更せず停止する。"""
        local_path = _make_remote_repository(tmp_path, "target")
        git_repository.run_git(local_path, "branch", "worktree-process-loop")
        before = git_repository.run_git(local_path, "rev-parse", "refs/heads/worktree-process-loop").stdout.strip()

        session_calls, git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree"],
        )

        after = git_repository.run_git(local_path, "rev-parse", "refs/heads/worktree-process-loop").stdout.strip()
        assert not session_calls
        assert before == after
        assert not (local_path / ".claude").exists()
        assert any(command[1:4] == ["worktree", "list", "--porcelain"] for command in git_calls)
        assert not any(len(command) > 1 and command[1] in ("fetch", "rebase") for command in git_calls)
        assert not any(command[1:3] == ["worktree", "add"] for command in git_calls)

    def test_exclusion_append_is_idempotent_when_higher_priority_ignore_negates_it(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """再判定が失敗する同一状態を繰り返してもinfo/excludeを重複追記しない。"""
        local_path = _make_remote_repository(tmp_path, "target")
        (local_path / ".gitignore").write_text("!/.claude/worktrees/\n", encoding="utf-8")
        git_repository.run_git(local_path, "add", ".gitignore")
        git_repository.run_git(local_path, "commit", "-m", "negate worktree ignore")
        exclude_path = local_path / ".git" / "info" / "exclude"
        before = exclude_path.read_text(encoding="utf-8")
        _setup_notes(tmp_path)
        session_calls: list[dict[str, Any]] = []
        real_run: Any = subprocess.run

        def fake_run(cmd: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
            if cmd[:1] in (["claude"], ["codex"]):
                session_calls.append({"cmd": list(cmd), "cwd": kwargs.get("cwd")})
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")
            return real_run(cmd, *args, **kwargs)  # pylint: disable=subprocess-run-check  # 実Git実行へそのまま委譲する

        monkeypatch.setattr(subprocess, "run", fake_run)
        monkeypatch.setattr(_process_loop, "resolve_repo_id", lambda *_args, **_kwargs: "github.com/example/repo")
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        def run_once() -> None:
            counts = iter((1,))
            monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
            with pytest.raises(SystemExit) as exc_info:
                atk.main(
                    [
                        "wi",
                        "process-loop",
                        f"--target-repo={local_path}",
                        "--worktree=custom",
                        "--no-update",
                        "--no-alerts",
                    ],
                    home=tmp_path,
                )
            assert exc_info.value.code == 0

        run_once()
        after_first = exclude_path.read_text(encoding="utf-8")
        run_once()
        after_second = exclude_path.read_text(encoding="utf-8")
        assert not session_calls
        assert after_first == after_second
        assert after_second.splitlines().count("/.claude/worktrees/") == 1
        assert before != after_second

    def test_target_repository_exclude_is_used_when_process_starts_elsewhere(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """起動cwdが別Gitリポジトリでも対象側のinfo/excludeだけを更新する。"""
        target = _make_remote_repository(tmp_path, "target")
        launcher = _make_remote_repository(tmp_path, "launcher")
        target_exclude = target / ".git" / "info" / "exclude"
        launcher_exclude = launcher / ".git" / "info" / "exclude"
        target_exclude.write_text("# target", encoding="utf-8")
        launcher_before = launcher_exclude.read_text(encoding="utf-8")
        monkeypatch.chdir(launcher)

        session_calls, _git_calls = _run_public_process_loop(monkeypatch, tmp_path, target, ["--worktree=custom"])

        assert len(session_calls) == 1
        assert "/.claude/worktrees/" in target_exclude.read_text(encoding="utf-8").splitlines()
        assert launcher_exclude.read_text(encoding="utf-8") == launcher_before


_PULL_PRIVATE_NOTES_IMPL = _pl_watch.pull_private_notes  # pylint: disable=protected-access  # noqa: SLF001


@pytest.fixture(name="process_loop_commands_isolated")
def _process_loop_commands_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。常駐ループ全体を動かすテストが使う。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestProcessLoopSessionPreparation:
    """ready項目の処理前同期と失敗時のfail-closed動作を検証する。"""

    def test_private_notes_pull_runs_under_repo_lock(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """private-notesのpullが同一リポジトリのlock保持中だけ実行されること。"""
        events: list[str] = []

        @contextlib.contextmanager
        def fake_lock(path: pathlib.Path) -> Generator[None]:
            assert path == tmp_path
            events.append("lock-enter")
            yield
            events.append("lock-exit")

        monkeypatch.setattr(_wi_sync, "repo_lock", fake_lock)
        monkeypatch.setattr(_wi_sync, "pull", lambda path: events.append(f"pull:{path.name}"))

        assert _PULL_PRIVATE_NOTES_IMPL(tmp_path)
        assert events == ["lock-enter", f"pull:{tmp_path.name}", "lock-exit"]

    def test_pull_private_notes_reports_rebase_in_progress(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """rebase中間状態では例外終了せず、復旧待ちとして偽を返す。"""

        def fail_pull(_path: pathlib.Path) -> NoReturn:
            raise _atk_git_sync.RebaseInProgressError("rebase中")

        monkeypatch.setattr(_wi_sync, "pull", fail_pull)

        assert not _PULL_PRIVATE_NOTES_IMPL(tmp_path)
        stderr = capsys.readouterr().err
        assert "remote同期に失敗（子セッションを起動せず待機します）: rebase中" in stderr
        assert "status`で同期状態を確認し" in stderr.split("\n次の操作: ", 1)[1]

    def test_missing_update_command_does_not_repull_or_start_session(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """更新コマンドが未解決なら再pullへ進まず子セッションも起動しないこと。"""
        monkeypatch.setattr(_pl_env, "resolve_executable", lambda _name: None)
        monkeypatch.setattr(
            _pl_watch,
            "pull_private_notes",
            lambda _path: pytest.fail("更新コマンド未解決では再pullしないこと"),
        )

        assert _pl_update.update_before_session(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            None,
            ["atk"],
            {},
        ) == (False, False)

    def test_update_failure_still_starts_session_and_reports_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """更新が非0で終了しても再pullへ進み、子セッションを起動できる状態を返すこと。"""
        monkeypatch.setattr(_pl_env, "resolve_executable", lambda _name: "update-dotfiles")
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda cmd, **_kwargs: subprocess.CompletedProcess(cmd, 1),
        )
        monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _path: True)

        assert _pl_update.update_before_session(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            None,
            ["atk"],
            {},
        ) == (True, False)
        assert "exit code 1" in capsys.readouterr().err

    def test_initial_ready_session_runs_pull_update_repull_before_codex(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """初回ready処理はpull・update・再pull・再集計後にCodexを起動すること。"""
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "codex:gpt-5.6-sol/medium")
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        events: list[str] = []
        counts = iter((1, 1))

        def fake_pull(_path: pathlib.Path) -> bool:
            events.append("pull")
            return True

        def fake_count(*_args: object, **_kwargs: object) -> int:
            events.append("count")
            return next(counts)

        def fake_update(*_args: object, **_kwargs: object) -> tuple[bool, bool]:
            events.extend(("update", "repull"))
            return True, True

        monkeypatch.setattr(_pl_watch, "pull_private_notes", fake_pull)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", fake_count)
        monkeypatch.setattr(_pl_update, "update_before_session", fake_update)
        base_fake_run = fake_run_with_remote_url(myrepo, [], 7)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[:1] == ["codex"] and cmd[:2] != ["codex", "exec"]:
                events.append("session")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                [
                    "wi",
                    "process-loop",
                    f"--target-repo={myrepo}",
                    "--no-alerts",
                ],
                home=tmp_path,
            )

        assert exc_info.value.code == 7
        assert events == ["pull", "count", "update", "repull", "count", "session"]

    def test_successful_ready_update_restart_skips_duplicate_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """開始前更新で再起動しても、同じ上流状態への更新は1回に限定する。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        dotfiles_root = tmp_path / "dotfiles"
        canonical_script = dotfiles_root / "agent-toolkit" / "agent_toolkit" / "atk.py"
        canonical_script.parent.mkdir(parents=True)
        canonical_script.write_text("", encoding="utf-8")
        monkeypatch.setattr(_pl_update, "resolve_dotfiles_root", lambda: dotfiles_root)
        hashes = iter(("before-update", "after-update", "after-update"))
        monkeypatch.setattr(_pl_update, "code_hash", lambda _path: next(hashes))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_args, **_kwargs: 1)
        update_calls: list[list[str]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, [], 7)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if pathlib.Path(cmd[0]).stem.lower() == "update-dotfiles":
                update_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, "", "")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        restart_spec = tmp_path / "restart-spec"
        monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(restart_spec))
        initial_argv = [str(canonical_script), "wi", "process-loop", f"--target-repo={myrepo}", "--no-alerts"]
        monkeypatch.setattr(sys, "argv", initial_argv)

        with pytest.raises(SystemExit) as restart_exit:
            atk.main(initial_argv[1:], home=tmp_path)

        assert restart_exit.value.code == _pl_update._RESTART_EXIT_CODE  # pylint: disable=protected-access  # noqa: SLF001
        restart_argv = restart_spec.read_text(encoding="utf-8").splitlines()
        assert "--internal-dotfiles-updated" in restart_argv
        monkeypatch.setattr(sys, "argv", restart_argv)

        with pytest.raises(SystemExit) as session_exit:
            atk.main(restart_argv[1:], home=tmp_path)

        assert session_exit.value.code == 7
        assert len(update_calls) == 1

    def test_worktree_preparation_failure_returns_to_wait(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """dotfiles用worktreeを準備できない場合はプロセスを異常終了せず待機へ戻る。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "dotfiles"
        myrepo.mkdir()
        session_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, session_calls, 0))
        monkeypatch.setattr(_process_loop, "resolve_local_worktree", lambda _value: myrepo)
        monkeypatch.setattr(_process_loop, "resolve_repo_id", lambda *_args, **_kwargs: DOTFILES_REPO_ID)
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_args, **_kwargs: 1)
        monkeypatch.setattr(_pl_worktree, "_sync_worktree_with_upstream", lambda *_args: None)
        wait_calls: list[pathlib.Path] = []

        def stop_wait(private_notes: pathlib.Path, _target_repo_id: str | None) -> None:
            wait_calls.append(private_notes)
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts"],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert wait_calls == [tmp_path / "private-notes"]
        assert not session_calls
        assert "worktree準備を再試行するまで変更検知を待機します。" in capsys.readouterr().out


@pytest.mark.usefixtures("process_loop_commands_isolated")
def test_process_loop_worktree_option_reaches_public_handler(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """公開CLIのworktree指定がprocess-loopハンドラへ伝わること。"""
    _setup_notes(tmp_path)
    received: list[str | None] = []

    def fake_process_loop(args: Any, _private_notes: pathlib.Path) -> NoReturn:
        received.append(args.worktree)
        raise SystemExit(0)

    monkeypatch.setattr(_process_loop, "cmd_process_loop", fake_process_loop)
    for extra_argv, expected in (([], None), (["--worktree"], "process-loop"), (["--worktree=custom"], "custom")):
        with pytest.raises(SystemExit) as exc_info:
            atk.main(["wi", "process-loop", *extra_argv], home=tmp_path)
        assert exc_info.value.code == 0
        assert received[-1] == expected


@pytest.mark.usefixtures("process_loop_commands_isolated")
@pytest.mark.parametrize("value", ["/tmp/worktree", "name/../other", "", ".hidden", "-leading"])
def test_process_loop_rejects_invalid_worktree_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    value: str,
) -> None:
    """公開CLIが配置先を逸脱するworktree名を拒否すること。"""
    called = False

    def fake_process_loop(_args: Any, _private_notes: pathlib.Path) -> NoReturn:
        nonlocal called
        called = True
        raise SystemExit(0)

    monkeypatch.setattr(_process_loop, "cmd_process_loop", fake_process_loop)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "process-loop", f"--worktree={value}"], home=tmp_path)
    assert exc_info.value.code == 2
    assert not called


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestProcessLoopWorktreeOption:
    """公開CLIのworktree指定とセッションの実行先を検証する。"""

    @pytest.mark.parametrize(
        ("worktree_argv", "expected_name"),
        [(["--worktree"], "process-loop"), (["--worktree=custom"], "custom")],
    )
    def test_non_dotfiles_worktree_option_changes_session_cwd_and_prompt(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        worktree_argv: list[str],
        expected_name: str,
    ) -> None:
        """非dotfiles対象でもworktree指定が同期先・cwd・公開先文へ反映されること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "myrepo"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))

        def stop_wait(*_args: object, **_kwargs: object) -> NoReturn:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)
        worktree_path = myrepo / ".claude" / "worktrees" / expected_name
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync(local_path: pathlib.Path, worktree_name: str) -> pathlib.Path:
            sync_calls.append((local_path, worktree_name))
            return worktree_path

        monkeypatch.setattr(_pl_worktree, "_sync_worktree_with_upstream", fake_sync)

        with pytest.raises(SystemExit) as exc_info:
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts", *worktree_argv],
                home=tmp_path,
            )

        assert exc_info.value.code == 0
        assert sync_calls == [(myrepo, expected_name)]
        assert len(claude_calls) == 1
        assert claude_calls[0]["cwd"] == worktree_path
        assert "現在のHEADを`origin/main`へ反映" not in claude_calls[0]["cmd"][-1]
        assert "--worktree=" not in " ".join(claude_calls[0]["cmd"])

    def test_dotfiles_worktree_name_can_be_overridden(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """dotfilesの自動worktree名が明示指定だけで上書きされること。"""
        _setup_notes(tmp_path)
        myrepo = tmp_path / "dotfiles"
        myrepo.mkdir()
        claude_calls: list[dict[str, Any]] = []
        base_fake_run = fake_run_with_remote_url(myrepo, claude_calls, 0)

        def fake_run(cmd: list[str], *_args: object, **kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd == ["git", "-C", str(myrepo), "remote", "get-url", "origin"]:
                empty: Any = (
                    "https://github.com/ak110/dotfiles.git\n"
                    if kwargs.get("text")
                    else b"https://github.com/ak110/dotfiles.git\n"
                )
                return subprocess.CompletedProcess(cmd, 0, empty, "" if kwargs.get("text") else b"")
            return base_fake_run(cmd, *_args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fake_run)
        counts = iter((1, 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: next(counts))
        monkeypatch.setattr(_pl_watch, "wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))
        worktree_path = myrepo / ".claude" / "worktrees" / "custom"
        sync_calls: list[tuple[pathlib.Path, str]] = []

        def fake_sync(local_path: pathlib.Path, worktree_name: str) -> pathlib.Path:
            sync_calls.append((local_path, worktree_name))
            return worktree_path

        monkeypatch.setattr(
            _pl_worktree,
            "_sync_worktree_with_upstream",
            fake_sync,
        )

        with pytest.raises(SystemExit):
            atk.main(
                ["wi", "process-loop", f"--target-repo={myrepo}", "--no-update", "--no-alerts", "--worktree=custom"],
                home=tmp_path,
            )

        assert sync_calls == [(myrepo, "custom")]
        assert claude_calls[0]["cwd"] == worktree_path
        assert "現在のHEADを`origin/master`へ反映" not in claude_calls[0]["cmd"][-1]


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestWorktreeWriterGate:
    """実装セッション起動前のclean判定と上流追随のfail-closed契約を検証する。"""

    def test_missing_worktree_is_created_from_upstream(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ) -> None:
        """未作成のworktreeを専用ブランチと上流ブランチから作成する。"""
        local_path = tmp_path / "repo"
        local_path.mkdir()
        worktree_path = local_path / ".claude" / "worktrees" / "process-loop"
        calls: list[list[str]] = []

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str:
            del cwd
            return "origin" if args == ["remote"] else "origin/main"

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)
        monkeypatch.setattr(_pl_worktree, "_worktree_is_clean", lambda path: path == worktree_path)

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            if cmd[1:3] == ["worktree", "add"]:
                worktree_path.mkdir(parents=True)
            returncode = 1 if "show-ref" in cmd else 0
            return subprocess.CompletedProcess(cmd, returncode, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop") == worktree_path  # pylint: disable=protected-access  # noqa: SLF001
        assert ["git", "fetch", "origin"] in calls
        assert [
            "git",
            "worktree",
            "add",
            "-b",
            "worktree-process-loop",
            str(worktree_path),
            "origin/main",
        ] in calls
        assert ["git", "rebase", "origin/main"] in calls

    @pytest.mark.parametrize("dirty_command", ["diff", "cached", "untracked"])
    def test_worktree_is_clean_rejects_each_dirty_kind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        dirty_command: str,
    ) -> None:
        """unstaged・staged・未追跡の各差分を個別に拒否する。"""

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            if dirty_command == "diff" and cmd == ["git", "diff", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            if dirty_command == "cached" and cmd == ["git", "diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(cmd, 1, "", "")
            stdout = "new.txt\n" if dirty_command == "untracked" and "ls-files" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert not _pl_worktree._worktree_is_clean(tmp_path)  # pylint: disable=protected-access  # noqa: SLF001

    @pytest.mark.parametrize("failed_step", ["fetch", "rebase"])
    def test_sync_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        failed_step: str,
    ) -> None:
        """fetchまたはrebase失敗時は実装セッションを起動可能と判定しない。"""
        local_path = tmp_path / "repo"
        (local_path / ".claude" / "worktrees" / "process-loop").mkdir(parents=True)
        worktree_path = local_path / ".claude" / "worktrees" / "process-loop"
        monkeypatch.setattr(_pl_worktree, "_worktree_is_clean", lambda _path: True)
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _path: True)

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str:
            del cwd
            if args == ["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"]:
                return ""
            if args == ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"]:
                return "origin/main"
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--git-common-dir"]:
                return str(local_path / ".git")
            if args == ["rev-parse", "--show-toplevel"]:
                return str(worktree_path)
            if args == ["symbolic-ref", "--short", "HEAD"]:
                return "worktree-process-loop"
            raise AssertionError(args)

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            command = cmd[1] if len(cmd) > 1 else ""
            failed = command == failed_step
            return subprocess.CompletedProcess(cmd, 1 if failed else 0, "", "failure" if failed else "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop") is None  # pylint: disable=protected-access  # noqa: SLF001

    def test_title_set_at_start_and_after_runs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """process-loop開始時にタイトルを設定し、claude起動・update-dotfiles実行の直後にも再設定すること。"""
        myrepo = tmp_path / "repo"
        myrepo.mkdir()
        _setup_notes(tmp_path)
        entered: list[str] = []
        title_calls: list[str] = []
        monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, [], 0))
        monkeypatch.setattr(_wi_readiness, "count_pending_entries", lambda *_a, **_kw: 1)

        @contextlib.contextmanager
        def fake_console_title(title: str) -> Generator[None]:
            entered.append(title)
            yield

        monkeypatch.setattr(_process_loop._console_title, "console_title", fake_console_title)  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(_process_loop._console_title, "set_console_title", title_calls.append)  # pylint: disable=protected-access  # noqa: SLF001
        monkeypatch.setattr(os, "execv", raise_system_exit_0)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", "--target-repo", str(myrepo)], home=tmp_path)
        assert entered == ["atk wi process-loop"]
        # 処理前更新・可用性判定・claude起動の各subprocess.runの直後に1回ずつ続く。
        assert title_calls == ["atk wi process-loop"] * 3
