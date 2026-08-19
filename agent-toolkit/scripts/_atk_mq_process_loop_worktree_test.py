"""`atk mq process-loop`のworktree追随処理のテスト。

本体テストの`_atk_mq_process_loop_test.py`が行数上限に達したため、
worktree追随という責務の境界で分割した。
"""

import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _atk_mq_process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
import atk  # noqa: E402  # pylint: disable=wrong-import-position
from atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.fixture(autouse=True)
def _prepare_process_loop_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """公開CLIテストの外部コマンド解決とprivate-notes同期を隔離する。"""
    monkeypatch.setattr(_process_loop.shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_process_loop, "_pull_private_notes", lambda _path: True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))


def _run_git(args: list[str], cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    """テスト用Gitコマンドを実行する。"""
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def _make_remote_repository(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """mainブランチを持つローカルoriginとcloneを生成する。"""
    remote = tmp_path / f"{name}-origin.git"
    seed = tmp_path / f"{name}-seed"
    local = tmp_path / name
    _run_git(["init", "--bare", "--initial-branch=main", str(remote)], tmp_path)
    _run_git(["init", "--initial-branch=main", str(seed)], tmp_path)
    _run_git(["config", "user.name", "test"], seed)
    _run_git(["config", "user.email", "test@example.invalid"], seed)
    (seed / "state.txt").write_text("base\n", encoding="utf-8")
    _run_git(["add", "state.txt"], seed)
    _run_git(["commit", "-m", "base"], seed)
    _run_git(["remote", "add", "origin", str(remote)], seed)
    _run_git(["push", "-u", "origin", "main"], seed)
    _run_git(["clone", str(remote), str(local)], tmp_path)
    _run_git(["config", "user.name", "test"], local)
    _run_git(["config", "user.email", "test@example.invalid"], local)
    _run_git(["remote", "set-head", "origin", "-a"], local)
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
    monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: "github.com/example/repo")
    counts = iter((1, 0))
    monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_args, **_kwargs: next(counts))

    def stop_wait(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_process_loop, "_wait_for_changes", stop_wait)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "mq",
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
        monkeypatch.setattr(_process_loop, "_ensure_worktree_excluded", lambda _path: True)
        monkeypatch.setattr(_process_loop, "_validate_existing_worktree", lambda *_args: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            stdout = "origin/master" if cmd[1:2] == ["symbolic-ref"] else ""
            return subprocess.CompletedProcess(cmd, returncode=0, stdout=stdout, stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _process_loop._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "fetch", "origin"] in calls
        assert ["git", "rebase", "origin/master"] in calls
        assert worktree.is_dir()

    def test_reuses_existing_branch_when_worktree_absent(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """worktree未作成でも専用ブランチがあれば同ブランチから作成する。"""
        local_path = tmp_path / "repo"
        worktree = local_path / ".claude" / "worktrees" / "process-loop"
        calls: list[list[str]] = []
        monkeypatch.setattr(_process_loop, "_git_output", lambda *_args, **_kwargs: "origin/master")
        monkeypatch.setattr(_process_loop, "_worktree_is_clean", lambda path: path == worktree)

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
        result = _process_loop._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert result == (worktree, "origin/master")
        assert ["git", "worktree", "add", str(worktree), "worktree-process-loop"] in calls
        assert ["git", "rebase", "origin/master"] in calls

    def test_existing_unregistered_branch_is_not_recreated_or_rebased(self, tmp_path: pathlib.Path) -> None:
        """worktree未登録の既存ブランチは再作成・rebaseせずOIDを維持する。"""
        origin = tmp_path / "origin.git"
        seed = tmp_path / "seed"
        local_path = tmp_path / "repo"

        def run_git(args: list[str], cwd: pathlib.Path = tmp_path) -> subprocess.CompletedProcess[str]:
            return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)

        run_git(["init", "--bare", "--initial-branch=master", str(origin)])
        run_git(["init", "--initial-branch=master", str(seed)])
        run_git(["config", "user.name", "test"], cwd=seed)
        run_git(["config", "user.email", "test@example.com"], cwd=seed)
        (seed / "state.txt").write_text("base\n", encoding="utf-8")
        run_git(["add", "state.txt"], cwd=seed)
        run_git(["commit", "-m", "base"], cwd=seed)
        run_git(["remote", "add", "origin", str(origin)], cwd=seed)
        run_git(["push", "-u", "origin", "master"], cwd=seed)
        run_git(["clone", str(origin), str(local_path)])
        run_git(["branch", "worktree-process-loop"], cwd=local_path)
        before = run_git(["rev-parse", "refs/heads/worktree-process-loop"], cwd=local_path).stdout.strip()

        (seed / "state.txt").write_text("upstream\n", encoding="utf-8")
        run_git(["commit", "-am", "upstream"], cwd=seed)
        run_git(["push", "origin", "master"], cwd=seed)

        worktree = local_path / ".claude" / "worktrees" / "process-loop"
        result = _process_loop._sync_worktree_with_upstream(local_path, "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        after = run_git(["rev-parse", "refs/heads/worktree-process-loop"], cwd=local_path).stdout.strip()
        assert result is None
        assert before == after
        assert not worktree.exists()

    def test_aborts_rebase_and_warns_on_conflict(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """rebase失敗時は中断したうえで警告を発し、実装セッションを起動しない。"""
        self._make_worktree(tmp_path)
        calls: list[list[str]] = []
        monkeypatch.setattr(_process_loop, "_ensure_worktree_excluded", lambda _path: True)
        monkeypatch.setattr(_process_loop, "_validate_existing_worktree", lambda *_args: True)

        def fake_run(cmd: list[str], *_args: object, **_kwargs: object) -> subprocess.CompletedProcess[Any]:
            calls.append(list(cmd))
            if cmd[1:2] == ["symbolic-ref"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="origin/master", stderr="")
            if cmd[1:3] == ["rebase", "origin/master"]:
                return subprocess.CompletedProcess(cmd, returncode=1, stdout="", stderr="conflict")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)
        _process_loop._sync_worktree_with_upstream(tmp_path / "repo", "process-loop")  # pylint: disable=protected-access  # noqa: SLF001

        assert ["git", "rebase", "--abort"] in calls
        assert "追随に失敗したため実装セッションを起動しません" in capsys.readouterr().err


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
        assert "現在のHEADを`origin/main`へ反映" in session_calls[0]["cmd"][-1]
        assert exclude_path.read_text(encoding="utf-8") == "# existing\n/.claude/worktrees/\n"
        assert exclude_path.read_text(encoding="utf-8").splitlines().count("/.claude/worktrees/") == 1
        check_ignore = subprocess.run(
            ["git", "check-ignore", "-q", ".claude/worktrees/"],
            cwd=local_path,
            check=False,
        )
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
        _run_git(["branch", "worktree-process-loop"], local_path)
        before = _run_git(["rev-parse", "refs/heads/worktree-process-loop"], local_path).stdout.strip()

        session_calls, git_calls = _run_public_process_loop(
            monkeypatch,
            tmp_path,
            local_path,
            ["--worktree"],
        )

        after = _run_git(["rev-parse", "refs/heads/worktree-process-loop"], local_path).stdout.strip()
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
        _run_git(["add", ".gitignore"], local_path)
        _run_git(["commit", "-m", "negate worktree ignore"], local_path)
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
        monkeypatch.setattr(_process_loop, "_resolve_repo_id", lambda *_args, **_kwargs: "github.com/example/repo")
        monkeypatch.setattr(_process_loop, "_wait_for_changes", lambda *_a, **_kw: (_ for _ in ()).throw(KeyboardInterrupt))

        def run_once() -> None:
            counts = iter((1,))
            monkeypatch.setattr(_process_loop, "_count_pending_entries", lambda *_a, **_kw: next(counts))
            with pytest.raises(SystemExit) as exc_info:
                atk.main(
                    [
                        "mq",
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
