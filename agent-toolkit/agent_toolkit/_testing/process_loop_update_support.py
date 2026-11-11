"""process_loop_updateの試験で共有する準備処理と隔離fixture。"""

import argparse
import pathlib
import shutil
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import config as _config  # noqa: E402  # pylint: disable=wrong-import-position
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_env as _pl_env
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as _pl_session
from agent_toolkit._atk.wi import process_loop_update as _pl_update
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.managed_temp_support import setattr_in_managed_temp_modules
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands


@pytest.fixture(autouse=True)
def _resolve_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・managed-tempの登録簿をユーザー環境から分離する。"""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")
    monkeypatch.setattr(_config.platformdirs, "user_config_dir", lambda _name, **_kwargs: str(tmp_path / "config"))
    setattr_in_managed_temp_modules(monkeypatch, "_state_root_path", lambda: tmp_path / "managed-temp-state")
    monkeypatch.setattr(shutil, "which", lambda command: f"/resolved/{command}")
    monkeypatch.setattr(_pl_watch, "_submission_active", lambda _targets: False)
    monkeypatch.delenv(_RESTART_SPEC_ENV, raising=False)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))


@pytest.fixture(autouse=True)
def _registered_update_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """更新試験は取得と統括を隔離し、更新・再起動と対話CLI構築を実行する。"""
    repository = _config.ConfiguredRepository("github.com/example/myrepo", tmp_path / "repo", tmp_path / "repo/.git")
    monkeypatch.setattr(_config, "resolve_configured_repositories", lambda: (repository,))
    monkeypatch.setattr(ownership, "resume_repositories", lambda _session, targets: set(targets))
    monkeypatch.setattr(_pl_watch, "wait_for_start_permission", lambda *_args: True)
    monkeypatch.setattr(_pl_session, "select_available_orchestrator", lambda specs, *_args, **_kwargs: (*specs[0], None, None))

    def snapshot(notes: pathlib.Path, _repositories: object = None) -> tuple[listing.RepositorySnapshot, ...]:
        count = _wi_readiness.count_pending_entries(notes, repository.repo_id)
        entries = tuple((notes / f"item-{index}.md", repository.repo_id, "# 要求", "inbox", "awi") for index in range(count))
        ready = _wi_readiness.ReadinessResult(ready=tuple(entry[0].name for entry in entries))
        return (listing.RepositorySnapshot(repository, entries, ready, notes),)

    def run_cli(argv: list[str], env: dict[str, str], cwd: pathlib.Path, **_kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.run(argv, env=env, cwd=cwd, check=False)

    def coordinate(
        args: argparse.Namespace, snapshots: tuple[listing.RepositorySnapshot, ...], **_kwargs: Any
    ) -> dict[str, dict[str, str]]:
        engine, model, effort = _pl_session.resolve_orchestrator_specs()[0]
        _pl_session.run_process_session(
            args,
            pathlib.Path.cwd(),
            "goal",
            _pl_env.child_env(),
            orchestrator=engine,
            model=model,
            effort=effort,
            resume_pending=False,
        )
        return {snapshot.repository.repo_id: {"status": "completed"} for snapshot in snapshots}

    monkeypatch.setattr(listing, "configured_repo_snapshot", snapshot)
    monkeypatch.setattr(_pl_session, "_run_cli_session", run_cli)
    monkeypatch.setattr(loop, "run_coordinator", coordinate)


@pytest.fixture(name="process_loop_commands_isolated")
def _process_loop_commands_isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。常駐ループ全体を動かすテストが使う。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


def _command_was_called(calls: list[list[str]], command: str) -> bool:
    """呼び出し配列の先頭要素の基底名が一致するか確認する。"""
    return any(pathlib.Path(call[0]).stem.lower() == command for call in calls)


_has_upstream_diff = _pl_update._has_upstream_diff  # pylint: disable=protected-access


_restart_process_loop = _pl_update.restart_process_loop  # pylint: disable=protected-access


_RESTART_SPEC_ENV = _pl_env.RESTART_SPEC_ENV  # pylint: disable=protected-access


_RESTART_EXIT_CODE = _pl_update._RESTART_EXIT_CODE  # pylint: disable=protected-access


_INTERNAL_MISE_REFRESHED_ARG = _pl_update._INTERNAL_MISE_REFRESHED_ARG  # pylint: disable=protected-access


_INTERNAL_DOTFILES_UPDATED_ARG = _pl_update._INTERNAL_DOTFILES_UPDATED_ARG  # pylint: disable=protected-access


_RESUME_ARGV_CASES = [
    ["--resume", "00000000-0000-0000-0000-000000000000"],
    ["--resume=00000000-0000-0000-0000-000000000000"],
]
