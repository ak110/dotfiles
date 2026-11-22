import datetime
import os
import pathlib
import sys

import pytest

from pytools import date_relocator


def _run_main(monkeypatch: pytest.MonkeyPatch, *args: str) -> int | str | None:
    monkeypatch.setattr(sys, "argv", ["dateRelocator", *args])
    with pytest.raises(SystemExit) as exc_info:
        date_relocator.main()
    return exc_info.value.code


def _make_target(tmp_path: pathlib.Path) -> pathlib.Path:
    target = tmp_path / "photos"
    target.mkdir()
    file = target / "a.jpg"
    file.write_bytes(b"x")
    ts = datetime.datetime(2026, 1, 15).timestamp()
    os.utime(file, (ts, ts))
    return target


def test_exit_code_one_when_target_is_not_directory(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _make_target(tmp_path)

    assert _run_main(monkeypatch, str(tmp_path / "missing"), str(target)) == 1
    assert (tmp_path / "photos-202601" / "a.jpg").exists()


def test_exit_code_zero_without_failure(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = _make_target(tmp_path)

    assert _run_main(monkeypatch, str(target)) == 0
    assert (tmp_path / "photos-202601" / "a.jpg").exists()
