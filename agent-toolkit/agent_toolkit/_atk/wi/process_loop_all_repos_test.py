"""登録取得・通常delegate・リポジトリ別所有の接続契約を検証する。"""

import argparse
import asyncio
import json
import os
import pathlib
from typing import Any, cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop_all_repos as all_repos
from agent_toolkit._atk.wi import process_loop_log as loop_log
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._atk.wi import process_loop_update as update_module
from agent_toolkit._atk.wi import process_loop_watch as watch
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    FakeClaudeClient,
    ResultMessage,
    SystemMessage,
    install_backend,
    server_manager,
)
from agent_toolkit._testing.agents_server_support import (
    claude_backend as claude,
)
from agent_toolkit.atk_test import _setup_notes


@pytest.mark.parametrize("mode,api", [("pay-as-you-go", True), ("api-credits", False)])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_repository_orchestrator_follows_mode(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    api: bool,
) -> None:
    """all-reposの公開起動からSDKまで、リポジトリ担当をオーケストレーターとして扱う。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "task.md", repositories[0].repo_id)
    path = tmp_path / "api.env"
    path.write_text("ANTHROPIC_API_KEY=selected-key\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-subscription")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_ENV_FILE", str(path))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_CLAUDE_API_MODE", mode)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_ORCHESTRATE_ENGINE", "claude")
    options_seen: list[Any] = []

    def factory(options: Any) -> FakeClaudeClient:
        options_seen.append(options)
        return FakeClaudeClient([[SystemMessage("repository-orchestrator"), AssistantMessage("作業"), ResultMessage("完了")]])

    manager = server_manager.AgentsServerManager()
    backend = claude.ClaudeServerManager(manager.sessions, client_factory=factory)
    install_backend(manager, "claude", backend)
    results = await all_repos.run_repository_delegates(
        argparse.Namespace(worktree=None, resume=None),
        listing.configured_repo_snapshot(notes, repositories),
        manager=manager,
    )
    assert results[repositories[0].repo_id]["status"] == "completed"
    assert bool(options_seen[0].env.get("ANTHROPIC_API_KEY")) is api


def _write(notes: pathlib.Path, name: str, repo_id: str, *, extra: str = "", state: str = "inbox") -> pathlib.Path:
    path = notes / state / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: awi\ntarget_repo: {repo_id}\n{extra}---\n\n# 要求\n", encoding="utf-8")
    return path


def _repositories(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[config.ConfiguredRepository, ...]:
    paths = [
        git.init_repository(tmp_path / name, origin=f"https://example.test/team/{name}.git") for name in ("one", "two", "empty")
    ]
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(path) for path in paths]))
    return config.resolve_configured_repositories()


def test_configured_scope(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """設定CLIのJSON登録を読み、同originをまとめ、空・不正登録を区別する。"""
    one = git.init_repository(tmp_path / "one", origin="https://example.test/team/one.git")
    clone = git.init_repository(tmp_path / "clone", origin="git@example.test:team/one.git")
    two = git.init_repository(tmp_path / "two", origin="https://example.test/team/two.git")
    value = json.dumps([str(one), str(clone), str(two)])
    args = argparse.Namespace(config_subcommand="set", key="process_loop_repositories", value=value)
    assert config.dispatch(args, tmp_path) == 0
    repositories = config.resolve_configured_repositories()
    assert [repository.worktree for repository in repositories] == [one, two]
    args.value = "[]"
    assert config.dispatch(args, tmp_path) == 0
    assert not config.resolve_configured_repositories()
    for invalid in ('{"repo": 1}', "broken", '["relative"]', json.dumps([str(tmp_path / "missing")])):
        monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", invalid)
        with pytest.raises(ValueError, match=".+"):
            config.resolve_configured_repositories()


def test_configured_repo_snapshot(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """キューを1回だけ取得し、リポジトリ別の依存・候補上限・登録外の除外を反映した一覧を渡す。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    one, two, _empty = (repository.repo_id for repository in repositories)
    _write(notes, "root.md", one)
    _write(notes, "internal.md", one, extra="depends_on: [root.md]\n")
    _write(notes, "external.md", one, extra="depends_on: [other.md]\n")
    _write(notes, "other.md", two)
    _write(notes, "unregistered.md", "example.test/team/unregistered")
    snapshots = listing.configured_repo_snapshot(notes, repositories)
    assert snapshots[0].readiness.internal_dependency_waits == ("internal.md",)
    assert "external.md" in snapshots[0].readiness.blocked
    assert [len(snapshot.candidates) for snapshot in snapshots] == [2, 1, 0]
    assert [snapshot.lane_limit for snapshot in snapshots] == [1, 1, 0]
    reads: list[pathlib.Path] = []
    original_read = pathlib.Path.read_text

    def read_text(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        if path.is_relative_to(notes):
            reads.append(path)
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read_text)
    args = argparse.Namespace(
        configured_repos=True,
        skip_pull=True,
        status=["active"],
        type=["all"],
        answered=["all"],
        source=None,
        count=False,
        summary_only=False,
        jsonl=True,
        with_staleness=True,
    )
    listing.cmd_list(args, notes)
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(reads) == 5 and len(set(reads)) == 5
    assert {record["filename"] for record in records} == {"root.md", "internal.md", "external.md", "other.md"}
    external = next(record for record in records if record["filename"] == "external.md")
    assert external["blocked_reason"] == "dependency-unmet-external"
    assert all("staleness" in record for record in records)


def test_processing_candidates_and_lane_limit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """外部依存を待つprocessingの項目を候補に保ち、通常の除外を適用した後の件数からレーン上限を算出する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    one = repositories[0]
    two = repositories[1]
    _write(notes, "external.md", two.repo_id)
    for number in range(11):
        _write(notes, f"processing-{number:02}.md", one.repo_id, state="processing", extra="depends_on: [external.md]\n")
    _write(notes, "blocked-inbox.md", one.repo_id, extra="depends_on: [external.md]\n")
    run_skill = notes / "processing" / "run-skill.md"
    run_skill.write_text(
        f"---\ntype: uwi\ntarget_repo: {one.repo_id}\nsource: run-skill\n---\n\n# 確認\n\n## 回答\n実施する\n",
        encoding="utf-8",
    )
    snapshot = listing.configured_repo_snapshot(notes, repositories)[0]
    assert len(snapshot.candidates) == 11
    assert snapshot.lane_limit == 2
    records = snapshot.candidate_records()
    assert len(records) == 13
    assert all(
        {"filename", "target_repo", "ready", "blocked_reason", "source", "summary", "body", "staleness"} <= record.keys()
        for record in records
    )
    assert (
        next(record for record in records if record["filename"] == "processing-00.md")["blocked_reason"]
        == "dependency-unmet-external"
    )


class _Manager:
    """外部モデルだけを代用し、公開delegate起動と終端の応答を記録する。"""

    def __init__(self, *, fail_repo: str = "") -> None:
        self.started: list[tuple[str, str]] = []
        self.pending: list[dict[str, Any]] = []
        self.fail_repo = fail_repo
        self.closed = False
        self.activated = False

    async def activate_async(self) -> None:
        await asyncio.sleep(0)
        self.activated = True

    async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
        assert self.activated
        assert model_type == "orchestrate" and kwargs["launch_kind"] == "delegate"
        self.started.append((cwd, prompt))
        if pathlib.Path(cwd).name == self.fail_repo:
            raise OSError("外部モデルの起動失敗")
        session_id = f"session-{pathlib.Path(cwd).name}"
        self.pending.append({"session_id": session_id, "status": "completed", "agent_message": "完了"})
        return {"session_id": session_id}

    async def wait(self) -> dict[str, Any]:
        await asyncio.sleep(0)
        return self.pending.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_repository_delegates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """取得後のキュー読取なしで候補とcwdを渡し、1repo失敗でも他repoを回収する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories[:2]:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    snapshots = listing.configured_repo_snapshot(notes, repositories)
    for path in notes.rglob("*.md"):
        path.unlink()
    manager = _Manager(fail_repo="one")
    args = argparse.Namespace(resume=None, worktree="process-loop")
    results = asyncio.run(
        all_repos.run_repository_delegates(args, snapshots, manager=cast(all_repos.RepositoryDelegateManager, manager))
    )
    assert results[repositories[0].repo_id]["status"] == "failed"
    assert results[repositories[1].repo_id]["status"] == "completed"
    assert len(manager.started) == 2 and manager.closed
    payloads = [json.loads(next(line for line in prompt.splitlines() if line.startswith("{"))) for _, prompt in manager.started]
    assert [payload["candidates"][0]["filename"] for payload in payloads] == ["one.md", "two.md"]
    assert {cwd for cwd, _ in manager.started} == {str(repository.worktree) for repository in repositories[:2]}
    assert len({payload["lane_session_id"] for payload in payloads}) == 2
    assert all(payload["worktree_name"] == "process-loop" for payload in payloads)
    assert pathlib.Path(results[repositories[1].repo_id]["result_path"]).is_file()


def test_owner_wait_does_not_block_other_repository(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """専用起動が所有する対象を除外し、独立repoの担当だけを進める。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories[:2]:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    first = ownership.try_acquire(repositories[0].repo_id, worktree=repositories[0].worktree)
    assert first is not None
    manager = _Manager()
    args = argparse.Namespace(resume=None, worktree=None)
    results = asyncio.run(
        all_repos.run_repository_delegates(
            args,
            listing.configured_repo_snapshot(notes, repositories),
            manager=cast(all_repos.RepositoryDelegateManager, manager),
        )
    )
    assert results[repositories[0].repo_id]["status"] == "owner_wait"
    assert results[repositories[1].repo_id]["status"] == "completed"
    assert len(manager.started) == 1
    ownership.release(first)


def test_instructions_and_audit(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """指示は起動集合で1回受領して全担当へ中継し、監査条件のある空repoだけを起動する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories[:2]:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    snapshots = listing.configured_repo_snapshot(notes, repositories)
    manager = _Manager()
    loop_log.append_instruction("次の担当へ")
    consumed: list[str] = []
    original_consume = loop_log.consume_instructions

    def consume() -> str:
        consumed.append("consume")
        return original_consume()

    monkeypatch.setattr(loop_log, "consume_instructions", consume)
    args = argparse.Namespace(resume=None, worktree=None)
    results = asyncio.run(
        all_repos.run_repository_delegates(
            args,
            snapshots,
            manager=cast(all_repos.RepositoryDelegateManager, manager),
            audit_repositories={repositories[2].repo_id},
        )
    )
    assert all(result["status"] == "completed" for result in results.values())
    assert len(manager.started) == 3
    assert sum("次の担当へ" in prompt for _, prompt in manager.started) == 3
    assert not loop_log.read_instructions()
    assert consumed == ["consume"]
    empty_payload = json.loads(next(line for line in manager.started[2][1].splitlines() if line.startswith("{")))
    assert not empty_payload["candidates"]


def _loop_args(*, no_update: bool) -> argparse.Namespace:
    return argparse.Namespace(
        target_repo=None,
        resume=None,
        auto_resume=False,
        worktree=None,
        no_update=no_update,
        no_alerts=True,
        internal_dotfiles_updated=False,
    )


def test_start_conditions_compose(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """登録集合全体を`StartGate`へ渡し、抑止の解除前は担当を一件も生成しない。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "one.md", repositories[0].repo_id)
    gates: list[tuple[set[str], object]] = []
    generated: list[_Manager] = []

    def gate(_notes: pathlib.Path, targets: set[str], current: object) -> bool:
        gates.append((targets, current))
        return False

    def factory() -> all_repos.RepositoryDelegateManager:
        manager = _Manager()
        generated.append(manager)
        return cast(all_repos.RepositoryDelegateManager, manager)

    monkeypatch.setattr(watch, "pull_private_notes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch, "wait_for_start_permission", gate)
    monkeypatch.setattr(update_module, "resolve_dotfiles_root", lambda: None)
    all_repos.cmd_process_loop_all_repos(_loop_args(no_update=True), notes, manager_factory=factory)
    assert len(gates) == 1 and gates[0][0] == {repository.repo_id for repository in repositories}
    assert not generated


def test_control_and_updates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """2repo開始前の更新を1回に共有し、全担当の終端後に全体を1回だけ再起動する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories[:2]:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    manager = _Manager()
    events: list[str] = []

    def update(*_args: Any, **_kwargs: Any) -> tuple[bool, bool]:
        events.append("update")
        return True, True

    def restart(*_args: Any, **_kwargs: Any) -> None:
        events.append("restart")
        assert manager.closed
        raise SystemExit(0)

    monkeypatch.setattr(watch, "pull_private_notes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(update_module, "resolve_dotfiles_root", lambda: None)
    monkeypatch.setattr(update_module, "update_before_session", update)
    monkeypatch.setattr(update_module, "restart_process_loop", restart)
    with pytest.raises(SystemExit, match="0"):
        all_repos.cmd_process_loop_all_repos(
            _loop_args(no_update=False),
            notes,
            manager_factory=lambda: cast(all_repos.RepositoryDelegateManager, manager),
        )
    assert len(manager.started) == 2
    assert events == ["update", "restart"]


def test_registration_changes_before_start_public_cli(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """公開CLIの開始待機で登録解除・追加を反映し、新しい登録集合へ`StartGate`の判定を再び適用する。"""
    _setup_notes(tmp_path)
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    repositories = _repositories(tmp_path, monkeypatch)
    for repository in repositories[:2]:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(repositories[0].worktree)]))
    manager = _Manager()
    gates: list[tuple[set[str], object]] = []

    def gate(_notes: pathlib.Path, targets: set[str], current: object) -> bool:
        gates.append((targets, current))
        if len(gates) == 1:
            monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(repositories[1].worktree)]))
        return True

    def stop(*_args: Any, **_kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(watch, "pull_private_notes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch, "wait_for_start_permission", gate)
    monkeypatch.setattr(watch, "wait_for_changes", stop)
    monkeypatch.setattr(update_module, "resolve_dotfiles_root", lambda: None)
    monkeypatch.setattr("agent_toolkit.atk._agents_manager.AgentsServerManager", lambda **_kwargs: manager)
    with pytest.raises(SystemExit, match="0"):
        atk.main(["wi", "process-loop", "--no-update", "--no-alerts", "all-repos"], home=tmp_path)
    assert [targets for targets, _gate in gates] == [{repositories[0].repo_id}, {repositories[1].repo_id}]
    assert gates[0][1] is gates[1][1]
    assert [cwd for cwd, _prompt in manager.started] == [str(repositories[1].worktree)]


@pytest.mark.parametrize(
    ("message", "unfinished"),
    [("続行できない理由: CI認証の回復が必要", True), ("例示の語は続行できない理由:だが全工程は完了した", False)],
)
def test_completed_turn_with_blocked_result_retains_resume(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    unfinished: bool,
) -> None:
    """モデルturnの完了を工程完了に読み替えず、未完了結果を次回へ渡す。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "one.md", repositories[0].repo_id)
    snapshots = listing.configured_repo_snapshot(notes, repositories)

    class BlockedManager(_Manager):
        async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
            result = await super().start(model_type, prompt, cwd, **kwargs)
            self.pending[-1]["agent_message"] = message
            return result

    manager = BlockedManager()
    asyncio.run(
        all_repos.run_repository_delegates(
            argparse.Namespace(resume=None, worktree=None),
            snapshots,
            manager=cast(all_repos.RepositoryDelegateManager, manager),
        )
    )
    expected = {repositories[0].repo_id} if unfinished else set()
    assert ownership.unfinished_repository_ids(tuple(repository.repo_id for repository in repositories)) == expected
    if unfinished:
        assert ownership.resume_repository("session-one", (repositories[0].repo_id,)) == repositories[0].repo_id


def test_session_finished_before_restart(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """再起動が戻らない場合も正常通知を一度だけ送り、割込みは異常通知にする。"""
    notifications: list[bool] = []

    def restart(*_args: Any, **kwargs: Any) -> bool:
        kwargs["on_session_finished"](True)
        raise SystemExit(0)

    monkeypatch.setattr(session, "_run_process_session", restart)
    with pytest.raises(SystemExit):
        session.run_process_session(
            argparse.Namespace(),
            tmp_path,
            "",
            {},
            orchestrator="codex",
            model="model",
            effort="medium",
            resume_pending=False,
            dotfiles_root=None,
            on_session_finished=notifications.append,
        )
    assert notifications == [True]

    def interrupt(*_args: Any, **_kwargs: Any) -> bool:
        raise KeyboardInterrupt

    monkeypatch.setattr(session, "_run_process_session", interrupt)
    with pytest.raises(KeyboardInterrupt):
        session.run_process_session(
            argparse.Namespace(),
            tmp_path,
            "",
            {},
            orchestrator="codex",
            model="model",
            effort="medium",
            resume_pending=False,
            dotfiles_root=None,
            on_session_finished=notifications.append,
        )
    assert notifications == [True, False]
