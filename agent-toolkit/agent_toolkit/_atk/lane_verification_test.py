"""lane verificationの契約を検証する。"""

from __future__ import annotations

import contextlib
import json
import pathlib
import sys
from collections.abc import Callable

import pytest

from agent_toolkit._atk import lane
from agent_toolkit._testing.lane_integration_support import (
    _integration_args,
    _integration_selection,
    _rebased_integration,
    _timed_integration,
    _write_empty_finalization_manifest,
)
from agent_toolkit._testing.lane_support import _create, _invoke


def test_integrate_manifest_timeout_reclaims_verification_descendants(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """上限を持たない検証へ内部の有限上限を渡し、子孫回収後の失敗を所有記録へ保存する。"""
    repo, selected, record = _create(tmp_path, monkeypatch, capsys)
    _integration_selection(selected, monkeypatch, repo)
    manifest = _write_empty_finalization_manifest(tmp_path)
    marker = tmp_path / "verification-child.pid"
    program = (
        "import pathlib,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
        f"pathlib.Path({str(marker)!r}).write_text(str(child.pid)); time.sleep(60)"
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    data["verification_timeout"] = 10**4000
    manifest.write_text(json.dumps(data), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, record["initial_head"]))
    assert code == 2 and "verification_timeout" in stderr
    assert not marker.exists()
    data.update(verification=[[sys.executable, "-c", program]], verification_timeout=0.5)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    code, _result, _stderr = _invoke(capsys, *_integration_args(record, repo, manifest, record["initial_head"]))
    child = None
    with contextlib.suppress(lane.psutil.NoSuchProcess):
        if marker.exists():
            child = lane.psutil.Process(int(marker.read_text(encoding="utf-8")))
    try:
        assert code != 0 and marker.exists()
        stored = json.loads(pathlib.Path(record["record_path"]).read_text(encoding="utf-8"))
        assert stored["integration"]["results"]["verification-0"]["timed_out"]
        if child is not None:
            assert not child.is_running() or child.status() in (lane.psutil.STATUS_ZOMBIE, lane.psutil.STATUS_DEAD)
    finally:
        if child is not None:
            with contextlib.suppress(lane.psutil.NoSuchProcess):
                if child.is_running() and child.status() not in (lane.psutil.STATUS_ZOMBIE, lane.psutil.STATUS_DEAD):
                    child.kill()


def test_integrate_restart_review_after_failed_rebased_verification(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """検証失敗後、argvだけの是正をrebase前のレビューHEADで再開し検収を経る。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys, failed=True)
    value["verification"] = [[sys.executable, "-c", "pass"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    args = _integration_args(record, repo, manifest, original["old_head"])
    code, pending, stderr = _invoke(capsys, *args, "--restart-review", "--accept-rebased-head", original["new_head"])
    assert code == 1 and pending["new_head"] == original["new_head"], stderr
    assert pending["phase"] == "awaiting-validation"
    code, final, stderr = _invoke(capsys, *args, "--accept-rebased-head", pending["new_head"])
    assert code == 0 and final["phase"] == "finalized", stderr


def test_integrate_rejects_changed_input_with_restart_guidance(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    record_property: Callable[[str, object], None],
) -> None:
    """manifest是正だけの再開が、検収へ戻る操作を拒否文から選べる。"""
    repo, record, manifest, value, original = _rebased_integration(tmp_path, monkeypatch, capsys)
    value["verification"] = [[sys.executable, "-c", "assert True"]]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, original["old_head"]))
    record_property("restart_diagnostic", stderr)
    assert code == 2
    assert all(word in stderr for word in ("--restart-review", "検証argv", "最終化argv", "handoff", "検収待ち"))


@pytest.mark.parametrize("rebased", [False, True])
def test_integrate_verification_is_not_limited_by_procedure_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], rebased: bool
) -> None:
    """検証argvは自身の上限で終わり、固有手順向けのprocedure_timeoutで打ち切られずに統合が進む。"""
    repo, record, handoff, head, argv = _timed_integration(tmp_path, monkeypatch, capsys, rebased=rebased)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": []}],
                "handoff": str(handoff),
                "verification": [argv],
            }
        ),
        encoding="utf-8",
    )
    code, result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    if rebased:
        assert code == 1 and result["phase"] == "awaiting-validation", stderr
    else:
        assert code == 0 and result["phase"] == "finalized", stderr
    assert "verification-0" in result["completed"]


def test_integrate_finalization_keeps_procedure_timeout(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """自身の上限を持たない最終化argvは、従来どおりprocedure_timeoutで打ち切られる。"""
    repo, record, handoff, head, argv = _timed_integration(tmp_path, monkeypatch, capsys, rebased=False)
    manifest = tmp_path / "integration.json"
    manifest.write_text(
        json.dumps(
            {
                "plans": [],
                "wis": [{"name": "20261023-000000-001.md", "commands": [argv]}],
                "handoff": str(handoff),
                "verification": [],
            }
        ),
        encoding="utf-8",
    )
    code, _result, stderr = _invoke(capsys, *_integration_args(record, repo, manifest, head))
    assert code == 2 and "wis:20261023-000000-001.md:0が終了124" in stderr, stderr
