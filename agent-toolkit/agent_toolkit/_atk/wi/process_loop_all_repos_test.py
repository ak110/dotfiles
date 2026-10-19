"""登録取得・通常delegate・リポジトリ別所有の接続契約を検証する。"""

import argparse
import asyncio
import contextlib
import dataclasses
import datetime
import io
import json
import os
import pathlib
from typing import Any, cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import config, lane, review_audit
from agent_toolkit._atk.wi import alerts as wi_alerts
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop_all_repos as all_repos
from agent_toolkit._atk.wi import process_loop_launch_gate as launch_gate
from agent_toolkit._atk.wi import process_loop_log as loop_log
from agent_toolkit._atk.wi import process_loop_ownership as ownership
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._atk.wi import process_loop_update as update_module
from agent_toolkit._atk.wi import process_loop_watch as watch
from agent_toolkit._common import session_registry, state_paths
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.agents_server_support import (
    AssistantMessage,
    FakeClaudeClient,
    ResultMessage,
    SystemMessage,
    install_backend,
    resume_waits,
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
        (notes / "inbox/task.md").unlink()
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
    monkeypatch.setenv(
        "AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / ("private-notes" if (tmp_path / "private-notes").exists() else "notes"))
    )
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
    assert len(records) == 11
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
        self.sent: list[tuple[str, str]] = []
        self.killed: list[str] = []
        self.finish_work = True

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
        result = self.pending.pop(0)
        if self.finish_work:
            notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
            repo_name = result["session_id"].removeprefix("session-")
            for directory in ("inbox", "processing"):
                for path in (notes / directory).glob("*.md"):
                    if f"target_repo: example.test/team/{repo_name}\n" in path.read_text(encoding="utf-8"):
                        (notes / "adopted").mkdir(exist_ok=True)
                        path.rename(notes / "adopted" / path.name)
        return result

    async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
        self.sent.append((session_id, prompt))
        self.pending.append({"session_id": session_id, "status": "completed", "agent_message": "完了"})
        return {"delivery": "reply_started"}

    async def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
        del verbose
        return {"session_id": session_id, "status": "running", "started_at": datetime.datetime.now(datetime.UTC).isoformat()}

    async def kill(self, session_id: str, timeout: float = 270, stop: bool = False) -> dict[str, Any]:
        del timeout, stop
        self.killed.append(session_id)
        return {"session_id": session_id, "status": "interrupted"}

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


def _run_delegates(
    manager: _Manager, snapshots: tuple[listing.RepositorySnapshot, ...], *, timeout: float = 5
) -> dict[str, dict[str, Any]]:
    return asyncio.run(
        asyncio.wait_for(
            all_repos.run_repository_delegates(
                argparse.Namespace(resume=None, worktree=None),
                snapshots,
                manager=cast(all_repos.RepositoryDelegateManager, manager),
            ),
            timeout=timeout,
        )
    )


# 実際のレーン回収はプロセス一覧から使用中かを判定するため、負荷の高い並列実行では1回に数秒かかる。
_LANE_TIMEOUT = 120


@pytest.mark.parametrize("remaining", ["processing", "inbox", "lane", "held-result"])
def test_incomplete_delegate_is_continued(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    remaining: str,
) -> None:
    """残る各客観条件と保留結果の確定で同じ担当へ続行し、所有を維持する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    task = _write(notes, "task.md", repositories[0].repo_id)
    snapshots = listing.configured_repo_snapshot(notes, repositories)
    if remaining == "processing":
        (notes / "processing").mkdir()
        task = task.rename(notes / "processing/task.md")
    elif remaining in {"lane", "held-result"}:
        task.unlink()

    class ContinuingManager(_Manager):
        def __init__(self) -> None:
            super().__init__()
            self.lane_path = tmp_path / "unused-lane.json"

        async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
            response = await super().start(model_type, prompt, cwd, **kwargs)
            payload = json.loads(next(line for line in prompt.splitlines() if line.startswith("{")))
            directory = state_paths.state_dir() / "lanes" / payload["lane_session_id"]
            self.lane_path = directory / "lane-01.json"
            if remaining == "lane":
                directory.mkdir(parents=True)
                git.commit_all(pathlib.Path(cwd), "初期状態")
                # lane.createの登録形式を使い、HEADは実Gitのbranchから照会させる。
                branch = git.run_git(pathlib.Path(cwd), "branch", "--show-current").stdout.strip()
                self.lane_path.write_text(json.dumps({"repo": cwd, "branch": branch, "lane": "lane-01"}), encoding="utf-8")
            if remaining == "held-result":
                self.pending[-1]["error"] = {"heldResultFinalized": True}
            return response

        async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
            records = [
                json.loads(path.read_text(encoding="utf-8"))
                for path in ownership.ownership_directory().glob("*.json")
                if not path.name.endswith(".result.json")
            ]
            assert len(records) == 1 and not records[0]["released"]
            assert (
                "task.md" in prompt
                if remaining in {"processing", "inbox"}
                else "lane-01" in prompt
                if remaining == "lane"
                else True
            )
            task.unlink(missing_ok=True)
            self.lane_path.unlink(missing_ok=True)
            return await super().send_message(session_id, prompt)

    manager = ContinuingManager()
    manager.finish_work = False
    _run_delegates(manager, snapshots)
    assert len(manager.sent) == 1 and manager.sent[0][0] == "session-one"
    assert not ownership.unfinished_repository_ids((repositories[0].repo_id,))


def test_completed_delegate_releases_ownership(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """候補の終端とprocessing・未統合レーンの空集合で完遂を確定する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "task.md", repositories[0].repo_id)
    manager = _Manager()
    _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories))
    record = json.loads(
        next(
            path for path in ownership.ownership_directory().glob("*.json") if not path.name.endswith(".result.json")
        ).read_text(encoding="utf-8")
    )
    assert record["completed"] and record["released"]
    assert not manager.sent


def test_no_progress_continuation_is_abandoned(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最初の続行を基準とし、2回連続で同じ写しなら再開情報を保持して閉じる。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "task.md", repositories[0].repo_id)
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    manager = _Manager()
    manager.finish_work = False
    _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories))
    assert len(manager.sent) == 2
    assert "打ち切った" in capsys.readouterr().out
    assert "repository_no_progress" in loop_log.log_path().read_text(encoding="utf-8")
    acquired = ownership.try_acquire(repositories[0].repo_id, worktree=repositories[0].worktree)
    assert acquired is not None and acquired.previous is not None
    assert acquired.previous["session_id"] == "session-one"
    ownership.release(acquired)


@pytest.mark.parametrize("output", [True, False])
def test_stalled_delegate_is_killed(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, output: bool) -> None:
    """最終出力または起動時刻から閾値を超えた担当を停止し、他担当を回収する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories[:2]:
        _write(notes, repository.worktree.name + ".md", repository.repo_id)

    class StalledManager(_Manager):
        async def wait(self) -> dict[str, Any]:
            self.pending = [item for item in self.pending if item["session_id"] != "session-one"]
            if self.pending:
                return await super().wait()
            await asyncio.Event().wait()
            raise AssertionError("待機は停止の観測で取り消される")

        async def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
            assert verbose
            if session_id == "session-two":
                return await super().show_session(session_id, verbose=verbose)
            now = datetime.datetime.now(datetime.UTC)
            old = (now - datetime.timedelta(seconds=all_repos.STALL_THRESHOLD_SECONDS + 1)).isoformat()
            return {"status": "running", "started_at": old, "output_updated_at": old if output else None}

    manager = StalledManager()
    results = _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories))
    assert manager.killed == ["session-one"]
    assert results[repositories[1].repo_id]["status"] == "completed"
    assert ownership.unfinished_repository_ids(tuple(repo.repo_id for repo in repositories)) == {repositories[0].repo_id}


def test_stall_threshold_constant() -> None:
    """停止検知の猶予は自動再開の期限より長く保つ。"""
    assert all_repos.STALL_THRESHOLD_SECONDS == resume_waits.AUTO_RESUME_DEADLINE_SECONDS + 1800


@pytest.mark.parametrize("failures", [1, 3])
def test_wait_exception_is_retried(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, failures: int) -> None:
    """待機例外は反復ごとに隔離し、1回なら回復、連続3回なら停止して再開情報を保つ。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories[:2]:
        _write(notes, repository.worktree.name + ".md", repository.repo_id)

    class FailingWaitManager(_Manager):
        calls = 0

        async def wait(self) -> dict[str, Any]:
            self.calls += 1
            if self.calls <= failures:
                raise RuntimeError("待機の障害")
            return await super().wait()

    manager = FailingWaitManager()
    _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories))
    assert len(manager.killed) == (2 if failures == 3 else 0)
    assert bool(ownership.unfinished_repository_ids(tuple(repo.repo_id for repo in repositories))) is (failures == 3)
    assert manager.closed


def test_repeated_wait_exceptions_stop_round(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """3回連続の待機例外で担当を停止し、再開情報を保持する。"""
    test_wait_exception_is_retried(tmp_path, monkeypatch, 3)


@pytest.mark.parametrize("kill_failure", ["exception", "running"])
@pytest.mark.parametrize(
    "resolution",
    [session_registry.Resolution.RUNNING, session_registry.Resolution.UNREADABLE, session_registry.Resolution.TERMINAL],
)
def test_held_ownership_after_kill_failure(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    kill_failure: str,
    resolution: session_registry.Resolution,
) -> None:
    """停止の確認失敗後も所有を保持し、次の反復のsession照会で再起動の可否を決める。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "task.md", repositories[0].repo_id)
    snapshots = listing.configured_repo_snapshot(notes, repositories)

    class HeldManager(_Manager):
        async def wait(self) -> dict[str, Any]:
            raise RuntimeError("待機の障害")

        async def kill(self, session_id: str, timeout: float = 270, stop: bool = False) -> dict[str, Any]:
            if kill_failure == "exception":
                raise RuntimeError("停止の障害")
            return {"status": "running"}

    _run_delegates(HeldManager(), snapshots)
    path = next(ownership.ownership_directory().glob("*.json"))
    record = json.loads(path.read_text(encoding="utf-8"))
    assert record["held"] and not record["released"]
    original = session_registry.resolve("missing")
    monkeypatch.setattr(session_registry, "resolve", lambda *_args, **_kwargs: dataclasses.replace(original, state=resolution))
    next_manager = _Manager()
    results = _run_delegates(next_manager, snapshots)
    if resolution in {session_registry.Resolution.RUNNING, session_registry.Resolution.UNREADABLE}:
        assert not next_manager.started
        assert results[repositories[0].repo_id]["status"] == "owner_wait"
    else:
        assert len(next_manager.started) == 0 and len(next_manager.sent) == 1


def _lane_repositories(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[config.ConfiguredRepository, ...]:
    paths = [
        git.init_repository(
            tmp_path / name, initial_branch="main", origin=f"https://example.test/team/{name}.git", commit_message="初期状態"
        )
        for name in ("one", "two")
    ]
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(path) for path in paths]))
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(tmp_path / "notes"))
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    return config.resolve_configured_repositories()


def _lane_selection(path: pathlib.Path, *, integrated: bool) -> None:
    state = "統合済み" if integrated else "未統合"
    path.write_text(
        json.dumps({"レーンの所要時間": [{"レーン": "lane-01", "統合状態": state}]}, ensure_ascii=False), encoding="utf-8"
    )


def _create_lane(repo: pathlib.Path, lane_session_id: str, selection: pathlib.Path) -> dict[str, Any]:
    """担当が受け取ったレーン所有sessionで、公開CLIから実際のレーン資源を作成する。"""
    stream = io.StringIO()
    previous = os.environ.get(lane.SESSION_ENV)
    os.environ[lane.SESSION_ENV] = lane_session_id
    try:
        with contextlib.redirect_stdout(stream), pytest.raises(SystemExit) as ended:
            atk.main(
                ["lane", "create", "--repo", str(repo), "--base-branch", "main", "--lane", "lane-01"]
                + ["--selection-file", str(selection)]
            )
    finally:
        if previous is None:
            os.environ.pop(lane.SESSION_ENV, None)
        else:
            os.environ[lane.SESSION_ENV] = previous
    assert ended.value.code == 0, stream.getvalue()
    record = json.loads(stream.getvalue())
    # 統合工程の完了を登録へ反映し、常駐側の完遂判定が未統合レーンとして数えない状態にする。
    path = pathlib.Path(record["record_path"])
    stored = json.loads(path.read_text(encoding="utf-8"))
    stored["integration"] = {"phase": "finalized"}
    path.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
    return record


def _lane_exists(record: dict[str, Any]) -> bool:
    repo = pathlib.Path(record["repo"])
    branches = git.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    return (
        pathlib.Path(record["worktree"]).exists()
        or pathlib.Path(record["record_path"]).exists()
        or record["branch"] in branches
    )


def _record_log(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    """ログの追記を実際に書いたうえで、イベント名と項目を構造のまま記録する。"""
    recorded: list[tuple[str, dict[str, Any]]] = []
    original = loop_log.append

    def append(event: str, *, session_id: str | None = None, **fields: Any) -> None:
        recorded.append((event, {"session_id": session_id, **fields}))
        original(event, session_id=session_id, **fields)

    monkeypatch.setattr(loop_log, "append", append)
    return recorded


def _events(recorded: list[tuple[str, dict[str, Any]]], name: str) -> list[dict[str, Any]]:
    return [fields for event, fields in recorded if event == name]


class _LaneManager(_Manager):
    """担当の起動ごとに受渡しの`lane_session_id`でレーンを作成し、返却本文を試験ごとに指定する。"""

    def __init__(self, selection: pathlib.Path, *, message: str = "完了") -> None:
        super().__init__()
        self.selection = selection
        self.message = message
        self.lanes: dict[str, dict[str, Any]] = {}
        self.lane_sessions: dict[str, str] = {}

    async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
        response = await super().start(model_type, prompt, cwd, **kwargs)
        payload = json.loads(next(line for line in prompt.splitlines() if line.startswith("{")))
        self.lane_sessions[pathlib.Path(cwd).name] = payload["lane_session_id"]
        self.lanes[pathlib.Path(cwd).name] = _create_lane(pathlib.Path(cwd), payload["lane_session_id"], self.selection)
        self.pending[-1]["agent_message"] = self.message
        return response


@pytest.mark.parametrize("route", ["completed", "blocked", "no_progress", "stalled"])
def test_finished_delegate_cleans_lane_resources(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, route: str) -> None:
    """完遂・続行できない返却・無進捗の打切り・停滞の停止のそれぞれで、所有解放の後に担当のレーン登録・worktree・branchを回収する。"""
    repositories = _lane_repositories(tmp_path, monkeypatch)
    recorded = _record_log(monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "one.md", repositories[0].repo_id)
    selection = tmp_path / "selection.json"
    _lane_selection(selection, integrated=True)

    class RouteManager(_LaneManager):
        async def wait(self) -> dict[str, Any]:
            if route == "stalled":
                await asyncio.Event().wait()
            return await super().wait()

        async def show_session(self, session_id: str, *, verbose: bool = False) -> dict[str, Any]:
            if route != "stalled":
                return await super().show_session(session_id, verbose=verbose)
            old = (
                datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=all_repos.STALL_THRESHOLD_SECONDS + 1)
            ).isoformat()
            return {"status": "running", "started_at": old}

    manager = RouteManager(selection, message="続行できない理由: 認可が必要" if route == "blocked" else "完了")
    manager.finish_work = route == "completed"
    _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories), timeout=_LANE_TIMEOUT)
    record = manager.lanes["one"]
    assert not _lane_exists(record)
    events = _events(recorded, "lane_cleanup")
    assert [(event["target_repo"], event["lane_session_id"], event["removed"]) for event in events] == [
        (repositories[0].repo_id, manager.lane_sessions["one"], ["lane-01"])
    ]
    assert manager.killed == (["session-one"] if route == "stalled" else [])


@pytest.mark.parametrize("condition", ["continue", "held", "unintegrated", "dirty", "active-session"])
def test_unfinished_or_protected_lanes_are_retained(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """続行中・停止未確認の間は回収せず、`lane.py`の`_eligible`の保持条件に当たる資源は理由と再回収の操作を残して保持する。"""
    repositories = _lane_repositories(tmp_path, monkeypatch)
    recorded = _record_log(monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "one.md", repositories[0].repo_id)
    selection = tmp_path / "selection.json"
    _lane_selection(selection, integrated=condition != "unintegrated")

    class ProtectedManager(_LaneManager):
        observed_during_continue: bool | None = None

        async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
            response = await super().start(model_type, prompt, cwd, **kwargs)
            worktree = pathlib.Path(self.lanes["one"]["worktree"])
            if condition == "dirty":
                (worktree / "unfinished.txt").write_text("未commitの成果", encoding="utf-8")
            elif condition == "active-session":
                owner = session_registry.owner_process()
                assert owner is not None
                session_registry.publish("child", terminal=False, cwd=str(worktree), processes=(owner,))
            return response

        async def wait(self) -> dict[str, Any]:
            if condition == "held":
                raise RuntimeError("待機の障害")
            return await super().wait()

        async def send_message(self, session_id: str, prompt: str) -> dict[str, Any]:
            # 続行を配送する時点では、担当のレーン資源が残っていなければならない。
            self.observed_during_continue = _lane_exists(self.lanes["one"])
            notes_path = notes / "inbox/one.md"
            notes_path.unlink(missing_ok=True)
            return await super().send_message(session_id, prompt)

        async def kill(self, session_id: str, timeout: float = 270, stop: bool = False) -> dict[str, Any]:
            if condition == "held":
                return {"status": "running"}
            return await super().kill(session_id, timeout, stop)

    manager = ProtectedManager(selection, message="完了" if condition == "continue" else "続行できない理由: 確認が必要")
    manager.finish_work = False
    _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories), timeout=_LANE_TIMEOUT)
    record = manager.lanes["one"]
    lane_session_id = manager.lane_sessions["one"]
    events = _events(recorded, "lane_cleanup")
    if condition == "continue":
        # 続行の後に完遂を確定してから回収する。
        assert manager.observed_during_continue is True
        assert not _lane_exists(record) and len(events) == 1
        return
    assert _lane_exists(record)
    if condition == "held":
        assert not events
        stored = json.loads(next(ownership.ownership_directory().glob("*[!t].json")).read_text(encoding="utf-8"))
        assert stored["held"] and stored["lane_session_id"] == lane_session_id
        return
    assert len(events) == 1 and events[0]["retained"] and not events[0]["removed"]
    assert f"atk lane delete --session-id {lane_session_id}" in capsys.readouterr().err
    acquired = ownership.try_acquire(repositories[0].repo_id, worktree=repositories[0].worktree)
    assert acquired is not None and acquired.lane_session_id == lane_session_id
    ownership.release(acquired)


def test_cleanup_targets_each_lane_session(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """複数リポジトリの担当では、各担当の終了時にその担当へ渡したレーン所有sessionだけを回収判定へ渡す。"""
    repositories = _lane_repositories(tmp_path, monkeypatch)
    recorded = _record_log(monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    selection = tmp_path / "selection.json"
    _lane_selection(selection, integrated=True)
    judged: list[str] = []
    original = lane.cleanup_session

    def recording_cleanup(session_id: str | None, **kwargs: Any) -> dict[str, Any]:
        assert session_id is not None
        judged.append(session_id)
        return original(session_id, **kwargs)

    monkeypatch.setattr(lane, "cleanup_session", recording_cleanup)
    manager = _LaneManager(selection)
    _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories), timeout=_LANE_TIMEOUT)
    assert sorted(judged) == sorted(manager.lane_sessions.values()) and len(set(judged)) == 2
    assert not any(_lane_exists(record) for record in manager.lanes.values())
    assert {(event["target_repo"], event["lane_session_id"]) for event in _events(recorded, "lane_cleanup")} == {
        (repository.repo_id, manager.lane_sessions[repository.worktree.name]) for repository in repositories
    }


def test_cleanup_failure_is_reported_and_others_continue(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1担当の保持と別担当の回収失敗を対象別に記録・案内し、全担当の終了記録まで進める。"""
    repositories = _lane_repositories(tmp_path, monkeypatch)
    recorded = _record_log(monkeypatch)
    notes = tmp_path / "notes"
    for repository in repositories:
        _write(notes, f"{repository.worktree.name}.md", repository.repo_id)
    selection = tmp_path / "selection.json"
    _lane_selection(selection, integrated=True)
    original = lane.cleanup_session

    class MixedManager(_LaneManager):
        async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
            response = await super().start(model_type, prompt, cwd, **kwargs)
            if pathlib.Path(cwd).name == "one":
                (pathlib.Path(self.lanes["one"]["worktree"]) / "unfinished.txt").write_text("成果", encoding="utf-8")
            return response

    manager = MixedManager(selection)

    def failing_cleanup(session_id: str | None, **kwargs: Any) -> dict[str, Any]:
        if session_id == manager.lane_sessions["two"]:
            raise OSError("回収先へ書けない")
        return original(session_id, **kwargs)

    monkeypatch.setattr(lane, "cleanup_session", failing_cleanup)
    results = _run_delegates(manager, listing.configured_repo_snapshot(notes, repositories), timeout=_LANE_TIMEOUT)
    assert all(results[repository.repo_id]["status"] == "completed" for repository in repositories)
    err = capsys.readouterr().err
    for name in ("one", "two"):
        assert f"atk lane delete --session-id {manager.lane_sessions[name]}" in err
    events = {event["target_repo"]: event for event in _events(recorded, "lane_cleanup")}
    assert events[repositories[0].repo_id]["retained"]
    assert events[repositories[1].repo_id]["error"] == "回収先へ書けない"
    ended = [fields["target_repo"] for fields in _events(recorded, "repository_session_end")]
    assert sorted(ended) == sorted(repository.repo_id for repository in repositories)


def _run_loop_rounds(
    monkeypatch: pytest.MonkeyPatch,
    notes: pathlib.Path,
    manager: _Manager,
    *,
    on_wait: tuple[Any, ...] = (),
    **options: Any,
) -> list[tuple[str, dict[str, Any]]]:
    """公開の常駐関数`cmd_process_loop_all_repos`を`--no-update`で回し、変更待機ごとに`on_wait`を順に適用し、尽きたら終了させる。"""
    recorded = _record_log(monkeypatch)
    waits = iter(on_wait)

    def wait(*_args: Any, **_kwargs: Any) -> bool:
        action = next(waits, None)
        if action is None:
            raise KeyboardInterrupt
        action()
        return True

    monkeypatch.setattr(watch, "pull_private_notes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch, "wait_for_start_permission", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(watch, "wait_for_changes", wait)
    monkeypatch.setattr(update_module, "resolve_dotfiles_root", lambda: None)
    args = _loop_args(no_update=True)
    for key, value in options.items():
        setattr(args, key, value)
    all_repos.cmd_process_loop_all_repos(
        args, notes, manager_factory=lambda: cast(all_repos.RepositoryDelegateManager, manager)
    )
    return recorded


class _UnresolvedManager(_Manager):
    """候補を解消せずに返す担当。`blocked`なら`続行できない理由:`を返す。"""

    def __init__(self, *, blocked: bool) -> None:
        super().__init__()
        self.blocked = blocked
        self.finish_work = False
        self.during_start: Any = None

    async def start(self, model_type: str, prompt: str, cwd: str, **kwargs: Any) -> dict[str, Any]:
        response = await super().start(model_type, prompt, cwd, **kwargs)
        if self.blocked:
            self.pending[-1]["agent_message"] = "続行できない理由: このホストでは完成条件を観測できない"
        if self.during_start is not None:
            self.during_start(len(self.started))
        return response


@pytest.mark.parametrize("ending", ["blocked", "no_progress"])
def test_no_progress_repository_is_skipped(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], ending: str
) -> None:
    """続行できない返却と無進捗の打切りの後は、起動入力が変わらない間その対象の担当を起動しない。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "one.md", repositories[0].repo_id)
    manager = _UnresolvedManager(blocked=ending == "blocked")
    recorded = _run_loop_rounds(monkeypatch, notes, manager, on_wait=(lambda: None,))
    assert len(manager.started) == 1
    assert [fields["target_repo"] for fields in _events(recorded, "launch_skipped_no_progress")] == [repositories[0].repo_id]
    out = capsys.readouterr().out
    assert "担当の起動を見送ります" in out and "新しい未判定のDependabotアラート" in out


@pytest.mark.parametrize("change", ["new-wi", "body", "state", "readiness", "new-alert", "during-run"])
def test_skipped_repository_relaunches_on_change(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    """見送り中に起動入力が変われば次の反復で同じ対象の担当を起動する。稼働中に加わったWIも次の起動対象になる。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    repo_id = repositories[0].repo_id
    task = _write(notes, "one.md", repo_id)
    if change == "readiness":
        _write(notes, "two.md", repo_id, extra="depends_on:\n- one.md\n")
    alerts: list[dict[str, Any]] = [{"number": 1}]
    monkeypatch.setattr(wi_alerts, "check_and_submit_alerts", lambda *_a, **_k: wi_alerts.AlertCheckResult(0, ()))
    monkeypatch.setattr(review_audit, "dependabot_pending", lambda _repository: {"status": "available", "alerts": alerts})
    manager = _UnresolvedManager(blocked=True)
    if change == "during-run":
        manager.during_start = lambda count: _write(notes, "arrived.md", repo_id) if count == 1 else None

    def apply() -> None:
        if change == "new-wi":
            _write(notes, "added.md", repo_id)
        elif change == "body":
            task.write_text(task.read_text(encoding="utf-8") + "\n## ユーザーコメント\n\n回答\n", encoding="utf-8")
        elif change == "state":
            (notes / "processing").mkdir(exist_ok=True)
            task.rename(notes / "processing" / task.name)
        elif change == "readiness":
            # 依存先の終端で依存元の区分が内部依存待ちからreadyへ変わる。
            (notes / "adopted").mkdir(exist_ok=True)
            task.rename(notes / "adopted" / task.name)
        elif change == "new-alert":
            alerts.append({"number": 2})

    recorded = _run_loop_rounds(
        monkeypatch, notes, manager, on_wait=(apply,), no_alerts=False, alert_interval=0, alert_forge="github"
    )
    # 未判定アラートだけを持つ他の対象は、1回目の担当の後は入力が変わらない限り見送られる。
    launched = [pathlib.Path(cwd).name for cwd, _prompt in manager.started] + [
        session_id.removeprefix("session-") for session_id, prompt in manager.sent if "repository-delegate" in prompt
    ]
    # 2回目は保持したall-repos担当への再開の配送で起動する。新しいアラートは全対象の入力を変える。
    relaunched = ["one", "two", "empty"] if change == "new-alert" else ["one"]
    assert launched == ["one", "two", "empty", *relaunched]
    assert {fields["target_repo"] for fields in _events(recorded, "launch_skipped_no_progress")} == (
        set() if change == "new-alert" else {repositories[1].repo_id, repositories[2].repo_id}
    )


@pytest.mark.parametrize("mode", ["resume", "auto-resume"])
def test_explicit_resume_bypasses_skip(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """無進捗記録があっても`--resume`と`--auto-resume`の対象は起動する。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    repo_id = repositories[0].repo_id
    _write(notes, "one.md", repo_id)
    snapshot = listing.configured_repo_snapshot(notes, repositories)[0]
    current = launch_gate.repository_input(snapshot, None)
    launch_gate.record_no_progress(repo_id, current, current)
    assert launch_gate.decide(repo_id, current).skipped
    held = ownership.try_acquire(repo_id, worktree=repositories[0].worktree, mode="all-repos")
    assert held is not None
    ownership.release(ownership.update_session(held, "session-one"), completed=False)
    manager = _UnresolvedManager(blocked=True)
    options: dict[str, Any] = {"resume": "session-one"} if mode == "resume" else {"auto_resume": True}
    _run_loop_rounds(monkeypatch, notes, manager, **options)
    # 保持したall-repos担当へ再開を配送し、記録による見送りを適用しない。
    assert not manager.started and "repository-delegate" in manager.sent[0][1]


def test_diagnostic_only_repository_is_skipped(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """対処を要する診断だけを持つ対象も、担当が続行できずに返した後は同じ診断の間は起動しない。"""
    repositories = _repositories(tmp_path, monkeypatch)
    notes = tmp_path / "notes"
    _write(notes, "broken.md", repositories[0].repo_id, extra="depends_on:\n- missing.md\n")
    snapshot = listing.configured_repo_snapshot(notes, repositories)[0]
    assert not snapshot.candidates and snapshot.readiness.actionable_count
    manager = _UnresolvedManager(blocked=True)
    recorded = _run_loop_rounds(monkeypatch, notes, manager, on_wait=(lambda: None,))
    assert [pathlib.Path(cwd).name for cwd, _prompt in manager.started] == ["one"]
    assert [fields["target_repo"] for fields in _events(recorded, "launch_skipped_no_progress")] == [repositories[0].repo_id]
