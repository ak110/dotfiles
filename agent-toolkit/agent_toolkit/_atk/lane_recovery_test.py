"""lane recoveryの契約を検証する。"""

from __future__ import annotations

import json
import pathlib
import subprocess
from typing import Any

import pytest

from agent_toolkit._atk import lane
from agent_toolkit._atk import lane_records as _lane_records
from agent_toolkit._common import session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing.lane_integration_support import _integration_args, _integration_selection, _legacy, _selection_with
from agent_toolkit._testing.lane_support import _create, _create_args, _create_in, _invoke, _selection


@pytest.mark.parametrize("integrated", [True, False])
def test_delete_uses_held_selection_after_selection_file_expires(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], integrated: bool
) -> None:
    """選定原本が失効しても、所有登録が保持した統合状態で回収と保持を判定する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _selection(selected, integrated=integrated)
    code, _held, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    selected.unlink()
    code, result, stderr = _invoke(capsys, "delete")
    if integrated:
        assert code == 0 and result["removed"] == ["lane-01"], stderr
        assert not pathlib.Path(record["worktree"]).exists()
    else:
        assert code == 1 and result["retained"][0]["reason"] == "選定の統合状態が未統合"
        assert pathlib.Path(record["worktree"]).is_dir() and pathlib.Path(record["record_path"]).is_file()


def test_integrate_finalizes_and_cleans_after_selection_file_expires(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合の前に選定原本が失効しても、保持したWI集合とmanifestを比べ、最終化と終了時回収まで完了する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    # 選定の確定後に作業場所を開く通常の順序に合わせ、確定した選定を所有登録へ取り込む。
    code, _reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    worktree = pathlib.Path(record["worktree"])
    (worktree / "feature.txt").write_text("成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "成果")
    handoff = tmp_path / "handoff.md"
    handoff.write_text("検収済み採否", encoding="utf-8")
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "verification": [],
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    args = _integration_args(record, repo, manifest, head)
    correct = manifest.read_text(encoding="utf-8")
    wrong = json.loads(correct)
    wrong["wis"] = [{"name": "other.md", "commands": []}]
    manifest.write_text(json.dumps(wrong), encoding="utf-8")
    selected.unlink()
    code, _result, stderr = _invoke(capsys, *args)
    assert code == 2 and "WI集合" in stderr
    manifest.write_text(correct, encoding="utf-8")
    code, result, stderr = _invoke(capsys, *args)
    assert code == 0 and result["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == head
    code, deleted, stderr = _invoke(capsys, "delete")
    assert code == 0 and deleted["removed"] == ["lane-01"], stderr


@pytest.mark.parametrize("entity", ["present", "missing"])
def test_recover_releases_legacy_registration_and_keeps_branch(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], entity: str
) -> None:
    """旧形式の登録は元branchの成果を残して作成先を解放し、別sessionが同じ固定作成先へ作成できる。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    (worktree / "unmerged.txt").write_text("未統合の成果", encoding="utf-8")
    head = git_repository.commit_all(worktree, "未統合の成果")
    _legacy(record)
    if entity == "missing":
        # 作業ツリーの実体だけが失われ、Gitの登録が残った状態。
        subprocess.run(["rm", "-rf", str(worktree)], check=True)
    code, held, stderr = _invoke(capsys, "delete")
    assert code == 1 and "atk lane recover --session-id lane-test-session --lane lane-01" in held["retained"][0]["reason"]
    code, recovered, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 0, stderr
    assert recovered["recovered"][0]["branch"] == record["branch"]
    assert recovered["recovered"][0]["head"] == head and recovered["recovered"][0]["phase"] == "done"
    assert git_repository.git_output(repo, "rev-parse", f"refs/heads/{record['branch']}") == head
    assert str(worktree.resolve()) not in git_repository.git_output(repo, "worktree", "list", "--porcelain")
    _selection(selected)
    code, created, stderr = _create_in(capsys, monkeypatch, "next-session", repo, selected)
    assert code == 0 and created["worktree"] == str(worktree), stderr
    code, listed, stderr = _invoke(capsys, "delete", "--list")
    assert code == 0, stderr
    old = next(entry["record"] for entry in listed["records"] if entry["record"]["session_id"] == "lane-test-session")
    assert old["recovery"]["branch"] == record["branch"] and old["recovery"]["head"] == head
    monkeypatch.setenv(lane.SESSION_ENV, "lane-test-session")
    code, cleaned, stderr = _invoke(capsys, "delete")
    assert code == 0 and cleaned["recovered"][0]["head"] == head and not cleaned["removed"], stderr
    code, repeated, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 0 and repeated["recovered"][0]["head"] == head, stderr


def test_recover_keeps_registrations_with_selection_information(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """選定原本か保持した選定情報を持つ登録は回復せず、通常の回収と再利用を案内する。"""
    _repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    before = pathlib.Path(record["record_path"]).read_bytes()
    for _ in range(2):
        code, result, _stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
        assert code == 1 and "回復の対象外" in result["retained"][0]["reason"]
        assert pathlib.Path(record["record_path"]).read_bytes() == before
        assert pathlib.Path(record["worktree"]).is_dir()
        # 2回目は原本だけが失効し、所有登録の保持値が残る場合を確かめる。
        selected.unlink(missing_ok=True)


@pytest.mark.parametrize("condition", ["dirty", "active-session", "foreign-owner", "partial-failure"])
def test_recover_protects_unfinished_work_and_resumes_after_failure(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], condition: str
) -> None:
    """dirty・稼働中・所有不一致の登録を保持し、途中失敗は同じ回復操作で残りから再開する。"""
    repo, _selected, record = _create(tmp_path, monkeypatch, capsys)
    worktree = pathlib.Path(record["worktree"])
    _legacy(record)
    record_path = pathlib.Path(record["record_path"])
    if condition == "dirty":
        (worktree / "unfinished.txt").write_text("未commitの成果", encoding="utf-8")
    elif condition == "active-session":
        owner = session_registry.owner_process()
        assert owner is not None
        session_registry.publish(
            "child", state=session_registry.PublicationState.PENDING_RUNNING, cwd=str(worktree), processes=(owner,)
        )
    elif condition == "foreign-owner":
        stored = json.loads(record_path.read_text(encoding="utf-8"))
        stored["session_id"] = "another-session"
        record_path.write_text(json.dumps(stored), encoding="utf-8")
    else:
        original = lane._git  # pylint: disable=protected-access  # noqa: SLF001

        def _fail_once(repo_path: pathlib.Path, *argv: str, **kwargs: Any) -> str:
            del kwargs
            if argv[:2] == ("worktree", "remove"):
                monkeypatch.setattr(_lane_records, "git", original)
                raise ValueError("git worktreeが終了1: 模擬した失敗")
            return original(repo_path, *argv)

        monkeypatch.setattr(_lane_records, "git", _fail_once)
    code, result, _stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
    assert code == 1 and result["retained"]
    assert worktree.is_dir()
    stored = json.loads(record_path.read_text(encoding="utf-8"))
    if condition == "partial-failure":
        assert stored["recovery"]["phase"] == "started"
        code, result, stderr = _invoke(capsys, "recover", "--session-id", "lane-test-session")
        assert code == 0 and result["recovered"][0]["phase"] == "done", stderr
        assert not worktree.exists()
        assert record["branch"] in git_repository.git_output(repo, "branch", "--format=%(refname:short)").splitlines()
    else:
        assert "recovery" not in stored


def test_reuse_switches_held_selection_to_current_selection(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再利用では前回の選定を引き継がず、今回の選定のWI集合と統合状態を保持する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _selection_with(selected, ["previous.md"])
    code, _reused, stderr = _invoke(capsys, *_create_args(repo, selected))
    assert code == 0, stderr
    current = tmp_path / "current-selection.yaml"
    _selection_with(current, ["current.md"], integrated=False)
    code, resumed, stderr = _create_in(
        capsys, monkeypatch, "next-session", repo, current, "--reuse-record", record["record_path"]
    )
    assert code == 0, stderr
    stored = json.loads(pathlib.Path(resumed["record_path"]).read_text(encoding="utf-8"))
    assert stored["selection"] == {"lane_wis": ["current.md"], "integrated": False}
    current.unlink()
    code, held, _stderr = _invoke(capsys, "delete")
    assert code == 1 and held["retained"][0]["reason"] == "選定の統合状態が未統合"
