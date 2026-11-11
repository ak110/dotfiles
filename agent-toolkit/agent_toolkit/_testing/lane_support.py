"""laneの分割試験で共有する準備と補助処理。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import yaml

from agent_toolkit import atk
from agent_toolkit._atk import lane
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
    state = "統合済み" if integrated else "未統合"
    path.write_text(
        yaml.safe_dump({"レーンの所要時間": [{"レーン": lane, "統合状態": state} for lane in ("lane-01", "lane-02")]}),
        encoding="utf-8",
    )


def _fixed(repo: pathlib.Path, lane_id: str) -> pathlib.Path:
    return repo.resolve() / ".claude" / "worktrees" / f"process-wi-{lane_id}"


def _create_in(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    session_id: str,
    repo: pathlib.Path,
    selected: pathlib.Path,
    *extra: str,
    lane_id: str = "lane-01",
) -> tuple[int, dict[str, Any], str]:
    monkeypatch.setenv(lane.SESSION_ENV, session_id)
    args = ("create", "--repo", str(repo), "--base-branch", "main", "--lane", lane_id, "--selection-file", str(selected))
    return _invoke(capsys, *args, *extra)


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
