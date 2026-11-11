# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""公開CLIからのworktree作成、除外設定と既存資源の確認を検証する。"""

import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._common import codex_models
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.process_loop_support import set_orchestrate_model
from agent_toolkit._testing.process_loop_worktree_support import _make_remote_repository, _run_public_process_loop
from agent_toolkit._testing.process_loop_worktree_support import (
    _prepare_process_loop_environment as _prepare_process_loop_environment,
)
from agent_toolkit._testing.process_loop_worktree_support import (
    _process_loop_commands_isolated as _process_loop_commands_isolated,
)
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.usefixtures("process_loop_commands_isolated")
class TestPublicWorktreePreparation:
    """公開CLIからのworktree準備と失敗時の停止条件を検証する。"""

    def test_codex_family_resolution_does_not_start_app_server(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """系列モデルを指定しても、起動前のモデル一覧は共通fixtureが隔離する。"""

        async def forbidden_catalog() -> list[dict[str, Any]]:
            pytest.fail("公開worktree試験から実Codex App Serverを起動しないこと")

        monkeypatch.setattr(codex_models, "list_models_from_app_server", forbidden_catalog)
        _setup_notes(tmp_path)
        set_orchestrate_model(tmp_path, "codex:sol/medium")
        local = _make_remote_repository(tmp_path, "target")
        sessions, _git_calls = _run_public_process_loop(monkeypatch, tmp_path, local, ["--worktree=custom"])
        assert len(sessions) == 1
        assert sessions[0]["cmd"][0] == "codex"
        assert sessions[0]["worktree"] == local / ".claude/worktrees/custom"

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
        assert session_calls[0]["worktree"] == worktree_path
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
        assert second_session_calls[0]["worktree"] == worktree_path
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
            if cmd == ["git", "symbolic-ref", "refs/remotes/origin/HEAD"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="refs/remotes/origin/main", stderr="")
            if cmd == ["git", "rev-parse", "--git-common-dir"]:
                if failure_kind == "empty":
                    return empty
                common = tmp_path / "other.git" if failure_kind == "common-dir" and cwd == worktree_path else local_common
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=str(common), stderr="")
            if cmd == ["git", "rev-parse", "--show-toplevel"]:
                top = local_path if failure_kind == "show-toplevel" else worktree_path
                return subprocess.CompletedProcess(cmd, returncode=0, stdout=str(top), stderr="")
            if cmd == ["git", "symbolic-ref", "HEAD"]:
                branch = "refs/heads/main" if failure_kind == "branch" else "refs/heads/worktree-custom"
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
        capsys: pytest.CaptureFixture[str],
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
        assert "既存ブランチのworktree登録を確認できない" in capsys.readouterr().err
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
        session_calls: list[dict[str, Any]] = []

        def run_once() -> None:
            sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local_path, ["--worktree=custom"])
            session_calls.extend(sessions)

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
