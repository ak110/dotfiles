# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""専用worktreeの上流追随と起動前のclean判定を検証する。"""

import contextlib
import pathlib
import subprocess
from collections.abc import Generator
from typing import Any

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._git import worktrees as _git_worktrees
from agent_toolkit._testing.process_loop_worktree_support import (
    _prepare_process_loop_environment as _prepare_process_loop_environment,
)
from agent_toolkit._testing.process_loop_worktree_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


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
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _local_path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:2] == ["symbolic-ref"]:
                stdout = "refs/remotes/origin/master"
            elif cmd[1:2] == ["remote"]:
                stdout = "origin\n"
            else:
                stdout = ""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "fetch", "origin"] in calls
        assert ["git", "-c", "submodule.recurse=false", "rebase", "refs/remotes/origin/master"] in calls
        assert worktree.is_dir()

    def test_tracking_branch_has_priority_without_origin_head(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """origin/HEADが無くても現在ブランチの追跡先を使って追随する。"""
        worktree = self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _local_path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str | None:
            del cwd
            if args == ["rev-parse", "--symbolic-full-name", "@{u}"]:
                return "refs/remotes/origin/feature"
            if args == ["remote"]:
                return "origin"
            if args == ["symbolic-ref", "refs/remotes/origin/HEAD"]:
                raise AssertionError("追跡先が解決できた場合はorigin/HEADを使わないこと")
            return None

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "fetch", "origin"] in calls
        assert ["git", "-c", "submodule.recurse=false", "rebase", "refs/remotes/origin/feature"] in calls

    def test_fetches_tracking_remote_before_rebase(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """origin以外の追跡remoteをfetchし、同じ追跡先へrebaseする。"""
        worktree = self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _local_path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str | None:
            del cwd
            if args == ["rev-parse", "--symbolic-full-name", "@{u}"]:
                return "refs/remotes/upstream/feature"
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
        assert ["git", "-c", "submodule.recurse=false", "rebase", "refs/remotes/upstream/feature"] in calls

    def test_reuses_existing_branch_when_worktree_absent(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """worktree未作成でも専用ブランチがあれば同ブランチから作成する。"""
        local_path = tmp_path / "repo"
        worktree = local_path / ".claude" / "worktrees" / "process-loop"
        calls: list[list[str]] = []

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str:
            del cwd
            return "origin" if args == ["remote"] else "refs/remotes/origin/master"

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)
        monkeypatch.setattr(_pl_worktree, "_worktree_is_clean", lambda path: path == worktree)
        # 登録の列挙はGitの保存形式を読む共通処理の契約で置き換える。列挙の実Gitでの動作はworktrees_test.pyが確かめる。
        registered = _git_worktrees.Worktree(
            path=pathlib.Path("/existing/worktree"),
            branch="worktree-process-loop",
            head_ref="refs/heads/worktree-process-loop",
            detached=False,
            bare=False,
            locked=False,
            prunable=False,
        )
        monkeypatch.setattr(_git_worktrees, "list_worktrees", lambda repo, **_kwargs: [registered])

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:3] == ["worktree", "add"]:
                worktree.mkdir(parents=True)
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == worktree
        assert ["git", "worktree", "add", str(worktree), "worktree-process-loop"] in calls
        assert ["git", "-c", "submodule.recurse=false", "rebase", "refs/remotes/origin/master"] in calls

    def test_aborts_rebase_and_warns_on_conflict(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rebase失敗時は中断したうえで警告を発し、実装セッションを起動しない。"""
        self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _local_path: True)
        monkeypatch.setattr(_pl_worktree, "_validate_existing_worktree", lambda *_args: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:2] == ["symbolic-ref"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="refs/remotes/origin/master", stderr="")
            if cmd[1:2] == ["remote"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="origin\n", stderr="")
            if cmd[1:5] == ["-c", "submodule.recurse=false", "rebase", "refs/remotes/origin/master"]:
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="conflict")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _pl_worktree._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "-c", "submodule.recurse=false", "rebase", "--abort"] in calls
        stderr = capsys.readouterr().err
        assert "追随に失敗したため実装セッションを起動しません" in stderr
        # 手作業で競合を解消するrebaseのコマンドを次の操作として続ける。
        assert "rebase refs/remotes/origin/master`を手作業で実行" in stderr.split("\n次の操作: ", 1)[1]

    def test_unresolved_upstream_guides_upstream_setting(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """上流ブランチを解決できない場合は、上流の設定コマンドを次の操作として返し実装セッションを起動しない。"""
        self._make_worktree(tmp_path)
        local_path = tmp_path / "repo"
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _local_path: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            if cmd[1:2] in (["rev-parse"], ["symbolic-ref"]):
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="no upstream")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        assert _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop") is None  # pylint: disable=protected-access  # noqa: SLF001

        stderr = capsys.readouterr().err
        assert "上流ブランチを解決できないため実装セッションを起動しません" in stderr
        assert f"`git -C {local_path} branch -u <remote>/<branch>`" in stderr.split("\n次の操作: ", 1)[1]


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
            return "origin" if args == ["remote"] else "refs/remotes/origin/main"

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
            "refs/remotes/origin/main",
        ] in calls
        assert ["git", "-c", "submodule.recurse=false", "rebase", "refs/remotes/origin/main"] in calls

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
        monkeypatch.setattr(_pl_worktree, "_worktree_is_clean", lambda _worktree_path: True)
        monkeypatch.setattr(_pl_worktree, "_ensure_worktree_excluded", lambda _local_path: True)
        failures: list[str] = []

        def fake_git_output(args: list[str], cwd: pathlib.Path) -> str:
            del cwd
            if args == ["rev-parse", "--symbolic-full-name", "@{u}"]:
                return ""
            if args == ["symbolic-ref", "refs/remotes/origin/HEAD"]:
                return "refs/remotes/origin/main"
            if args == ["remote"]:
                return "origin"
            if args == ["rev-parse", "--git-common-dir"]:
                return str(local_path / ".git")
            if args == ["rev-parse", "--show-toplevel"]:
                return str(worktree_path)
            if args == ["symbolic-ref", "HEAD"]:
                return "refs/heads/worktree-process-loop"
            raise AssertionError(args)

        monkeypatch.setattr(_pl_worktree, "_git_output", fake_git_output)

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            arguments = cmd[1:]
            if arguments[:2] == ["-c", "submodule.recurse=false"]:
                arguments = arguments[2:]
            command = arguments[0] if arguments else ""
            failed = command == failed_step
            if failed:
                failures.append(command)
            return subprocess.CompletedProcess(cmd, 1 if failed else 0, "", "failure" if failed else "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert _pl_worktree._sync_worktree_with_upstream(local_path, "process-loop") is None  # pylint: disable=protected-access  # noqa: SLF001
        assert failed_step in failures

    def test_title_set_at_start_and_after_runs(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """登録が空の待機でも、統括ループ全体にprocess-loopのタイトルを適用する。"""
        _setup_notes(tmp_path)
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", "[]")
        entered: list[str] = []

        @contextlib.contextmanager
        def fake_console_title(title: str) -> Generator[None]:
            entered.append(title)
            yield

        monkeypatch.setattr(_process_loop._console_title, "console_title", fake_console_title)  # pylint: disable=protected-access  # noqa: SLF001

        def stop_wait(*_args: object, **_kwargs: object) -> bool:
            raise KeyboardInterrupt

        monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)
        with pytest.raises(SystemExit):
            atk.main(["wi", "process-loop", "--no-update", "--no-alerts"], home=tmp_path)
        assert entered == ["atk wi process-loop"]
