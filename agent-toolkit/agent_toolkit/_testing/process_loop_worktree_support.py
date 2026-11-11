"""process_loop_worktreeの試験で共有する準備処理と隔離fixture。"""

import json
import pathlib
import shutil
import subprocess
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk import config
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes  # noqa: E402  # pylint: disable=wrong-import-position


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
        manifest = pathlib.Path(json.loads(child_env["AGENT_TOOLKIT_SESSION_START_INPUT"])["manifest"])
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
        lambda candidates, _env, _cwd, **_kwargs: (*candidates[0], None, None),
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


@pytest.fixture(name="process_loop_commands_isolated")
def _process_loop_commands_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。常駐ループ全体を動かすテストが使う。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


_PULL_PRIVATE_NOTES_IMPL = _pl_watch.pull_private_notes  # pylint: disable=protected-access  # noqa: SLF001
