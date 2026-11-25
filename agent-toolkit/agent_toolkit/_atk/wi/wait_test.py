import argparse
import json
from pathlib import Path

import pytest

from agent_toolkit._atk.wi import wait

_REPO = "github.com/example/project"
_NAME = "20261125-123456-001.md"


def _item(notes: Path, name: str, kind: str, *, answer: str = "", repo: str = _REPO, state: str = "inbox") -> None:
    month = notes / name[:4] / name[4:6]
    month.mkdir(parents=True, exist_ok=True)
    (month / name).write_text(
        f"---\ntype: {kind}\ntarget_repo: {repo}\nstate: {state}\n---\n# Title\n\n## 回答\n{answer}\n", encoding="utf-8"
    )


@pytest.mark.parametrize("change", ["answered", "awi", "none", "failure"])
def test_wait_scans_only_on_changed_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], change: str
) -> None:
    index = tmp_path / "index"
    index.write_text("initial", encoding="utf-8")
    _item(tmp_path, _NAME, "uwi")
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps([{"target_repo": _REPO, "filename": "20261125-123456-002.md"}]), encoding="utf-8")
    _item(tmp_path, "20261125-123456-002.md", "awi")
    _item(tmp_path, "20261125-123456-003.md", "awi", repo="github.com/example/other")
    _item(tmp_path, "20261125-123456-004.md", "awi", state="hold")
    monkeypatch.setattr(wait, "index_path", lambda _notes: index)
    sync_times: list[float] = []
    clock = 0.0
    scans: list[float] = []
    expected_scan = wait._matches  # pylint: disable=protected-access

    def scan(*args):
        scans.append(clock)
        return expected_scan(*args)

    def sleep(seconds: float) -> None:
        nonlocal clock
        clock += seconds
        if clock != 2:
            return
        if change == "answered":
            _item(tmp_path, _NAME, "uwi", answer="回答済み")
        elif change == "awi":
            _item(tmp_path, "20261125-123456-005.md", "awi")
        elif change == "failure":
            index.unlink()
            return
        else:
            return
        index.write_text("changed index", encoding="utf-8")

    monkeypatch.setattr(wait.time, "monotonic", lambda: clock)
    monkeypatch.setattr(wait.time, "sleep", sleep)
    monkeypatch.setattr(wait.background_sync, "schedule", lambda _notes: sync_times.append(clock))
    monkeypatch.setattr(wait, "_matches", scan)
    timeout = 125 if change == "none" else 5
    rc = wait.cmd_wait(argparse.Namespace(uwi=[_NAME], new_since=baseline, target_repo=_REPO, timeout=timeout), tmp_path)
    output = capsys.readouterr()
    if change in {"answered", "awi"}:
        assert rc == 0
        expected = (
            {"type": "uwi", "filename": _NAME}
            if change == "answered"
            else {"type": "awi", "filename": "20261125-123456-005.md"}
        )
        assert json.loads(output.out) == expected
        assert scans == [0, 2]
    else:
        assert rc == (3 if change == "none" else 1)
        assert not output.out
        assert scans == [0]
    assert sync_times == ([0, 60, 120] if change == "none" else [0])


def test_wait_already_answered(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _item(tmp_path, _NAME, "uwi", answer="回答済み", state="adopted")
    index = tmp_path / "index"
    index.touch()
    monkeypatch.setattr(wait, "index_path", lambda _notes: index)
    args = argparse.Namespace(uwi=[_NAME], new_since=None, target_repo=None, timeout=0)
    assert wait.cmd_wait(args, tmp_path) == 0
    assert json.loads(capsys.readouterr().out) == {"type": "uwi", "filename": _NAME}
