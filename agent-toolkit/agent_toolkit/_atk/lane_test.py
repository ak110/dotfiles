"""公開CLIからレーンの所有、保持、統合、終了時回収と再実行を確かめる。"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._atk import agents_exit_session, lane
from agent_toolkit._atk.wi import process_loop_session
from agent_toolkit._common import session_launchers, state_paths
from agent_toolkit._testing import git_repository


def _invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any], str]:
    with pytest.raises(SystemExit) as ended:
        atk.main(["lane", *args])
    output = capsys.readouterr()
    assert isinstance(ended.value.code, int)
    return ended.value.code, json.loads(output.out) if output.out.strip() else {}, output.err


def _create_args(repo: pathlib.Path, selected: pathlib.Path) -> tuple[str, ...]:
    return ("create", "--repo", str(repo), "--base-branch", "main", "--lane", "lane-01", "--selection-file", str(selected))


def _selection(path: pathlib.Path, *, integrated: bool = True) -> None:
    path.write_text(
        yaml.safe_dump({"レーンの所要時間": [{"レーン": "lane-01", "統合状態": "統合済み" if integrated else "未統合"}]}),
        encoding="utf-8",
    )


def _create(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *extra: str,
) -> tuple[pathlib.Path, pathlib.Path, dict[str, Any]]:
    monkeypatch.setenv(lane.SESSION_ENV, "lane-test-session")
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    selected = tmp_path / "selection.yaml"
    _selection(selected)
    code, record, stderr = _invoke(
        capsys,
        *_create_args(repo, selected),
        *extra,
    )
    assert code == 0, stderr
    assert record["ready"]
    assert pathlib.Path(record["record_path"]).is_file()
    return repo, selected, record


def test_create_reuses_integrated_resources_then_delete_removes_only_owned_lane(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合後の再開は同じ資源を使い、終了時だけ所有資源を削除する。"""
    repo, selected, first = _create(tmp_path, monkeypatch, capsys)
    foreign = tmp_path / "foreign-worktree"
    git_repository.run_git(repo, "worktree", "add", "-b", "foreign-branch", str(foreign))
    code, reused, stderr = _invoke(
        capsys,
        *_create_args(repo, selected),
    )
    assert code == 0, stderr
    assert reused["reused"] and reused["worktree"] == first["worktree"]
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"] and not deleted["retained"]
    assert not pathlib.Path(first["worktree"]).exists()
    assert not pathlib.Path(first["managed_temp"]).exists()
    assert not pathlib.Path(first["record_path"]).exists()
    assert foreign.is_dir() and repo.is_dir() and selected.is_file()
    branches = git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    assert first["branch"] not in branches and "foreign-branch" in branches
    assert pathlib.Path(deleted["result_path"]).is_file()
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    assert not listed["records"] and listed["results"][0]["record"]["removed"] == ["lane-01"]
    code, repeated, _stderr = _invoke(capsys, "delete")
    assert code == 0 and not repeated["removed"]
    assert repeated["result_path"] != deleted["result_path"]
    assert json.loads(pathlib.Path(deleted["result_path"]).read_text(encoding="utf-8"))["removed"] == ["lane-01"]


@pytest.mark.parametrize("condition", ["unintegrated", "dirty", "unmerged", "active-session"])
def test_delete_preserves_unfinished_work_and_recovers_after_condition_is_resolved(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """判定不能・未完了を削除せず、成立した後は同じ公開操作で回収する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    registry = session_launchers.registry_directory(state_paths.state_dir()) / "child.json"
    if condition == "unintegrated":
        _selection(selected, integrated=False)
    elif condition == "dirty":
        (worktree / "unfinished.txt").write_text("未commitの成果", encoding="utf-8")
    elif condition == "unmerged":
        git_repository.commit_all(worktree, "未統合の成果")
    else:
        registry.parent.mkdir(parents=True, exist_ok=True)
        # session_registry.publishが生成するcwd・terminalの契約から入力を作成する。
        registry.write_text(json.dumps({"session_id": "child", "cwd": str(worktree), "terminal": False}), encoding="utf-8")
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
        registry.write_text(json.dumps({"session_id": "child", "cwd": str(worktree), "terminal": True}), encoding="utf-8")
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
    """担当が終端しても、その作業先を使う外部プロセスを削除で巻き込まない。"""
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


def test_cleanup_rechecks_git_registration_after_project_teardown(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """固有手順の後にbranchが変わった作業先を削除せず、元の登録へ戻った後だけ回収する。"""
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


@pytest.mark.parametrize("phase", ["create", "prepare"])
def test_failed_preparation_recovers_using_the_same_registered_resource(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], phase: str
) -> None:
    """作成前と環境準備の失敗を登録へ残し、入力を是正して同じ資源から再開する。"""
    monkeypatch.setenv(lane.SESSION_ENV, "preparation-session")
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="基準")
    selected, commands = tmp_path / "selection.yaml", tmp_path / "commands.json"
    _selection(selected)
    commands.write_text(json.dumps({phase: [sys.executable, "-c", "raise SystemExit(3)"]}), encoding="utf-8")
    args = (*_create_args(repo, selected), "--commands-file", str(commands))
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and f"{phase}手順が終了3" in stderr
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    original = listed["records"][0]["record"]
    assert not original["ready"] and pathlib.Path(original["record_path"]).is_file()
    commands.write_text("{}", encoding="utf-8")
    code, resumed, stderr = _invoke(capsys, *args)
    assert code == 0, stderr
    assert resumed["ready"] and resumed["reused"]
    assert resumed["worktree"] == original["worktree"] and resumed["branch"] == original["branch"]
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


def test_interrupted_lane_can_transfer_to_new_session_without_losing_uncommitted_work(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """終端済み担当の資源を新しい所有sessionへ再登録し、中断した成果を保持する。"""
    repo, selected, original = _create(tmp_path, monkeypatch, capsys)
    unfinished = pathlib.Path(original["worktree"]) / "unfinished.txt"
    unfinished.write_text("再開する成果", encoding="utf-8")
    monkeypatch.setenv(lane.SESSION_ENV, "resumed-session")
    code, resumed, stderr = _invoke(capsys, *_create_args(repo, selected), "--reuse-record", original["record_path"])
    assert code == 0, stderr
    assert resumed["session_id"] == "resumed-session" and resumed["worktree"] == original["worktree"]
    assert unfinished.read_text(encoding="utf-8") == "再開する成果"
    assert not pathlib.Path(original["record_path"]).exists()
    assert pathlib.Path(resumed["record_path"]).is_file()
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"]
    git_repository.commit_all(pathlib.Path(resumed["worktree"]), "再開した成果")
    git_repository.run_git(repo, "merge", "--ff-only", resumed["branch"])
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0, stderr
    assert deleted["removed"] == ["lane-01"]


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
def test_process_loop_child_exit_cleans_before_normal_or_abnormal_classification(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, engine: str, exit_code: int
) -> None:
    """両engineの子終了境界で同じ回収を実行し、異常終了後も判定より前に資源を保全する。"""
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
        "        atk.main(['lane','create','--repo',sys.argv[1],'--base-branch','main',"
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
            dotfiles_root=None,
        )

    if exit_code:
        with pytest.raises(SystemExit) as ended:
            execute()
        assert ended.value.code == exit_code
    else:
        assert not execute()
    created = json.loads(snapshot.read_text(encoding="utf-8"))
    assert not pathlib.Path(created["worktree"]).exists() and not pathlib.Path(created["record_path"]).exists()
    assert repo.is_dir() and selected.is_file()
