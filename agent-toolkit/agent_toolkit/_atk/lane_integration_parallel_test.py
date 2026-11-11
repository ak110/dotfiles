"""公開CLIから、レーンの統合が統合先HEADの更新だけを直列にし、準備・検証・記録の確定を並行させることを確かめる。

一方のレーンを別プロセスで起動し、その検証か最終化の途中で他方のレーンの統合が終わることを印ファイルで観測する。
統合の全区間を共通の排他で直列にする実装では、他方が先行の終了を待つため、先行の待機が時間切れで失敗する。
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import threading
import time
from typing import Any

import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._atk import lane
from agent_toolkit._atk.lane_test import integration_args
from agent_toolkit._testing import git_repository, wi_entry_files

_SESSION = "lane-parallel-session"
# レーン数の上限（候補WI数から決まる）が2以上になるよう、lane-01へ10件、lane-02へ1件を割り当てる。
_WIS = {
    "lane-01": [f"20261111-000000-{index:03d}.md" for index in range(1, 11)],
    "lane-02": ["20261111-000000-011.md"],
}
_WAIT_SECONDS = 20
"""印ファイルを待つ上限秒数。正常時は数秒で揃う。試験全体の時間上限（60秒）より短くし、待機の失敗を診断として返す。"""


def _invoke(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict[str, Any], str]:
    with pytest.raises(SystemExit) as ended:
        atk.main(["lane", *args])
    output = capsys.readouterr()
    assert isinstance(ended.value.code, int)
    return ended.value.code, json.loads(output.out) if output.out.strip() else {}, output.err


def _prepare(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> tuple[pathlib.Path, dict[str, dict[str, Any]]]:
    """2レーンを同じ所有sessionで作成し、各レーンに1commitの成果を置く。"""
    monkeypatch.setenv(lane.SESSION_ENV, _SESSION)
    repo = git_repository.init_repository(tmp_path / "repo", initial_branch="main", commit_message="初期状態")
    # WIの所属と統合先の識別子を揃え、模擬remoteへの外部fetchは行わない。
    git_repository.run_git(repo, "remote", "add", "origin", "https://github.com/test/repo.git")
    git_repository.run_git(repo, "config", "remote.origin.skipFetchAll", "true")
    notes = tmp_path / "notes"
    for name in (wi for wis in _WIS.values() for wi in wis):
        wi_entry_files.entry_path(notes, name, state="processing").write_text(
            "---\ntype: awi\nstate: processing\nsource: test\ntarget_repo: github.com/test/repo\n---\n\n# 観測\n\n"
            "## 反映内容と反映先\n\n観測を保存する。\n\n## 完成条件\n\n観測を完了する。\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    selected = tmp_path / "selection.yaml"
    selected.write_text(
        yaml.safe_dump(
            {
                "選定": [
                    {"WI": wi, "レーン": lane_id, "書込対象": [], "鮮度": {"status": "current", "later_commit_count": 0}}
                    for lane_id, wis in _WIS.items()
                    for wi in wis
                ],
                "見込みの補正": {"係数": 1, "参照した記録": [], "除外した記録": []},
                "レーンの所要時間": [
                    {
                        "レーン": lane_id,
                        "統合状態": "未統合",
                        "実装秒数": 60,
                        "統合秒数": 60,
                        "補正前の実装秒数": 60,
                        "補正前の統合秒数": 60,
                        "根拠": "隔離した観測試験",
                    }
                    for lane_id in _WIS
                ],
                "初回配分": {
                    "候補WI": [wi for wis in _WIS.values() for wi in wis],
                    "レーン割当": {wi: lane_id for lane_id, wis in _WIS.items() for wi in wis},
                    "不可分成分": [{"WI": [wi], "結合条件": []} for wis in _WIS.values() for wi in wis],
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    records: dict[str, dict[str, Any]] = {}
    for lane_id in _WIS:
        args = ("create", "--repo", str(repo), "--base-branch", "main", "--lane", lane_id, "--selection-file", str(selected))
        code, record, stderr = _invoke(capsys, *args)
        assert code == 0, stderr
        worktree = pathlib.Path(record["worktree"])
        (worktree / f"{lane_id}.txt").write_text(lane_id, encoding="utf-8")
        record["reviewed_head"] = git_repository.commit_all(worktree, f"{lane_id}の成果")
        records[lane_id] = record
    return repo, records


def _manifest(path: pathlib.Path, lane_id: str, *, verification: list[list[str]], wi_commands: list[list[str]]) -> pathlib.Path:
    handoff = path.with_suffix(".handoff.md")
    handoff.write_text("並行統合の観測", encoding="utf-8")
    path.write_text(
        json.dumps(
            {
                "plans": [],
                # 先頭のWIだけに終了argvを置き、残りは延期として扱う。
                "wis": [{"name": wi, "commands": wi_commands if index == 0 else []} for index, wi in enumerate(_WIS[lane_id])],
                "verification": verification,
                "handoff": str(handoff),
            }
        ),
        encoding="utf-8",
    )
    return path


def _barrier(started: pathlib.Path, released: pathlib.Path) -> list[str]:
    """開始の印を書き、解放の印が現れるまで待つargv。上限までに現れなければ終了1とする。"""
    program = (
        "import pathlib,sys,time\n"
        "pathlib.Path(sys.argv[1]).write_text('started'); deadline=time.monotonic()+float(sys.argv[3])\n"
        "while time.monotonic()<deadline:\n"
        "    if pathlib.Path(sys.argv[2]).exists(): raise SystemExit(0)\n"
        "    time.sleep(0.1)\n"
        "raise SystemExit(1)\n"
    )
    return [sys.executable, "-c", program, str(started), str(released), str(_WAIT_SECONDS)]


def _start_first(arguments: tuple[str, ...], started: pathlib.Path, tmp_path: pathlib.Path) -> subprocess.Popen[bytes]:
    """先行レーンの統合を別プロセスで起動し、待機の区間へ入ったことを確かめる。"""
    # 子は複製したファイル記述子へ書くため、親の側は起動の直後に閉じてよい。
    with (tmp_path / "first.stdout").open("wb") as stdout, (tmp_path / "first.stderr").open("wb") as stderr:
        process = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, str(pathlib.Path(atk.__file__).resolve()), "lane", *arguments],
            stdout=stdout,
            stderr=stderr,
            env=os.environ.copy(),
        )
    deadline = time.monotonic() + _WAIT_SECONDS
    while not started.exists():
        assert process.poll() is None, (tmp_path / "first.stderr").read_text(encoding="utf-8")
        assert time.monotonic() < deadline, "先行レーンが待機の区間へ入らなかった"
        time.sleep(0.1)
    return process


def _finish_first(process: subprocess.Popen[bytes], tmp_path: pathlib.Path) -> tuple[int, dict[str, Any], str]:
    code = process.wait(timeout=_WAIT_SECONDS)
    out = (tmp_path / "first.stdout").read_text(encoding="utf-8")
    return code, json.loads(out) if out.strip() else {}, (tmp_path / "first.stderr").read_text(encoding="utf-8")


def test_second_lane_integrates_while_first_lane_verifies(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """一方の検証の実行中に他方が統合を終え、先行側は統合先の前進を検出して更新しない。"""
    repo, records = _prepare(tmp_path, monkeypatch, capsys)
    started, released = tmp_path / "first-verification.started", tmp_path / "second.done"
    first_manifest = _manifest(tmp_path / "first.json", "lane-01", verification=[_barrier(started, released)], wi_commands=[])
    first_args = integration_args(records["lane-01"], repo, first_manifest, records["lane-01"]["reviewed_head"])
    process = _start_first(first_args, started, tmp_path)
    try:
        second_manifest = _manifest(tmp_path / "second.json", "lane-02", verification=[], wi_commands=[])
        code, second, stderr = _invoke(
            capsys, *integration_args(records["lane-02"], repo, second_manifest, records["lane-02"]["reviewed_head"])
        )
        assert code == 0 and second["phase"] == "finalized", stderr
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == records["lane-02"]["reviewed_head"]
    finally:
        released.write_text("done", encoding="utf-8")
        code, first, stderr = _finish_first(process, tmp_path)
    # 先行側の検証は後続の統合の完了を観測して成功し、短い更新で統合先の前進を検出して更新しない。
    assert code == 1 and first["phase"] == "verified", stderr
    assert first["target_advanced"]["actual"] == records["lane-02"]["reviewed_head"]
    assert "--restart-review" in first["next_action"]
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == records["lane-02"]["reviewed_head"]
    # 既存の--restart-reviewで最新の統合先へ追随し、rebase結果の検収待ちへ戻る。
    code, restarted, stderr = _invoke(
        capsys,
        *integration_args(
            records["lane-01"],
            repo,
            _manifest(first_manifest, "lane-01", verification=[[sys.executable, "-c", "pass"]], wi_commands=[]),
            records["lane-01"]["reviewed_head"],
        ),
        "--restart-review",
    )
    assert code == 1 and restarted["phase"] == "awaiting-validation", stderr
    assert restarted["new_base"] == records["lane-02"]["reviewed_head"]


def test_second_lane_updates_target_while_first_lane_finalizes(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """一方の記録の確定（WI終了argv）の実行中に、他方が統合先を更新して最終化まで終える。"""
    repo, records = _prepare(tmp_path, monkeypatch, capsys)
    started, released = tmp_path / "first-finalization.started", tmp_path / "second.done"
    first_manifest = _manifest(tmp_path / "first.json", "lane-01", verification=[], wi_commands=[_barrier(started, released)])
    process = _start_first(
        integration_args(records["lane-01"], repo, first_manifest, records["lane-01"]["reviewed_head"]), started, tmp_path
    )
    try:
        # 先行側は統合先を更新済みで、最終化の途中にいる。
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == records["lane-01"]["reviewed_head"]
        second_manifest = _manifest(
            tmp_path / "second.json", "lane-02", verification=[[sys.executable, "-c", "pass"]], wi_commands=[]
        )
        second_args = integration_args(records["lane-02"], repo, second_manifest, records["lane-02"]["reviewed_head"])
        code, second, stderr = _invoke(capsys, *second_args)
        assert code == 1 and second["phase"] == "awaiting-validation", stderr
        code, second, stderr = _invoke(capsys, *second_args, "--accept-rebased-head", second["new_head"])
        assert code == 0 and second["phase"] == "finalized", stderr
        assert git_repository.git_output(repo, "rev-parse", "HEAD") == second["new_head"]
        assert process.poll() is None, "先行側の最終化が後続の統合の前に終わった"
    finally:
        released.write_text("done", encoding="utf-8")
        code, first, stderr = _finish_first(process, tmp_path)
    assert code == 0 and first["phase"] == "finalized", stderr
    assert first["integrated_head"] == records["lane-01"]["reviewed_head"]
    # メインの受領条件（exec-parent-integration.md「統合の受領」の2と7）を、統合先が先へ進んだ状態の両レーンへ当てはめる。
    target_head = git_repository.git_output(repo, "rev-parse", "main")
    assert target_head == second["integrated_head"] != first["integrated_head"]
    for result, lane_id in ((first, "lane-01"), (second, "lane-02")):
        stored = json.loads(pathlib.Path(records[lane_id]["record_path"]).read_text(encoding="utf-8"))["integration"]
        assert stored["integrated_head"] == result["integrated_head"]
        git_repository.run_git(repo, "merge-base", "--is-ancestor", result["integrated_head"], "main")
        # 統合前HEAD（短い更新の直前の統合先HEAD）からの範囲は、そのレーンの1commitだけを含む。
        before = stored.get("new_base") or stored["verified_base"]
        assert (
            git_repository.git_output(repo, "rev-list", f"{before}..{result['integrated_head']}") == result["integrated_head"]
        )
    selected = yaml.safe_load(pathlib.Path(records["lane-01"]["selection_file"]).read_text(encoding="utf-8"))
    assert {row["レーン"]: row["統合状態"] for row in selected["レーンの所要時間"]} == {
        "lane-01": "統合済み",
        "lane-02": "統合済み",
    }


def test_target_advance_before_short_update_keeps_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """準備の後に統合先が進んだら古い前提で更新せず、記録した工程から--restart-reviewで追随できる。"""
    repo, records = _prepare(tmp_path, monkeypatch, capsys)
    record = records["lane-01"]
    manifest = _manifest(tmp_path / "first.json", "lane-01", verification=[[sys.executable, "-c", "pass"]], wi_commands=[])
    original = lane._verify  # pylint: disable=protected-access
    advanced: list[str] = []

    def advance_target(*arguments: Any) -> None:
        # 準備（rebase不要の検収済み）の後、短い更新の直前に別の統合が統合先を進めた状態を作る。
        original(*arguments)
        if not advanced:
            (repo / "other.txt").write_text("別の統合", encoding="utf-8")
            advanced.append(git_repository.commit_all(repo, "別の統合"))

    monkeypatch.setattr(lane, "_verify", advance_target)
    code, result, stderr = _invoke(capsys, *integration_args(record, repo, manifest, record["reviewed_head"]))
    assert code == 1 and result["phase"] == "verified", stderr
    assert "統合先が準備の時点から進んだ" in stderr
    assert result["target_advanced"] == {"expected": record["initial_head"], "actual": advanced[0]}
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == advanced[0]
    stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
    assert stored["integration"]["phase"] == "verified"
    monkeypatch.setattr(lane, "_verify", original)
    args = integration_args(record, repo, manifest, record["reviewed_head"])
    code, result, stderr = _invoke(capsys, *args, "--restart-review")
    assert code == 1 and result["phase"] == "awaiting-validation" and result["new_base"] == advanced[0], stderr
    code, result, stderr = _invoke(capsys, *args, "--accept-rebased-head", result["new_head"])
    assert code == 0 and result["phase"] == "finalized", stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == result["new_head"]


@pytest.mark.parametrize("no_merge", [False, True])
def test_lane_starts_while_other_lane_updates_target(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], no_merge: bool
) -> None:
    """他レーンの短い更新が統合先のロックファイルを作っている間に始めた統合が、中断状態と誤判定せずに更新を待つ。"""
    repo, records = _prepare(tmp_path, monkeypatch, capsys)
    record = records["lane-02"]
    manifest = _manifest(tmp_path / "second.json", "lane-02", verification=[], wi_commands=[])
    index_lock = pathlib.Path(
        git_repository.git_output(repo, "rev-parse", "--path-format=absolute", "--git-path", "index.lock")
    )
    outcome: list[tuple[int, dict[str, Any], str]] = []
    # --no-mergeは統合先を書かないが、統合先の作業ツリーと中断状態を確かめるため同じ待機を観測する。
    extra = ("--no-merge",) if no_merge else ()
    # 他レーンのfast-forwardの途中（統合先単位の排他を保持し、統合先にindex.lockがある状態）を作る。
    with lane._target_locked(record, "main"):  # pylint: disable=protected-access
        index_lock.write_text("", encoding="utf-8")
        thread = threading.Thread(
            target=lambda: outcome.append(
                _invoke(capsys, *integration_args(record, repo, manifest, record["reviewed_head"]), *extra)
            )
        )
        thread.start()
        thread.join(timeout=5)
        assert thread.is_alive(), outcome
        index_lock.unlink()
    thread.join(timeout=_WAIT_SECONDS)
    assert not thread.is_alive(), "排他の解放後に統合が終わらなかった"
    code, result, stderr = outcome[0]
    assert code == 0 and result["phase"] == "finalized", stderr
    expected_head = record["initial_head"] if no_merge else record["reviewed_head"]
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == expected_head


@pytest.mark.parametrize("residual", ["index.lock", "dirty"])
def test_residual_target_state_is_still_rejected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], residual: str
) -> None:
    """排他を保持する統合が無いのに残る統合先の中断状態と未commitの変更は、短い更新で拒否して統合先を変えない。"""
    repo, records = _prepare(tmp_path, monkeypatch, capsys)
    record = records["lane-02"]
    manifest = _manifest(tmp_path / "second.json", "lane-02", verification=[], wi_commands=[])
    if residual == "index.lock":
        git_path = git_repository.git_output(repo, "rev-parse", "--path-format=absolute", "--git-path", "index.lock")
        pathlib.Path(git_path).write_text("", encoding="utf-8")
        expected = "Gitの中断状態がある"
    else:
        (repo / "dirty.txt").write_text("未commit", encoding="utf-8")
        expected = "未commitまたは未追跡の変更がある"
    code, _result, stderr = _invoke(capsys, *integration_args(record, repo, manifest, record["reviewed_head"]))
    assert code == 2 and expected in stderr, stderr
    assert git_repository.git_output(repo, "rev-parse", "HEAD") == record["initial_head"]


def test_delete_retains_lane_while_its_integration_runs(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """統合中のレーンは待たずに保持し、資源と所有登録を残す。"""
    _repo, records = _prepare(tmp_path, monkeypatch, capsys)
    record = records["lane-01"]
    with lane._lane_locked(pathlib.Path(record["record_path"])):  # pylint: disable=protected-access
        code, result, _stderr = _invoke(capsys, "delete")
    assert code == 1
    reasons = {pathlib.Path(row["record_path"]).stem: row["reason"] for row in result["retained"]}
    assert "統合が実行中" in reasons["lane-01"]
    assert pathlib.Path(record["worktree"]).is_dir() and pathlib.Path(record["record_path"]).is_file()
