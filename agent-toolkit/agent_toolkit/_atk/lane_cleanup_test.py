"""lane cleanupの契約を検証する。"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import pathlib
import shlex
import shutil
import stat
import subprocess
import sys
import types
from collections.abc import Generator, Iterator
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import agents_exit_session, lane, lane_activity, managed_temp
from agent_toolkit._common.delegated_session import OWNER_SESSION_ENV
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import _finalize_lane_for_cleanup, _legacy
from agent_toolkit._testing.lane_support import _create, _create_in, _invoke, _selection


def _published_main(repo: pathlib.Path) -> None:
    git_repository.run_git(repo, "remote", "add", "origin", str(repo.parent / "remote.git"))
    git_repository.run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git_repository.run_git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")


@pytest.fixture(name="idle_lane")
def _idle_lane(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(lane_activity, "process_activity", lambda _worktree: None)


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(4.0, reason="実Gitの候補の一覧から終了時の回収と保持までを確かめる。")
def test_exit_removes_only_disposable_orphan_worktrees(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _selected, _registered = _create(tmp_path, monkeypatch, capsys)
    _published_main(repo)
    (repo / "integrated-only.txt").write_text("記録された統合先だけへの取込み", encoding="utf-8")
    git_repository.commit_all(repo, "対象系統へ取込み")
    monkeypatch.chdir(repo)
    monkeypatch.delenv(OWNER_SESSION_ENV, raising=False)
    deletable = repo.parent / "integrated"
    retained = repo.parent / "with-local-work"
    locked = repo.parent / "locked"
    git_repository.run_git(repo, "worktree", "add", "-b", "orch/integrated", str(deletable))
    git_repository.run_git(repo, "worktree", "add", "-b", "orch/dirty", str(retained))
    git_repository.run_git(repo, "worktree", "add", "--lock", "-b", "orch/locked", str(locked))
    (retained / "notes.txt").write_text("保存する未追跡ファイル", encoding="utf-8")
    monkeypatch.setattr(agents_exit_session, "request_termination", lambda **_kwargs: ("unsupported", None))

    code, listed, stderr = _invoke(capsys, "delete", "--list", "--repo", str(repo))
    assert code == 0, stderr
    paths = {item["worktree"]: item for item in listed["orphan_candidates"]}
    assert paths[str(deletable.resolve())]["removable"]
    assert paths[str(deletable.resolve())]["age_seconds"] >= 0
    assert not paths[str(retained.resolve())]["removable"]
    assert "未追跡" in paths[str(retained.resolve())]["reason"]
    assert paths[str(retained.resolve())]["age_seconds"] >= 0
    assert deletable.is_dir() and retained.is_dir()

    with pytest.raises(SystemExit) as ended:
        atk.main(["agents-exit-session"])
    assert ended.value.code == 0
    result = json.loads(capsys.readouterr().out)["lane_cleanup"]
    assert not deletable.exists()
    assert (retained / "notes.txt").is_file()
    code, listed, stderr = _invoke(capsys, "delete", "--list", "--repo", str(repo))
    assert code == 0, stderr
    after = next(item["record"] for item in listed["results"] if item["record"]["result_path"] == result["result_path"])
    kept = {item["worktree"]: item["reason"] for item in after["orphans"]["retained"]}
    assert "未追跡" in kept[str(retained.resolve())]
    assert "ロック" in kept[str(locked.resolve())] and locked.is_dir()


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(4.0, reason="実Gitのworktreeロックを公開CLIの一覧から確認する。")
def test_list_orphans_retains_locked_worktree(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, _selected, _registered = _create(tmp_path, monkeypatch, capsys)
    retained = repo.parent / "locked"
    git_repository.run_git(repo, "worktree", "add", "--lock", "-b", "orch/locked", str(retained))

    code, listed, stderr = _invoke(capsys, "delete", "--list", "--repo", str(repo))

    assert code == 0, stderr
    [item] = [item for item in listed["orphan_candidates"] if item["worktree"] == str(retained.resolve())]
    assert not item["removable"]
    assert "ロック" in item["reason"]


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(4.0, reason="実Gitと公開CLIで未取込みの差分を終了時に保持するか確かめる。")
@pytest.mark.parametrize("case", ["registered", "detached-caller", "explicit", "explicit-wins", "conflicting", "unregistered"])
def test_exit_uses_only_the_explicit_or_recorded_integration_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], case: str
) -> None:
    repo, selected, _registered = _create(tmp_path, monkeypatch, capsys)
    _published_main(repo)
    feature = repo.parent / "feature"
    retained = repo.parent / "not-integrated"
    git_repository.run_git(repo, "worktree", "add", "-b", "feature", str(feature))
    (feature / "feature.txt").write_text("未取込み", encoding="utf-8")
    git_repository.commit_all(feature, "作業中の差分")
    git_repository.run_git(repo, "worktree", "add", "-b", "orch/unmerged", str(retained), "feature")
    git_repository.run_git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/feature")
    if case == "detached-caller":
        git_repository.run_git(feature, "checkout", "--detach")
    elif case in {"conflicting", "explicit-wins"}:
        code, _record, stderr = _invoke(
            capsys,
            "create",
            "--repo",
            str(repo),
            "--base-branch",
            "feature",
            "--lane",
            "lane-02",
            "--selection-file",
            str(selected),
        )
        assert code == 0, stderr
    elif case in {"unregistered", "explicit"}:
        monkeypatch.setenv(lane.SESSION_ENV, "session-without-lane-register")
    monkeypatch.chdir(feature)
    monkeypatch.delenv(OWNER_SESSION_ENV, raising=False)
    monkeypatch.setattr(agents_exit_session, "request_termination", lambda **_kwargs: ("unsupported", None))

    args = ["--repo", str(repo)] if case in {"explicit", "explicit-wins"} else []
    with pytest.raises(SystemExit) as ended:
        atk.main(["agents-exit-session", *args])

    assert ended.value.code == 0
    cleanup = json.loads(capsys.readouterr().out)["lane_cleanup"]
    assert (retained / "feature.txt").read_text(encoding="utf-8") == "未取込み"
    assert git_repository.git_output(repo, "rev-parse", "orch/unmerged") == git_repository.git_output(
        repo, "rev-parse", "feature"
    )
    if case in {"registered", "detached-caller", "explicit", "explicit-wins"}:
        assert "統合先へ含まれない" in cleanup["orphans"]["retained"][0]["reason"]
    elif case == "conflicting":
        assert "統合先が複数" in cleanup["orphan_cleanup_error"]
    else:
        assert "orphans" not in cleanup


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_retains_resources_and_explains_retry_after_ignore_check_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """ignore判定の失敗時は原診断と再開条件を返して成果を保持する。"""
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "output").mkdir()
    original = lane.git_command.run

    def fail_ignore(arguments: Any, *args: Any, **kwargs: Any) -> Any:
        if arguments[0] == "check-ignore":
            return subprocess.CompletedProcess(arguments, 128, stdout="", stderr="fatal: invalid configuration")
        return original(arguments, *args, **kwargs)

    monkeypatch.setattr(lane.git_command, "run", fail_ignore)
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and not held["removed"]
    reason = held["retained"][0]["reason"]
    assert "git check-ignoreが終了128: fatal: invalid configuration" in reason
    assert str(worktree) in reason and "Git設定と読取権限を確認" in reason and "同じlane操作を再実行" in reason
    assert worktree.is_dir() and pathlib.Path(record["record_path"]).is_file()


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_recovers_finalized_lane_after_base_history_is_rewritten(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, record, worktree, new_head = _finalize_lane_for_cleanup(
        tmp_path, monkeypatch, capsys, feature_name="feature.txt", feature_text="統合済みの成果"
    )
    git_repository.run_git(repo, "commit", "--amend", "-m", "書換え後の統合済み成果")
    assert git_repository.git_output(repo, "rev-parse", "HEAD") != new_head

    code, deleted, stderr = _invoke(capsys, "delete")

    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert record["branch"] not in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    assert not worktree.exists()
    assert not pathlib.Path(record["record_path"]).exists()
    assert str(worktree) not in git_repository.git_output(repo, "worktree", "list", "--porcelain")


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_keeps_finalized_lane_with_a_later_commit(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo, record, worktree, _head = _finalize_lane_for_cleanup(
        tmp_path, monkeypatch, capsys, feature_name="integrated.txt", feature_text="統合済み"
    )
    (worktree / "later.txt").write_text("未統合", encoding="utf-8")
    git_repository.commit_all(worktree, "統合後の追加成果")

    code, held, stderr = _invoke(capsys, "delete")

    assert code == 1, stderr
    assert held["retained"]
    assert "統合先branchへ含まれないcommit" in held["retained"][0]["reason"]
    assert "成果を統合してから同じlane deleteを再実行する" in held["retained"][0]["reason"]
    assert pathlib.Path(record["record_path"]).exists()
    assert pathlib.Path(record["worktree"]).exists()


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_recovers_missing_worktree_registration_without_delete_procedure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    marker = tmp_path / "delete-ran"
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps(
            {"delete": [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).touch();sys.exit(17)", str(marker)]}
        ),
        encoding="utf-8",
    )
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    worktree = pathlib.Path(record["worktree"])
    shutil.rmtree(worktree)

    code, deleted, stderr = _invoke(capsys, "delete")

    assert "を実行できない" not in stderr
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert deleted["missing"] == [
        {
            "lane": "lane-01",
            "worktree": str(worktree),
            "branch": record["branch"],
            "last_head": record["initial_head"],
            "skipped_procedure": "delete",
        }
    ]
    assert not marker.exists()
    assert "実体の無いレーン登録を回収した" in stderr


@pytest.mark.parametrize("state", ["unregistered", "unready", "legacy", "recovered"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_recovers_absent_resources_for_each_registration_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], state: str
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    path = pathlib.Path(record["record_path"])
    if state in {"legacy", "recovered"}:
        _legacy(record)
    if state == "recovered":
        code, recovered, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
        assert code == 0 and recovered["recovered"], stderr
    else:
        git_repository.run_git(repo, "worktree", "remove", "--force", record["worktree"])
    git_repository.run_git(repo, "branch", "-D", record["branch"])
    if state == "unready":
        stored = json.loads(path.read_text(encoding="utf-8"))
        stored["ready"] = False
        path.write_text(json.dumps(stored, ensure_ascii=False) + "\n", encoding="utf-8")

    code, deleted, stderr = _invoke(capsys, "delete")

    assert "を実行できない" not in stderr
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert not path.exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXのディレクトリ権限を実機で検証する")
@pytest.mark.parametrize("dirty", [False, True])
@pytest.mark.parametrize("operation", ["delete", "recover"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_recovers_permissions_before_checking_untracked_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], dirty: bool, operation: str
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    locked = worktree / "locked"
    locked.mkdir()
    payload = locked / "payload"
    payload.write_text("成果", encoding="utf-8")
    if not dirty:
        git_repository.commit_all(worktree, "保存した成果")
        git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    if operation == "recover":
        _legacy(record)
    locked.chmod(0)
    try:
        code, _listed, stderr = _invoke(capsys, "delete", "--list")
        assert code == 0, stderr
        assert stat.S_IMODE(locked.stat().st_mode) == 0
        code, result, stderr = _invoke(capsys, operation, "--session-id", "lane-test-session")
        if dirty:
            assert code == 1, stderr
            assert "未commitまたは未追跡" in result["retained"][0]["reason"]
            assert payload.read_text(encoding="utf-8") == "成果"
            assert pathlib.Path(record["record_path"]).exists()
            assert str(worktree) in git_repository.git_output(repo, "worktree", "list", "--porcelain")
        else:
            assert code == 0, stderr
            if operation == "delete":
                assert result["removed"] == ["lane-01"]
                assert not pathlib.Path(record["record_path"]).exists()
            else:
                assert result["recovered"][0]["phase"] == "done"
            assert not worktree.exists()
            assert str(worktree) not in git_repository.git_output(repo, "worktree", "list", "--porcelain")
    finally:
        if locked.exists():
            locked.chmod(0o700)


@pytest.mark.parametrize("operation", ["delete", "exit"])
@pytest.mark.parametrize("output", ["data", "build/data"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_runs_project_teardown_before_preparing_foreign_ignored_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
    output: str,
) -> None:
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps({"delete": [sys.executable, "-c", "import shutil,sys;shutil.rmtree(sys.argv[1])", "{worktree}/" + output]}),
        encoding="utf-8",
    )
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    worktree = pathlib.Path(record["worktree"])
    (worktree / ".gitignore").write_text(f"/{output}/\n", encoding="utf-8")
    git_repository.commit_all(worktree, "生成物を無視する")
    git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    foreign = worktree / output / "mysql"
    foreign.mkdir(parents=True)
    (foreign / "payload").write_text("生成物", encoding="utf-8")
    original_scandir = os.scandir

    @contextlib.contextmanager
    def foreign_owner_entries(path: Any, **kwargs: Any) -> Generator[Iterator[Any]]:
        with original_scandir(path, **kwargs) as entries:
            if not isinstance(path, (str, os.PathLike)) or pathlib.Path(path) != foreign.parent:
                yield entries
                return

            def entry_with_owner(entry: os.DirEntry[str]) -> Any:
                if entry.name != foreign.name:
                    return entry
                observed = entry.stat(follow_symlinks=False)
                fields = list(observed)
                fields[4] = observed.st_uid + 1
                return types.SimpleNamespace(name=entry.name, stat=lambda **_kwargs: os.stat_result(fields))

            yield (entry_with_owner(entry) for entry in entries)

    with monkeypatch.context() as patch:
        patch.setattr(os, "scandir", foreign_owner_entries)
        if operation == "exit":
            patch.setattr(agents_exit_session, "request_termination", lambda **_kwargs: ("unsupported", None))
            with pytest.raises(SystemExit) as ended:
                atk.main(["agents-exit-session"])
            code = ended.value.code
            observed = capsys.readouterr()
            deleted, stderr = json.loads(observed.out)["lane_cleanup"], observed.err
        else:
            code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert not foreign.exists() and not pathlib.Path(record["record_path"]).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXのディレクトリ権限を検証する")
@pytest.mark.parametrize("tracked", [True, False])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_protects_git_content_before_project_teardown(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tracked: bool,
) -> None:
    marker = tmp_path / "teardown-ran"
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps({"delete": [sys.executable, "-c", "import pathlib,sys;pathlib.Path(sys.argv[1]).touch()", str(marker)]}),
        encoding="utf-8",
    )
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    worktree = pathlib.Path(record["worktree"])
    (worktree / ".gitignore").write_text("/data/\n", encoding="utf-8")
    folder = worktree / ("data" if tracked else "untracked") / "nested"
    folder.mkdir(parents=True)
    payload = folder / "result.txt"
    if tracked:
        payload.write_text("保存前", encoding="utf-8")
        git_repository.run_git(worktree, "add", "--force", "data/nested/result.txt")
    git_repository.commit_all(worktree, "保護対象とignoreを保存")
    git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    payload.write_text("未保存の成果", encoding="utf-8")
    folder.chmod(0)
    try:
        code, result, stderr = _invoke(capsys, "delete")
        assert code == 1, stderr
        assert "未commitまたは未追跡" in result["retained"][0]["reason"]
        assert not marker.exists()
        assert payload.read_text(encoding="utf-8") == "未保存の成果"
        assert pathlib.Path(record["record_path"]).exists()
    finally:
        folder.chmod(0o700)


@pytest.mark.skipif(os.name != "posix", reason="POSIXの所有者保護を検証する")
@pytest.mark.parametrize("teardown", ["none", "failure", "leftover"])
@pytest.mark.usefixtures("idle_lane")
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_delete_keeps_foreign_ignored_output_until_teardown_is_resolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    teardown: str,
) -> None:
    """固有撤去の未設定・失敗・撤去漏れは資源を保持し、原因解消後の同じdeleteで再開する。"""
    script = tmp_path / "delete.py"
    script.write_text("raise SystemExit(7)\n" if teardown == "failure" else "pass\n", encoding="utf-8")
    commands = tmp_path / "commands.json"
    commands.write_text(json.dumps({"delete": [sys.executable, str(script), "{worktree}/data"]}), encoding="utf-8")
    extra = ("--commands-file", str(commands)) if teardown != "none" else ()
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys, *extra)
    worktree = pathlib.Path(record["worktree"])
    (worktree / ".gitignore").write_text("/data/\n", encoding="utf-8")
    git_repository.commit_all(worktree, "生成物を無視する")
    git_repository.run_git(repo, "merge", "--ff-only", record["branch"])
    foreign = worktree / "data" / "mysql"
    foreign.mkdir(parents=True)
    (foreign / "payload").write_text("生成物", encoding="utf-8")
    original_owner_check = lane.temp_permissions._require_owner  # noqa: SLF001  # pylint: disable=protected-access

    def require_owner(metadata: os.stat_result, path: pathlib.Path | str) -> None:
        if pathlib.Path(path) == foreign:
            raise managed_temp.ManagedTempError(
                "所有者が異なる: mysql (uid=999)", next_action="所有者uid=999で生成物を撤去する"
            )
        original_owner_check(metadata, path)

    monkeypatch.setattr(lane.temp_permissions, "_require_owner", require_owner)
    code, retained, stderr = _invoke(capsys, "delete")
    assert code == 1, stderr
    assert retained["retained"][0]["reason"]
    assert retained["retained"][0]["next_action"]
    assert foreign.exists() and pathlib.Path(record["record_path"]).exists()
    assert record["branch"] in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    if teardown == "failure":
        script.write_text("import shutil,sys\nshutil.rmtree(sys.argv[1])\n", encoding="utf-8")
    else:
        shutil.rmtree(worktree / "data")
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert not worktree.exists() and not pathlib.Path(record["record_path"]).exists()


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_recover_reaches_remove_hook_with_foreign_ignored_output(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    destination = tmp_path / "hook-created"
    script = tmp_path / "hook.py"
    script.write_text(
        "import json,pathlib,shutil,subprocess,sys\n"
        "p=json.load(sys.stdin)\n"
        "if p['hook_event_name']=='WorktreeCreate':\n"
        " subprocess.run(['git','worktree','add','-b',p['name'],sys.argv[1]],check=True,stdout=sys.stderr)\n"
        " print(sys.argv[1])\n"
        "else:\n"
        " shutil.rmtree(pathlib.Path(sys.argv[1],'data'))\n"
        " subprocess.run(['git','worktree','remove',sys.argv[1]],check=True)\n",
        encoding="utf-8",
    )
    argv = [sys.executable, str(script), str(destination)]
    command = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    settings = repo / ".claude/settings.local.json"
    settings.parent.mkdir()
    settings.write_text(
        json.dumps(
            {
                "hooks": {
                    event: [{"hooks": [{"type": "command", "command": command}]}]
                    for event in ("WorktreeCreate", "WorktreeRemove")
                }
            }
        ),
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("/data/\n", encoding="utf-8")
    git_repository.commit_all(repo, "撤去hookと無視対象を保存")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    code, record, stderr = _create_in(capsys, monkeypatch, "lane-test-session", repo, selected)
    assert code == 0, stderr
    foreign = destination / "data" / "mysql"
    foreign.mkdir(parents=True)
    (foreign / "payload").write_text("生成物", encoding="utf-8")
    _legacy(record)

    original_owner_check = lane.temp_permissions._require_owner  # noqa: SLF001  # pylint: disable=protected-access

    def require_owner(metadata: os.stat_result, path: pathlib.Path | str) -> None:
        if pathlib.Path(path) == foreign:
            raise managed_temp.ManagedTempError("所有者が異なる: mysql (uid=999)")
        original_owner_check(metadata, path)

    monkeypatch.setattr(lane.temp_permissions, "_require_owner", require_owner)
    code, recovered, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 0, stderr
    assert not destination.exists()
    assert recovered["recovered"][0]["head"] == record["initial_head"]
    assert git_repository.git_output(repo, "rev-parse", f"refs/heads/{record['branch']}") == record["initial_head"]
    assert pathlib.Path(record["record_path"]).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIXの所有者とmodeを検証する")
@pytest.mark.parametrize("operation", ["delete", "recover"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_lane_keeps_foreign_os_owner_and_reports_owner_action(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], operation: str
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    locked = worktree / "foreign-owner"
    locked.mkdir()
    payload = locked / "payload"
    payload.write_text("保持する成果", encoding="utf-8")
    metadata = locked.lstat()
    foreign_uid = metadata.st_uid + 1
    locked.chmod(0)
    if operation == "recover":
        _legacy(record)
    original_scandir = os.scandir

    @contextlib.contextmanager
    def foreign_owner_entries(path: Any, **kwargs: Any) -> Generator[Iterator[Any]]:
        with original_scandir(path, **kwargs) as entries:

            def entry_with_owner(entry: os.DirEntry[str]) -> Any:
                observed = entry.stat(follow_symlinks=False)
                if (observed.st_dev, observed.st_ino) != (metadata.st_dev, metadata.st_ino):
                    return entry
                fields = list(observed)
                fields[4] = foreign_uid
                return types.SimpleNamespace(name=entry.name, stat=lambda **_kwargs: os.stat_result(fields))

            yield (entry_with_owner(entry) for entry in entries)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "scandir", foreign_owner_entries)
            code, result, stderr = _invoke(capsys, operation, "--session-id", "lane-test-session")
        assert code == 1, stderr
        retained = result["retained"][0]
        assert str(locked) in retained["reason"] and f"uid={foreign_uid}" in retained["reason"]
        assert str(locked) in retained["next_action"] and f"uid={foreign_uid}" in retained["next_action"]
        assert "所有者" in retained["next_action"]
        assert stat.S_IMODE(locked.stat().st_mode) == 0
        assert pathlib.Path(record["record_path"]).exists()
        assert str(worktree) in git_repository.git_output(repo, "worktree", "list", "--porcelain")
    finally:
        locked.chmod(0o700)
    assert payload.read_text(encoding="utf-8") == "保持する成果"


@pytest.mark.usefixtures("idle_lane")
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_project_teardown_failure_preserves_record_and_retries_remaining_cleanup(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """固有撤去の失敗後も資源と記録を失わず、成功後の回収へ進める。"""
    permit = tmp_path / "allow-delete"
    commands = tmp_path / "commands.json"
    commands.write_text(
        json.dumps(
            {
                "delete": [
                    sys.executable,
                    "-c",
                    "import pathlib,sys;sys.exit(0 if pathlib.Path(sys.argv[1]).exists() else 3)",
                    str(permit),
                ]
            }
        ),
        encoding="utf-8",
    )
    _repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"] and pathlib.Path(record["record_path"]).is_file()
    permit.touch()
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


@pytest.mark.usefixtures("idle_lane")
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_cleanup_rechecks_git_registration_after_project_teardown(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commands = tmp_path / "commands.json"
    commands.write_text(json.dumps({"delete": ["git", "-C", "{worktree}", "switch", "-c", "different"]}), encoding="utf-8")
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys, "--commands-file", str(commands))
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and "worktreeの登録" in held["retained"][0]["reason"]
    worktree = pathlib.Path(record["worktree"])
    assert worktree.is_dir() and pathlib.Path(record["record_path"]).is_file()
    git_repository.run_git(worktree, "switch", record["branch"])
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]
    assert "different" in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()


@pytest.mark.parametrize("registration", ["retained", "pruned"])
@pytest.mark.parametrize("branch", ["absent", "reachable", "unmerged"])
@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_missing_worktree_cleanup_distinguishes_branch_state(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    registration: str,
    branch: str,
) -> None:
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    record_path = pathlib.Path(record["record_path"])
    if branch == "unmerged":
        git_repository.git_output(worktree, "commit", "--allow-empty", "-m", "保持する未統合成果")
    branch_head = git_repository.git_output(repo, "rev-parse", record["branch"])
    original_record = record_path.read_bytes()
    shutil.rmtree(worktree)
    if registration == "pruned":
        git_repository.git_output(repo, "worktree", "prune", "--expire", "now")
    if branch == "absent":
        git_repository.git_output(repo, "update-ref", "-d", "refs/heads/" + record["branch"])

    code, result, stderr = _invoke(capsys, "delete")

    assert "を実行できない" not in stderr
    assert "を実行できない" not in json.dumps(result, ensure_ascii=False)
    assert not worktree.exists()
    if branch == "unmerged":
        assert code == 1
        assert not result["removed"]
        assert len(result["retained"]) == 1
        assert "統合先" in result["retained"][0]["reason"]
        assert "含まれないcommit" in result["retained"][0]["reason"]
        assert "再実行" in result["retained"][0]["reason"]
        assert "終了1" not in result["retained"][0]["reason"]
        assert record_path.read_bytes() == original_record
        assert git_repository.git_output(repo, "rev-parse", record["branch"]) == branch_head
    else:
        assert code == 0, stderr
        assert result["removed"] == ["lane-01"]
        assert not result["retained"]
        assert not record_path.exists()
        assert result["missing"][0]["last_head"] == record["initial_head"]
        assert "lane=lane-01" in stderr
        assert record["initial_head"] in stderr
        assert str(worktree) not in git_repository.git_output(repo, "worktree", "list", "--porcelain")
        assert record["branch"] not in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "実Gitの履歴・所有作業先・公開CLIでしか判定できない状態を最小構成" + "で確認する。同群Linux CI所有CPU最大0.426秒。"
    ),
)
def test_missing_worktree_query_error_keeps_owned_resources(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Git照会の失敗は未統合の説明へ混ぜず、所有登録と残ったbranchを保持する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    record_path = pathlib.Path(record["record_path"])
    original_record = record_path.read_bytes()
    branch_head = git_repository.git_output(repo, "rev-parse", record["branch"])
    shutil.rmtree(worktree)
    original_run = lane.git_command.run

    def query_error(arguments: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if arguments[:2] == ["merge-base", "--is-ancestor"]:
            return subprocess.CompletedProcess(arguments, 128, "", "照会に失敗する対照")
        return original_run(arguments, *args, **kwargs)

    monkeypatch.setattr(lane.git_command, "run", query_error)

    code, result, stderr = _invoke(capsys, "delete")

    assert code == 1
    assert not result["removed"]
    reason = result["retained"][0]["reason"]
    assert "終了128" in reason
    assert "照会に失敗する対照" in reason
    assert "含まれないcommit" not in reason
    assert "を実行できない" not in reason
    assert "を実行できない" not in stderr
    assert record_path.read_bytes() == original_record
    assert git_repository.git_output(repo, "rev-parse", record["branch"]) == branch_head


@pytest.mark.parametrize("kind", ["plain", "generated", "mixed", "nested-note", "recorded-share", "edited-share"])
@pytest.mark.cpu_limit(2.0, reason="登録済みレーンを実Gitの無視設定と公開CLIで削除か保持する。")
def test_selected_lane_retains_unknown_ignored_directory(tmp_path, monkeypatch, capsys, kind):
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    root = pathlib.Path(record["worktree"])
    with (repo / ".git" / "info" / "exclude").open("a", encoding="utf-8") as exclude:
        exclude.write("\n/.venv/\n")
    environment = root / ".venv"
    environment.mkdir()
    if kind != "plain":
        (environment / "pyvenv.cfg").write_text("home = environment\n", encoding="ascii")
    item = environment / ("CACHEDIR.TAG" if kind == "generated" else "data")
    if kind in {"nested-note", "recorded-share", "edited-share"}:
        item = environment / "share" / "payload"
        item.parent.mkdir()
    if kind in {"recorded-share", "edited-share"}:
        installed = environment / "Lib" / "site-packages" / "demo.dist-info" / "RECORD"
        installed.parent.mkdir(parents=True)
        encoded = base64.urlsafe_b64encode(hashlib.sha256(b"data").digest()).decode("ascii").rstrip("=")
        installed.write_text(f"demo.dist-info/RECORD,,\n../../share/payload,sha256={encoded},4\n", encoding="ascii")
    item.write_text("edit" if kind == "edited-share" else "data", encoding="utf-8")

    status, result, stderr = _invoke(capsys, "delete")
    if kind in {"generated", "recorded-share"}:
        assert status == 0 and result["removed"] == ["lane-01"], stderr
        assert not root.exists()
    else:
        assert status == 1 and "無視対象" in result["retained"][0]["reason"], stderr
        assert item.is_file()


def test_packaged_cleanup_preserves_projection_without_manifest(tmp_path):
    from agent_toolkit._atk import lane_cleanup  # pylint: disable=import-outside-toplevel

    root = tmp_path / "agent-toolkit-codex"
    root.mkdir()
    (root / "GENERATED.md").write_text("# 自動生成ファイル\n", encoding="utf-8")
    assert not lane_cleanup._registered_generated_directory(tmp_path, root.name + "/")  # pylint: disable=protected-access
