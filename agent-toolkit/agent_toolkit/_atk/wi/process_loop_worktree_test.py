"""`atk wi process-loop`のセッションを起動する作業ツリーの準備と上流との同期のテスト。"""

import contextlib
import json
import os
import pathlib
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Generator
from typing import Any, NoReturn

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import config, managed_temp
from agent_toolkit._atk import git_sync as _atk_git_sync
from agent_toolkit._atk.wi import process_loop as _process_loop  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import process_loop_worktree as _pl_worktree
from agent_toolkit._atk.wi import sync as _wi_sync
from agent_toolkit._common import codex_models, state_paths
from agent_toolkit._git import worktrees as _git_worktrees
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.process_loop_support import (
    isolate_process_loop_commands,
    set_orchestrate_model,
)
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("name", ["fixed", None])
@pytest.mark.parametrize("detached", [False, True])
def test_project_hook(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], fails: bool, name: str | None, detached: bool
) -> None:
    """公開されたworktree準備処理がWorktreeCreate hookの返したパスを再利用し、hookが失敗してもGitによる作成へ切り替えない。"""
    local = _make_remote_repository(tmp_path, "hook-target")
    destination = tmp_path / "project-worktree"
    count = tmp_path / "calls"
    script = tmp_path / "create-hook.py"
    script.write_text(
        "import json,pathlib,subprocess,sys\n"
        "p=json.load(sys.stdin)\n"
        "assert p['name']==sys.argv[5] and p['cwd']==sys.argv[1]\n"
        "pathlib.Path(sys.argv[3]).write_text('called')\n"
        "if sys.argv[4]=='fail': raise SystemExit(3)\n"
        "mode=['--detach'] if sys.argv[6]=='detached' else ['-b','project-created']\n"
        "subprocess.run(['git','worktree','add',*mode,sys.argv[2]],check=True,stdout=sys.stderr)\n"
        "print(sys.argv[2])\n",
        encoding="utf-8",
    )
    argv = [
        sys.executable,
        str(script),
        str(local),
        str(destination),
        str(count),
        "fail" if fails else "success",
        name or "process-loop",
        "detached" if detached else "branch",
    ]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = local / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}), encoding="utf-8"
    )
    first = _pl_worktree.prepare_session_target(local, "example/repo", "input", worktree_name=name, resume_pending=False)
    assert count.is_file()
    if fails:
        assert first is None and not destination.exists()
        assert not (local / ".claude/worktrees/fixed").exists()
        match = re.search(r"途中資源を保持した: (.+?\.json):", capsys.readouterr().err)
        assert match is not None
        evidence = pathlib.Path(match[1])
        assert evidence.parent.parent == managed_temp.temp_root_path()
        observed = json.loads(evidence.read_text(encoding="utf-8"))
        assert observed["exit_code"] == 3 and observed["input"]["name"] == (name or "process-loop")
    else:
        assert first is not None and first[0] == destination
        stored_paths = list((state_paths.state_dir() / "process-loop-worktrees").glob("*.json"))
        assert len(stored_paths) == 1
        stored = json.loads(stored_paths[0].read_text(encoding="utf-8"))
        diagnostics = pathlib.Path(stored["hook_diagnostics"])
        assert diagnostics.parent == managed_temp.temp_root_path()
        observed = json.loads((diagnostics / "WorktreeCreate-0.json").read_text(encoding="utf-8"))
        assert observed["exit_code"] == 0 and observed["input"]["cwd"] == str(local)
        count.unlink()
        second = _pl_worktree.prepare_session_target(local, "example/repo", "input", worktree_name=name, resume_pending=False)
        assert second is not None and second[0] == destination
        assert not count.exists()


@pytest.mark.parametrize("destination_kind", ["main", "branch", "detached"])
@pytest.mark.parametrize("dirty", [False, True])
def test_project_hook_rejects_preexisting_worktree(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], destination_kind: str, dirty: bool
) -> None:
    """新規hookの返却だけで既存資源を転用せず、branch・HEAD・ユーザー差分を保つ。"""
    local = _make_remote_repository(tmp_path, "hook-existing")
    destination = local if destination_kind == "main" else tmp_path / "other worktree"
    if destination_kind != "main":
        mode = ["--detach"] if destination_kind == "detached" else ["-b", "user-branch"]
        git_repository.run_git(local, "worktree", "add", *mode, str(destination))
    if dirty:
        (destination / "state.txt").write_text("user tracked change\n", encoding="utf-8")
        (destination / "user.txt").write_text("user untracked change\n", encoding="utf-8")
    script = tmp_path / "return-existing.py"
    script.write_text("import sys\nprint(sys.argv[1])\n", encoding="utf-8")
    argv = [sys.executable, str(script), str(destination)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = local / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps({"hooks": {"WorktreeCreate": [{"hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8",
    )

    def snapshot() -> tuple[str, str, str, str, str, str | None]:
        return (
            git_repository.run_git(destination, "symbolic-ref", "--quiet", "HEAD", check=False).stdout,
            git_repository.run_git(destination, "rev-parse", "HEAD").stdout,
            git_repository.run_git(destination, "status", "--porcelain=v1", "--untracked-files=all").stdout,
            git_repository.run_git(local, "for-each-ref", "refs/heads", "--format=%(refname) %(objectname)").stdout,
            (destination / "state.txt").read_text(encoding="utf-8"),
            (destination / "user.txt").read_text(encoding="utf-8") if dirty else None,
        )

    before = snapshot()
    result = _pl_worktree.prepare_session_target(local, "example/repo", "input", worktree_name="fixed", resume_pending=False)
    assert snapshot() == before
    assert result is None
    assert not list((state_paths.state_dir() / "process-loop-worktrees").glob("*.json"))
    assert "既存の作業ツリー" in capsys.readouterr().err


@pytest.fixture(autouse=True)
def _prepare_process_loop_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """公開CLIテストの外部コマンド解決・private-notes同期・managed-tempの登録簿を隔離する。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _private_notes: True)
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: False)
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
    update_flags: tuple[str, ...] = ("--no-update",),
) -> tuple[list[dict[str, Any]], list[list[str]]]:
    """登録対象を公開CLIで準備し、常駐cwd・manifest・Git呼び出しを返す。"""
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
    repository = config.ConfiguredRepository("github.com/example/repo", local_path, local_path / ".git")
    monkeypatch.setattr(config, "resolve_configured_repositories", lambda: (repository,))
    notes = tmp_path / "private-notes"
    entry = notes / "2026/10/worktree.md"
    entry.parent.mkdir(parents=True, exist_ok=True)
    entry.write_text("---\ntype: awi\nstate: inbox\ntarget_repo: github.com/example/repo\n---\n\n# 作業\n", encoding="utf-8")

    def execute(_args: Any, cwd: pathlib.Path, prompt: str, child_env: dict[str, str], **kwargs: Any) -> bool:
        start = prompt.index("は ") + len("は ")
        manifest = pathlib.Path(prompt[start : prompt.index(" を読む", start)])
        data = json.loads(manifest.read_text(encoding="utf-8"))
        target = data["repositories"][0]
        session_calls.append(
            {
                "cmd": [kwargs["orchestrator"], prompt],
                "cwd": cwd,
                "worktree": pathlib.Path(target["worktree"]),
                "target": target,
            }
        )
        assert cwd == pathlib.Path.cwd()
        kwargs["on_session_started"]("worktree-coordinator")
        child_env[_pl_env.PROCESS_LOOP_SESSION_ID_ENV] = "worktree-coordinator"
        entry.unlink()
        pathlib.Path(data["result_file"]).write_text(
            json.dumps({"repositories": {repository.repo_id: {"status": "completed", "writer_sessions": []}}}), encoding="utf-8"
        )
        return True

    monkeypatch.setattr(_pl_session, "run_process_session", execute)
    monkeypatch.setattr(
        _pl_session,
        "select_available_orchestrator",
        lambda candidates, _env, _cwd, **_kwargs: (*candidates[0], None),
    )
    monkeypatch.setattr(_pl_watch, "wait_for_start_permission", lambda *_args: True)
    monkeypatch.setattr(_pl_update, "restart_process_loop", lambda *_args, **_kwargs: None)

    def stop_wait(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(_pl_watch, "wait_for_changes", stop_wait)
    with pytest.raises(SystemExit) as exc_info:
        atk.main(
            [
                "wi",
                "process-loop",
                *update_flags,
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
            lambda _plugin_root: pytest.fail("更新コマンド未解決では再pullしないこと"),
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
        monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _private_notes: True)

        assert _pl_update.update_before_session(  # pylint: disable=protected-access  # noqa: SLF001
            tmp_path,
            None,
            None,
            ["atk"],
            {},
        ) == (True, False)
        assert "exit code 1" in capsys.readouterr().err

    def test_registered_start_updates_before_worktree_and_session(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """登録全体の開始前更新がworktree準備と統括起動に先行する。"""
        local = _make_remote_repository(tmp_path, "target")
        events: list[str] = []
        prepare = _pl_worktree.prepare_session_target

        def update(*_args: Any, **_kwargs: Any) -> tuple[bool, bool]:
            events.append("update")
            return True, True

        def preparing(*args: Any, **kwargs: Any) -> Any:
            events.append("prepare")
            return prepare(*args, **kwargs)

        monkeypatch.setattr(_pl_update, "update_before_session", update)
        monkeypatch.setattr(_pl_worktree, "prepare_session_target", preparing)
        sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local, ["--worktree=custom"], update_flags=())
        assert events == ["update", "prepare"] and len(sessions) == 1

    @pytest.mark.parametrize(
        ("relative_path", "restarts"),
        [
            ("agent_toolkit/runtime.py", True),
            ("scripts/launch.py", True),
            ("agent_toolkit/runtime_test.py", False),
            ("agent_toolkit/__pycache__/runtime.py", False),
        ],
    )
    def test_before_session_update_detects_real_runtime_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, relative_path: str, restarts: bool
    ) -> None:
        """開始前更新は実走査対象の変更だけで再起動する。"""
        plugin_root = tmp_path / "dotfiles" / "agent-toolkit"
        package = plugin_root / "agent_toolkit"
        scripts = plugin_root / "scripts"
        package.mkdir(parents=True)
        scripts.mkdir()
        (package / "runtime.py").write_text("before = 1\n", encoding="utf-8")
        (package / "runtime_test.py").write_text("before = 1\n", encoding="utf-8")
        (scripts / "launch.py").write_text("before = 1\n", encoding="utf-8")
        startup_hash = _pl_update.code_hash(plugin_root)
        monkeypatch.setattr(_pl_env, "resolve_executable", lambda _name: "update-dotfiles")
        monkeypatch.setattr(_pl_watch, "pull_private_notes", lambda _private_notes: True)
        restart_spec = tmp_path / "restart-spec"
        monkeypatch.setenv("AGENT_TOOLKIT_RESTART_SPEC", str(restart_spec))

        def fake_run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            path = plugin_root / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("after = 2\n", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)
        if restarts:
            with pytest.raises(SystemExit) as restart:
                _pl_update.update_before_session(
                    tmp_path, tmp_path / "dotfiles", startup_hash, ["atk", "wi", "process-loop"], {}
                )
            expected = _pl_update._RESTART_EXIT_CODE  # pylint: disable=protected-access  # noqa: SLF001
            assert restart.value.code == expected
        else:
            result = _pl_update.update_before_session(
                tmp_path, tmp_path / "dotfiles", startup_hash, ["atk", "wi", "process-loop"], {}
            )
            assert result == (True, True)
            assert not restart_spec.exists()

    def test_registered_updated_marker_skips_duplicate_update(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """開始前更新済みの印を引き継いだ反復は同じ更新を繰り返さない。"""
        local = _make_remote_repository(tmp_path, "target")

        def unexpected(*_args: Any, **_kwargs: Any) -> Any:
            pytest.fail("更新済みの反復でupdate_before_sessionを呼び出した")

        monkeypatch.setattr(_pl_update, "update_before_session", unexpected)
        sessions, _calls = _run_public_process_loop(
            monkeypatch, tmp_path, local, [], update_flags=("--internal-dotfiles-updated",)
        )
        assert len(sessions) == 1

    def test_registered_preparation_failure_returns_to_wait(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """準備失敗時は統括を起動せず、キューと対象作業を保持して待機する。"""
        local = _make_remote_repository(tmp_path, "target")
        monkeypatch.setattr(_pl_worktree, "prepare_session_target", lambda *_args, **_kwargs: None)
        sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local, ["--worktree"])
        assert not sessions
        assert (tmp_path / "private-notes/2026/10/worktree.md").is_file()


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
    """対象worktree名と、常駐cwdで起動する統括への受渡しを確認する。"""

    @pytest.mark.parametrize(("flags", "name"), [(["--worktree"], "process-loop"), (["--worktree=custom"], "custom")])
    def test_registered_worktree_option(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, flags: list[str], name: str
    ) -> None:
        local = _make_remote_repository(tmp_path, "target")
        sessions, _calls = _run_public_process_loop(monkeypatch, tmp_path, local, flags)
        assert sessions[0]["worktree"] == local / ".claude/worktrees" / name
        assert sessions[0]["cwd"] == pathlib.Path.cwd()
        assert sessions[0]["target"]["target_repo"] == "github.com/example/repo"


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
