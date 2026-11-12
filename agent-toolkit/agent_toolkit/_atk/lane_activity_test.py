"""lane activityの契約を検証する。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import subprocess
import sys
from collections.abc import Callable
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import agents_exit_session, lane
from agent_toolkit._atk import lane_activity as _lane_activity
from agent_toolkit._atk.wi import process_loop_session
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository, isolation
from agent_toolkit._testing.lane_integration_support import _integration_args, _integration_selection, _legacy
from agent_toolkit._testing.lane_support import _create, _create_args, _create_in, _invoke, _selection
from agent_toolkit._testing.session_release_support import retained_executor


@pytest.mark.parametrize("condition", ["unintegrated", "dirty", "unmerged", "active-session", "identity-less-session"])
def test_delete_preserves_unfinished_work_and_recovers_after_condition_is_resolved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """判定不能・未完了を削除せず、成立した後は同じ公開操作で回収する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    if condition == "unintegrated":
        _selection(selected, integrated=False)
    elif condition == "dirty":
        (worktree / "unfinished.txt").write_text("未commitの成果", encoding="utf-8")
    elif condition == "unmerged":
        git_repository.commit_all(worktree, "未統合の成果")
    else:
        # 生存するプロセスを記録した実行中の記録と、識別を持たない実行中の記録は、どちらも担当の終了を確認できない。
        owner = session_registry.owner_process()
        assert owner is not None
        processes = (owner,) if condition == "active-session" else ()
        session_registry.publish(
            "child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree), processes=processes
        )
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"] and worktree.is_dir()
    assert pathlib.Path(record["record_path"]).is_file()
    before = pathlib.Path(record["record_path"]).read_bytes()
    code, listed, stderr = _invoke(capsys, "delete", "--list", "--session-id", "lane-test-session")
    assert code == 0, stderr
    assert listed["records"][0]["record_path"] == record["record_path"]
    assert pathlib.Path(record["record_path"]).read_bytes() == before
    if condition == "unintegrated":
        _selection(selected)
    elif condition == "dirty":
        git_repository.commit_all(worktree, "保存した成果")
        git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    elif condition == "unmerged":
        git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    else:
        session_registry.publish("child", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(worktree))
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_delete_treats_session_whose_recorded_processes_exited_as_finished(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """所有側が終了を公開せずに終了し、記録した全プロセスが存在しない担当は終了として回収する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    with subprocess.Popen([sys.executable, "-c", "pass"]) as exited:
        identity = session_registry.ProcessIdentity.of(exited.pid)
        exited.wait()
    assert identity is not None
    session_registry.publish(
        "child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree), processes=(identity,)
    )
    assert lane._session_activity(worktree.resolve()) is None  # pylint: disable=protected-access
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_cleanup_uses_branch_refs_when_tags_have_the_same_names(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """同名tagで未統合成果の判定を変えず、統合後はbranchだけを回収する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    git_repository.commit_all(worktree, "未統合の成果")
    git_repository.run_git(repo, "tag", "main", f"refs/heads/{record['branch']}")
    git_repository.run_git(repo, "tag", record["branch"], "refs/heads/main")
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"] and worktree.is_dir()
    git_repository.run_git(repo, "merge", "--ff-only", f"refs/heads/{record['branch']}")
    code, removed, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert removed["removed"] == ["lane-01"] and not worktree.exists()
    refs = git_repository.git_output(repo, "for-each-ref", "--format=%(refname)").splitlines()
    assert f"refs/heads/{record['branch']}" not in refs
    assert f"refs/tags/{record['branch']}" in refs and "refs/tags/main" in refs


def test_live_external_process_holds_worktree_until_it_ends(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """担当が終了しても、その作業先を使う外部プロセスを削除で巻き込まない。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    with subprocess.Popen(
        [sys.executable, "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
        cwd=record["worktree"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    ) as child:
        assert child.stdout is not None and child.stdout.readline().strip() == "ready"
        try:
            code, held, _stderr = _invoke(capsys, "delete")
            assert code == 1 and held["retained"] and pathlib.Path(record["worktree"]).is_dir()
        finally:
            child.communicate(input="", timeout=30)
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


@pytest.mark.parametrize("terminal", [False, True])
def test_lane_preserves_session_when_running_or_owner_unresponsive(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], terminal: bool
) -> None:
    """実行中は終了・結果回収後のstopを案内し、無応答の所有側も資源を保持して再回収へ接続する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    owner = session_registry.owner_process()
    assert owner is not None
    session_registry.publish(
        "held-session",
        state=session_registry.PublicationState.RESULT_COMPLETED
        if terminal
        else session_registry.PublicationState.PENDING_RUNNING,
        cwd=record["worktree"],
        processes=(owner,),
    )
    monkeypatch.setattr(_lane_activity, "get_wait_timeout", lambda _bucket: 0.02)
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1
    reason = held["retained"][0]["reason"]
    assert "held-session" in reason and "stop" in reason and "lane delete" in reason
    if not terminal:
        assert reason.index("自然終了") < reason.index("atk agents wait") < reason.index("stop") < reason.index("lane delete")
        assert session_registry.resolve("held-session").state is session_registry.Resolution.RUNNING
    assert pathlib.Path(record["worktree"]).is_dir()
    assert not list(session_registry.registry_directory().glob("*.release-*"))


@pytest.mark.parametrize("entrypoint", ["exit", "process-loop"])
def test_automatic_exit_keeps_unreleased_terminal_sessions_without_waiting(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
) -> None:
    """無応答の所有者を持つ複数登録を理由付きで保持し、解放待機を省いて終了要求と回収結果を返す。"""
    repo, selected, first = _create(tmp_path, monkeypatch, capsys)
    code, second, stderr = _create_in(capsys, monkeypatch, "lane-test-session", repo, selected, lane_id="lane-02")
    assert code == 0, stderr
    owner = session_registry.owner_process()
    assert owner is not None
    for index, record in enumerate((first, second)):
        session_registry.publish(
            f"unresponsive-{index}",
            state=session_registry.PublicationState.RESULT_COMPLETED,
            cwd=record["worktree"],
            processes=(owner,),
        )

    def unexpected_wait(_bucket: str) -> float:
        pytest.fail("自動終了が終了資源の解放待機へ進んだ")

    monkeypatch.setattr(_lane_activity, "get_wait_timeout", unexpected_wait)
    started = lane.time.monotonic()
    if entrypoint == "exit":
        monkeypatch.setattr(agents_exit_session, "request_termination", lambda **_kwargs: ("unsupported", None))
        with pytest.raises(SystemExit) as ended:
            atk.main(["agents-exit-session"])
        assert ended.value.code == 0
        observed = json.loads(capsys.readouterr().out)
        assert observed["exit_session_invoked"] is True
        result = observed["lane_cleanup"]
    else:
        result = process_loop_session.cleanup_lane_session("lane-test-session")
        assert "atk lane delete --session-id lane-test-session" in capsys.readouterr().err
    assert not result["removed"] and len(result["retained"]) == 2
    for index, (record, held) in enumerate(zip((first, second), result["retained"], strict=True)):
        assert f"unresponsive-{index}" in held["reason"] and "解放応答" in held["reason"]
        assert held["next_action"] and pathlib.Path(record["record_path"]).is_file()
        assert pathlib.Path(record["worktree"]).is_dir()
        assert record["branch"] in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    assert pathlib.Path(result["result_path"]).is_file()
    assert not list(session_registry.registry_directory().glob("*.release-*"))
    assert lane.time.monotonic() - started < 10


def test_exit_entrypoint_cleans_resources_even_when_interactive_host_is_unidentified(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """終了対象の識別と資源回収を分け、ホスト不明でも登録済みの安全な資源を回収する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)

    def unsupported(**_kwargs: Any) -> tuple[str, None]:
        return "unsupported", None

    monkeypatch.setattr(agents_exit_session, "request_termination", unsupported)
    with pytest.raises(SystemExit) as ended:
        atk.main(["agents-exit-session"])
    assert ended.value.code == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["status"] == "unsupported" and observed["lane_cleanup"]["removed"] == ["lane-01"]
    assert not pathlib.Path(record["worktree"]).exists()


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("exit_code", [0, 7])
def test_process_loop_child_exit_leaves_cleanup_to_repository_owner(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, exit_code: int
) -> None:
    """CLIの正常・異常終了後も対象別所有者が回収を判断でき、起点と選定原本を維持する。"""
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    snapshot = tmp_path / "created.json"
    _selection(selected)
    script = tmp_path / "child.py"
    script.write_text(
        "import contextlib,io,json,pathlib,sys\n"
        "from agent_toolkit import atk\n"
        "stream=io.StringIO()\n"
        "with contextlib.redirect_stdout(stream):\n"
        "    try:\n"
        "        atk.main(['lane','create','--session-id','process-loop-repository',"
        "'--repo',sys.argv[1],'--base-branch','main',"
        "'--lane','lane-01','--selection-file',sys.argv[2]])\n"
        "    except SystemExit as result:\n"
        "        if result.code: raise\n"
        "pathlib.Path(sys.argv[3]).write_text(stream.getvalue(),encoding='utf-8')\n"
        "raise SystemExit(int(sys.argv[4]))\n",
        encoding="utf-8",
    )

    def child_argv(*_args: Any, **_kwargs: Any) -> tuple[list[str], None]:
        # 外部ホストの起動だけを有限の実プロセスへ差し替え、CLI・登録・Git・回収は実物を使う。
        return [sys.executable, str(script), str(repo), str(selected), str(snapshot), str(exit_code)], None

    monkeypatch.setattr(process_loop_session, "_build_session_argv", child_argv)
    args = argparse.Namespace(no_update=True)

    def execute() -> bool:
        return process_loop_session.run_process_session(
            args,
            repo,
            "test",
            dict(os.environ),
            orchestrator=engine,
            model="test-model",
            effort="low",
            resume_pending=False,
        )

    assert execute() is (exit_code == 0)
    created = json.loads(snapshot.read_text(encoding="utf-8"))
    assert pathlib.Path(created["worktree"]).exists() and pathlib.Path(created["record_path"]).exists()
    process_loop_session.cleanup_lane_session(created["session_id"])
    assert not pathlib.Path(created["worktree"]).exists() and not pathlib.Path(created["record_path"]).exists()
    assert repo.is_dir() and selected.is_file()


def test_integrate_ignores_session_in_nested_lane_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合先配下の別レーンの担当を所有へ含めずに統合を完了できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    nested = repo / ".claude/worktrees/other"
    git_repository.run_git(repo, "worktree", "add", "-b", "other", str(nested))
    session_registry.publish("other-writer", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(nested))
    handoff = tmp_path / "handoff.md"
    handoff.touch()
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps({"plans": [], "wis": [{"name": "20261023-000000-001.md", "commands": []}], "handoff": str(handoff)}),
        encoding="utf-8",
    )
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, record["initial_head"]))
    assert code == 0 and result["phase"] == "finalized", stderr


@pytest.mark.parametrize("relative", [".", "direct"])
def test_integrate_rejects_session_in_target_outside_nested_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], relative: str
) -> None:
    """統合先の直下と通常の子ディレクトリを使う合意外の担当を検出する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    session_registry.publish("target-writer", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(repo / relative))
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, tmp_path / "unused", record["initial_head"]))
    assert code == 2 and "現在の書込所有に含まれない担当が稼働中" in stderr


def test_worktree_activity_ignores_nested_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """解放対象の選別と再走査で、入れ子の別worktreeの担当を区別する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    nested = worktree / ".claude/worktrees/nested"
    git_repository.run_git(worktree, "worktree", "add", "-b", "nested", str(nested))
    session_registry.publish("nested-writer", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(nested))
    assert lane._worktree_activity(worktree) is None  # pylint: disable=protected-access
    session_registry.publish("direct-writer", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree))
    assert "direct-writer" in (lane._worktree_activity(worktree) or "")  # pylint: disable=protected-access


@pytest.mark.parametrize("source", ["cwd", "argv"])
def test_process_activity_ignores_nested_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], source: str
) -> None:
    """プロセスのcwdと絶対引数の双方で入れ子の別worktreeを区別する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    nested = worktree / ".claude/worktrees/nested"
    git_repository.run_git(worktree, "worktree", "add", "-b", "nested", str(nested))
    location = nested

    class Process:
        """外部プロセスの照会を決定論的なパスへ向ける。"""

        pid = 12345
        info = {"username": lane.psutil.Process().username()}

        def cwd(self) -> str:
            """照会対象のcwdを返す。"""
            return str(location if source == "cwd" else tmp_path)

        def cmdline(self) -> list[str]:
            """照会対象の絶対引数を返す。"""
            return ["program", f"--file={location / 'file'}"] if source == "argv" else ["program"]

    monkeypatch.setattr(lane.psutil, "process_iter", lambda *_args, **_kwargs: [Process()])
    assert lane._process_activity(worktree) is None  # pylint: disable=protected-access
    location = worktree
    assert lane._process_activity(worktree)  # pylint: disable=protected-access


@pytest.mark.parametrize(
    ("environment", "expected_timeout"),
    [
        ({"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h"}, 1740.0),
        ({"CLAUDECODE": "1", "CLAUDE_CODE_PROMPT_CACHE_TTL": "1h", "AGENT_TOOLKIT_DELEGATED_SESSION": "1"}, 240.0),
    ],
    ids=["main", "delegated"],
)
def test_release_deadline_keeps_delegated_cap(
    monkeypatch: pytest.MonkeyPatch, environment: dict[str, str], expected_timeout: float
) -> None:
    """資源解放の待機は、CLIの結果待機と別の関数から委譲先の短縮を含む期限を得る。

    CLIの結果待機の関数を使うと、CLIの上限の改訂で資源解放の期限が変わる。
    """
    for name in ("CLAUDECODE", "AGENT_TOOLKIT_DELEGATED_SESSION", "AGENT_TOOLKIT_OWNER_SESSION", "FORCE_PROMPT_CACHING_5M"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert lane.get_wait_timeout("main") == expected_timeout


@pytest.mark.parametrize("entrypoint", ["delete", "cli", "reuse", "fixed", "residual"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_lane_releases_retained_executor_through_owner_monitor(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    entrypoint: str,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """cwd保持中の終了資源を所有側が解放し、公開CLI・終了時回収・再利用・移管を通して未回収結果を取得できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    monkeypatch.setattr(_lane_activity, "get_wait_timeout", lambda _bucket: 5.0)
    async with retained_executor(
        pathlib.Path(record["worktree"]),
        host_environ(),
        monkeypatch,
        keep_executor=entrypoint == "residual",
    ) as retained:
        if entrypoint == "cli":
            environment = host_environ()
            for name in (*isolation.HOME_ENVIRONMENT_NAMES, *isolation.CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
                environment[name] = os.environ[name]
            command = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    str(pathlib.Path(atk.__file__).resolve()),
                    "lane",
                    "delete",
                    "--session-id",
                    "lane-test-session",
                ],
                cwd=repo,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                timeout=15,
            )
            code, result, stderr = command.returncode, json.loads(command.stdout), command.stderr
        elif entrypoint in {"reuse", "fixed"}:
            monkeypatch.setenv(lane.SESSION_ENV, "next-session")
            extra = ("--reuse-record", record["record_path"]) if entrypoint == "reuse" else ()
            code, result, stderr = await asyncio.to_thread(_invoke, capsys, *_create_args(repo, selected), *extra)
        else:
            code, result, stderr = await asyncio.to_thread(_invoke, capsys, "delete")
        if entrypoint == "residual":
            assert code == 1 and retained.executor.poll() is None
            assert retained.session_id in result["retained"][0]["reason"]
            assert "stop" in result["retained"][0]["reason"]
            assert pathlib.Path(record["worktree"]).is_dir()
            assert session_registry.resolve(retained.session_id).state is session_registry.Resolution.RELEASED
            return
        assert code == 0, stderr
        assert retained.executor.poll() == 0
        assert retained.release_calls == [retained.session_id]
        assert session_registry.resolve(retained.session_id).state is session_registry.Resolution.RELEASED
        if entrypoint in {"delete", "cli"}:
            assert result["removed"] == ["lane-01"]
            assert not pathlib.Path(record["worktree"]).exists()
            branches = git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
            assert record["branch"] not in branches
        else:
            assert result["session_id"] == "next-session"
            assert result["worktree"] == record["worktree"]
        assert not list(session_registry.registry_directory(retained.state_root).glob("*.release-*"))
        assert retained.wait_result() == 0
        assert json.loads(capsys.readouterr().out)["agent_message"] == "回収後も受領する結果"
        resumed = await retained.resume("再開")
        assert resumed["delivery"] == "reply_started"


@pytest.mark.parametrize("operation", ["fixed", "reuse", "delete", "recover"])
@pytest.mark.parametrize("activity", ["external-process", "new-session"])
@pytest.mark.asyncio
@pytest.mark.usefixtures("agents_server_isolation")
async def test_lane_returns_independent_activity_after_owner_release_without_repeating(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    activity: str,
    host_environ: Callable[[], dict[str, str]],
) -> None:
    """解放成功後に残る独立使用を1回で返し、全消費経路の資源保持と同じ操作での再開を確かめる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    if operation == "recover":
        _legacy(record)
    if operation in {"fixed", "reuse"}:
        monkeypatch.setenv(lane.SESSION_ENV, "next-session")
        extra = ("--reuse-record", record["record_path"]) if operation == "reuse" else ()
        argv = (*_create_args(repo, selected), *extra)
    elif operation == "recover":
        argv = ("recover", "--session-id", "lane-test-session", "--lane", "lane-01")
    else:
        argv = ("delete",)
    original_activity = lane._session_activity  # pylint: disable=protected-access  # noqa: SLF001
    checks = 0

    def independent_usage(path: pathlib.Path) -> str | None:
        nonlocal checks
        checks += 1
        assert checks == 1, "所有側の解放完了後も独立した使用を反復照会した"
        if activity == "new-session":
            session_registry.publish("new-user", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(path))
        return original_activity(path)

    async with retained_executor(worktree, host_environ(), monkeypatch) as retained:
        with subprocess.Popen(
            [sys.executable, "-c", "import sys;print('ready',flush=True);sys.stdin.read()"],
            cwd=worktree,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        ) as external:
            assert external.stdout is not None and external.stdout.readline().strip() == "ready"
            monkeypatch.setattr(_lane_activity, "session_activity", independent_usage)
            try:
                code, result, stderr = await asyncio.to_thread(_invoke, capsys, *argv)
                assert code == (2 if operation in {"fixed", "reuse"} else 1), stderr
                reason = stderr if code == 2 else result["retained"][0]["reason"]
                assert ("new-user" if activity == "new-session" else "外部プロセス") in reason
                assert checks == 1 and retained.release_calls == [retained.session_id]
                assert session_registry.resolve(retained.session_id).state is session_registry.Resolution.RELEASED
                assert worktree.is_dir() and pathlib.Path(record["record_path"]).is_file()
                assert record["branch"] in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
            finally:
                monkeypatch.setattr(_lane_activity, "session_activity", original_activity)
                external.communicate(input="", timeout=30)
                if activity == "new-session":
                    session_registry.publish(
                        "new-user", state=session_registry.PublicationState.RESULT_COMPLETED, cwd=str(worktree)
                    )
        code, result, stderr = await asyncio.to_thread(_invoke, capsys, *argv)
        assert code == 0, stderr
        if operation in {"fixed", "reuse"}:
            assert result["session_id"] == "next-session" and worktree.is_dir()
        elif operation == "recover":
            assert not worktree.exists() and result["recovered"]
            assert record["branch"] in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
        else:
            assert not worktree.exists() and result["removed"] == ["lane-01"]
