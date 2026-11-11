"""serveのsessionsに関するcostsの試験。"""

# pylint: disable=protected-access

import errno
import json
import os
import pathlib
import subprocess
import sys
import time
import typing

import pytest

from agent_toolkit._atk.serve import sessions
from agent_toolkit._testing.serve_sessions_support import _claude_record, _context, _start_warmer, _wait_until


def test_family_cost_is_reused_until_a_family_record_changes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """家族の記録が変わらない間は再起動後も推定費用を算出し直さず、記録へ追記すると算出し直す。"""
    record = _claude_record(tmp_path)
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(record, ns=(old_ns, old_ns))
    estimated: list[pathlib.Path] = []
    original = sessions.session_cost_records.estimate_detail_family

    def recording(engine: str, path: pathlib.Path, *args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        estimated.append(path)
        return original(engine, path, *args, **kwargs)

    monkeypatch.setattr(sessions.session_cost_records, "estimate_detail_family", recording)
    context = _context(tmp_path, state_dir=tmp_path / "state")
    first = sessions.read_local_cost(context, "claude", str(record))
    assert sessions.read_local_cost(context, "claude", str(record)) == first
    restarted = _context(tmp_path, state_dir=tmp_path / "state")
    assert sessions.read_local_cost(restarted, "claude", str(record)) == first
    assert len(estimated) == 1
    with record.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps({"type": "assistant", "message": {"model": "claude-opus-5-5", "usage": {"input_tokens": 1000}}}) + "\n"
        )
    os.utime(record, ns=(old_ns + 5_000_000_000, old_ns + 5_000_000_000))
    changed = sessions.read_local_cost(restarted, "claude", str(record))
    assert len(estimated) == 2
    assert changed["known_usd"] > first["known_usd"]


def test_cost_warmer_precomputes_listed_family_in_worker_process(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """一覧を走査すると、画面で開く前に別プロセスが家族の推定費用を求め、再起動後の最初の要求も算出し直さない。

    一度も算出していない家族の最初の算出は全記録の解析に数秒かかり、要求の中では1秒以内に返せないためである。
    """
    record = _claude_record(tmp_path)
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(record, ns=(old_ns, old_ns))
    context = _context(tmp_path, state_dir=tmp_path / "state")
    expected = sessions.estimate_local_family(
        "claude",
        str(record),
        claude_home=context.claude_home,
        codex_home=context.codex_home,
        state_dir=context.state_dir,
        slim=context.state.slim_records,
    ).cost
    warmer = _start_warmer(context)
    try:
        assert [entry.path for entry in sessions.list_local_sessions(context)] == [str(record)]
        _wait_until(lambda: context.state.family_costs.get(record) is not None)
    finally:
        warmer.stop()
    assert context.state.family_costs.get(record) == expected

    def unexpected(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
        raise AssertionError("背景で求めた推定費用を使わずに算出し直した")

    monkeypatch.setattr(sessions.session_cost_records, "estimate_detail_family", unexpected)
    restarted = _context(tmp_path, state_dir=tmp_path / "state")
    assert sessions.read_local_cost(restarted, "claude", str(record)) == expected


def test_cost_warmer_stops_after_worker_exit_and_restarts_on_next_request(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """別プロセスが異常終了したら警告して背景の算出を止め、次の走査で別プロセスを起動し直して算出する。"""
    record = _claude_record(tmp_path)
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(record, ns=(old_ns, old_ns))
    context = _context(tmp_path, state_dir=tmp_path / "state")
    original = sessions._session_costs._start_worker
    started: list[str] = []

    def failing_then_real() -> subprocess.Popen[str]:
        started.append("worker")
        if len(started) == 1:
            return subprocess.Popen(  # pylint: disable=consider-using-with  # warmerが終了を待って閉じる
                [sys.executable, "-c", "import sys; sys.exit(3)"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
        return original()

    monkeypatch.setattr(sessions._session_costs, "_start_worker", failing_then_real)
    warmer = _start_warmer(context)
    try:
        with caplog.at_level("WARNING", logger=sessions._session_costs.logger.name):
            sessions.list_local_sessions(context)
            _wait_until(lambda: "終了コード3" in caplog.text)
        assert context.state.family_costs.get(record) is None
        sessions.list_local_sessions(context)
        _wait_until(lambda: context.state.family_costs.get(record) is not None)
    finally:
        warmer.stop()
    assert started == ["worker", "worker"]


def test_cost_warmer_survives_worker_start_failure_and_restarts_on_next_request(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """別プロセスを起動できなければ警告して背景の算出を止め、次の走査で起動し直して算出する。

    起動の失敗（プロセス数やファイル記述子の上限）で背景のスレッドが終わると、次の`request`でも再開しないためである。
    """
    record = _claude_record(tmp_path)
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(record, ns=(old_ns, old_ns))
    context = _context(tmp_path, state_dir=tmp_path / "state")
    original = sessions._session_costs._start_worker
    started: list[str] = []

    def failing_then_real() -> subprocess.Popen[str]:
        started.append("worker")
        if len(started) == 1:
            raise OSError(errno.EMFILE, "Too many open files")
        return original()

    monkeypatch.setattr(sessions._session_costs, "_start_worker", failing_then_real)
    warmer = _start_warmer(context)
    try:
        with caplog.at_level("WARNING", logger=sessions._session_costs.logger.name):
            sessions.list_local_sessions(context)
            _wait_until(lambda: "起動できない" in caplog.text)
        assert context.state.family_costs.get(record) is None
        sessions.list_local_sessions(context)
        _wait_until(lambda: context.state.family_costs.get(record) is not None)
    finally:
        warmer.stop()
    assert started == ["worker", "worker"]
    assert "Too many open files" in caplog.text
