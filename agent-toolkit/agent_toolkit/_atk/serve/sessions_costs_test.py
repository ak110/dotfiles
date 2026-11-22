# pylint: disable=protected-access

import contextlib
import errno
import io
import json
import os
import pathlib
import subprocess
import sys
import time
import types
import typing

import pytest

from agent_toolkit._atk.serve import sessions
from agent_toolkit._testing.serve_sessions_support import _claude_record, _codex_record, _context, _start_warmer, _wait_until


def test_cost_warmer_preserves_unreclaimed_owner_before_restarting(tmp_path, monkeypatch):
    """回収未確認は所有対象を保持して再試行し、解消するまで新workerを開始しない。"""
    subject = sessions._session_costs
    owned = typing.cast(typing.Any, object())
    failure = subject._process_tree.ReclaimError(["fixture-owned"], [owned], context="fixture")
    starts = []
    retried = []

    @contextlib.contextmanager
    def start():
        starts.append("worker")
        process = types.SimpleNamespace(stdin=io.StringIO(), stdout=io.StringIO(), returncode=1)
        try:
            yield process
        finally:
            process.stdin.close()
            process.stdout.close()
            raise failure

    def retry(**_kwargs):
        retried.append(list(failure.processes))
        return failure.residual

    monkeypatch.setattr(subject, "_start_worker", start)
    monkeypatch.setattr(failure, "retry", retry)
    warmer = subject.FamilyCostWarmer(
        subject.FamilyCostCache(None), claude_home=tmp_path, codex_home=tmp_path, state_dir=None, slim_directory=None
    )
    assert warmer._compute("claude", "unused") is None
    assert warmer._compute("claude", "unused") is None
    assert starts == ["worker"]
    assert warmer._worker_failure is failure
    with pytest.raises(subject._process_tree.ReclaimError) as caught:
        warmer.stop()
    assert caught.value is failure
    assert retried == [[owned], [owned]]
    assert warmer._worker_failure is failure


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.asyncio
async def test_local_watch_stop_retains_unreclaimed_cost_worker(tmp_path, monkeypatch, asynchronous):
    """同期・非同期の停止は回収未確認の所有を残し、同じ対象の再試行が成功してから解放する。"""
    subject = sessions._session_costs
    failure = subject._process_tree.ReclaimError(["fixture-owned"], [], context="fixture", unresolved=["not confirmed"])
    context = _context(tmp_path)
    warmer = subject.FamilyCostWarmer(
        subject.FamilyCostCache(None), claude_home=tmp_path, codex_home=tmp_path, state_dir=None, slim_directory=None
    )
    context.state.cost_warmer = warmer
    warmer._worker_failure = failure
    monkeypatch.setattr(failure, "retry", lambda **_kwargs: failure.residual)
    with pytest.raises(subject._process_tree.ReclaimError) as caught:
        if asynchronous:
            await sessions.stop_local_watch_async(context)
        else:
            sessions.stop_local_watch(context)
    assert caught.value is failure
    assert context.state.cost_warmer is warmer
    monkeypatch.setattr(failure, "retry", lambda **_kwargs: [])
    if asynchronous:
        await sessions.stop_local_watch_async(context)
    else:
        sessions.stop_local_watch(context)
    assert context.state.cost_warmer is None
    assert warmer._worker_failure is None


def test_family_cost_is_reused_until_a_family_record_changes(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_saved_unpriced_sol_is_recalculated_without_record_changes(tmp_path: pathlib.Path) -> None:
    record = _codex_record(tmp_path)
    with record.open("a", encoding="utf-8") as stream:
        for entry in [
            {"type": "turn_context", "payload": {"model": "gpt-6.2-sol"}},
            {"type": "event_msg", "payload": {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 1000}}}},
        ]:
            stream.write(json.dumps(entry) + "\n")
    state_dir = tmp_path / "state"
    cache = state_dir / "serve" / "session-costs.json"
    cache.parent.mkdir(parents=True)
    old_cost = {"estimated_usd": None, "known_usd": 0, "unknown_responses": 1, "pricing": {"as_of": "2026-11-11"}}
    family = {"stamps": sessions._session_costs.family_stamps([record]), "cost": old_cost}
    cache.write_text(json.dumps({"version": 1, "families": {str(record): family}}), encoding="utf-8")

    cost = sessions.read_local_cost(_context(tmp_path, state_dir=state_dir), "codex", str(record))
    assert cost["estimated_usd"] == pytest.approx(0.002)
    assert cost["unknown_responses"] == 0


def test_cost_warmer_precomputes_listed_family_in_worker_process(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    record = _claude_record(tmp_path)
    old_ns = time.time_ns() - 10_000_000_000
    os.utime(record, ns=(old_ns, old_ns))
    context = _context(tmp_path, state_dir=tmp_path / "state")
    original = sessions._session_costs._start_worker
    started: list[str] = []

    def failing_then_real() -> contextlib.AbstractContextManager[subprocess.Popen[str]]:
        started.append("worker")
        if len(started) == 1:
            return sessions._session_costs._process_tree.process_scope(
                [sys.executable, "-c", "import sys; sys.stdin.readline(); sys.exit(3)"],
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

    def failing_then_real() -> contextlib.AbstractContextManager[subprocess.Popen[str]]:
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
