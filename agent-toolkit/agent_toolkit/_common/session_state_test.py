"""agent-toolkit/agent_toolkit/_common/session_state.py のテスト。

並行書き込み時のキー保持・アトミック書き込みの保証・OS別ロックの動作を検証する。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
import time
from typing import Any, cast

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._common.session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    STALE_STATE_MAX_AGE_SECONDS,
    clear_session_state,
    delete_state,
    inherit_state_from_transcript,
    read_state,
    state_path,
    sweep_stale_states,
    update_state,
)
from agent_toolkit._testing.helpers import SESSION_STATE_FILENAME_TEMPLATE


@pytest.fixture(autouse=True)
def _redirect_tempdir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`tempfile.gettempdir()`が`tmp_path`を返すよう差し替える。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    state_path("sid").parent.mkdir(parents=True, exist_ok=True)


def _lock_path(session_id: str) -> pathlib.Path:
    return state_path(session_id).parent / ".state.lock"


def test_state_survives_changed_tempdir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """委譲先とスキルの記録は一時領域の切替後も同じ状態として読める。"""
    expected = {"agents_server_cwd_by_session": {"child": "/work"}, "operation_skill_delivered_agents": {"search": ["main"]}}
    assert update_state("resumed", lambda _current: expected)
    original = state_path("resumed")
    changed = tmp_path / "changed"
    changed.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(changed))
    assert state_path("resumed") == original
    assert read_state("resumed") == expected
    assert update_state("resumed", lambda current: {**current, "continued": True})
    assert read_state("resumed") == {**expected, "continued": True}
    assert not (changed / original.name).exists()


def test_legacy_state_migrates_once(tmp_path: pathlib.Path) -> None:
    """旧状態を読取と更新の両方から移し、移行済みの内容を優先する。"""
    legacy = tmp_path / SESSION_STATE_FILENAME_TEMPLATE.format(session_id="legacy")
    legacy.write_text('{"old": true}', encoding="utf-8")
    assert read_state("legacy") == {"old": True}
    assert state_path("legacy").is_file()
    assert not legacy.exists()
    legacy.write_text('{"stale": true}', encoding="utf-8")
    assert update_state("legacy", lambda current: {**current, "new": True})
    assert read_state("legacy") == {"old": True, "new": True}
    assert clear_session_state("legacy")
    assert not read_state("legacy")


def test_same_session_updates_share_lock_across_tempdirs(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """異なる一時領域を使う呼出元も、同じ状態への加算を失わない。"""
    directories = {name: tmp_path / name for name in ("state-first", "state-second")}
    for directory in directories.values():
        directory.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(directories.get(threading.current_thread().name, tmp_path)))
    ready = threading.Barrier(2)
    failures: list[str] = []

    def writer() -> None:
        ready.wait(timeout=5)
        for _ in range(20):

            def increment(current: dict) -> dict:
                count = current.get("count", 0)
                time.sleep(0.002)
                return {**current, "count": count + 1}

            if not update_state("shared", increment):
                failures.append(threading.current_thread().name)

    threads = [threading.Thread(target=writer, name=name) for name in directories]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)
    for thread in threads:
        assert not thread.is_alive()
    assert not failures
    assert read_state("shared") == {"count": 40}
    assert {path.name for path in state_path("shared").parent.glob("*.lock")} == {".state.lock"}


def test_state_cleanup_keeps_one_fixed_lock() -> None:
    """会話数に比例した空lockを残さず、JSON回収後も共有lockの実体を保つ。"""
    for index in range(10):
        assert update_state(f"session-{index}", lambda _current: {"active": True})
    directory = state_path("session-0").parent
    assert {path.name for path in directory.glob("*.lock")} == {".state.lock"}
    assert clear_session_state("session-0")
    assert sweep_stale_states(now=time.time() + STALE_STATE_MAX_AGE_SECONDS + 60) == 9
    assert {path.name for path in directory.iterdir()} == {".state.lock"}


def test_transcript_preparation_keeps_shared_lock_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """前身記録の走査中にも、別会話の短い状態更新を進められる。"""
    assert update_state("previous", lambda _current: {"inherited": True})
    reading = threading.Event()
    release = threading.Event()
    mutation_done = threading.Event()
    inherited: list[bool] = []

    def read_lines(_path: str) -> list[str]:
        reading.set()
        assert release.wait(timeout=5)
        return [json.dumps({"sessionId": "previous"})]

    def mutate() -> None:
        update_state("other", lambda _current: {"updated": True})
        mutation_done.set()

    monkeypatch.setattr("agent_toolkit._common.transcript.read_transcript_lines", read_lines)
    preparation = threading.Thread(target=lambda: inherited.append(inherit_state_from_transcript("current", "record")))
    mutation = threading.Thread(target=mutate)
    preparation.start()
    try:
        assert reading.wait(timeout=5)
        mutation.start()
        assert mutation_done.wait(timeout=2)
    finally:
        release.set()
        preparation.join(timeout=5)
        if mutation.ident is not None:
            mutation.join(timeout=5)
    assert not preparation.is_alive()
    assert not mutation.is_alive()
    assert inherited == [True]
    assert read_state("other") == {"updated": True}


class TestUpdateState:
    """単一スレッドからの基本動作。"""

    def test_initial_write_creates_file(self) -> None:
        modified = update_state("sid", lambda current: {**current, "a": 1})
        assert modified is True
        assert read_state("sid") == {"a": 1}

    def test_mutator_returns_none_skips_write(self) -> None:
        update_state("sid", lambda current: {**current, "a": 1})
        modified = update_state("sid", lambda current: None)
        assert modified is False
        assert read_state("sid") == {"a": 1}

    def test_invalid_session_id_empty(self) -> None:
        modified = update_state("", lambda current: {**current, "a": 1})
        assert modified is False

    def test_invalid_session_id_non_string(self) -> None:
        # 静的型は`str`だが、ランタイムでは外部payload由来の非文字列値を防御するため、
        # `cast`で型チェックを回避して入力の妥当性を判定する処理を直接検証する。
        modified = update_state(cast(str, 123), lambda current: {**current, "a": 1})
        assert modified is False

    def test_atomic_on_exception_preserves_old_content(self) -> None:
        """mutator内例外時に旧内容が残ること（書き込み未到達でファイル不変）。"""
        update_state("sid", lambda current: {**current, "a": 1})

        def _bad_mutator(_current: dict) -> dict:
            raise RuntimeError("simulated failure")

        with pytest.raises(RuntimeError):
            update_state("sid", _bad_mutator)
        assert read_state("sid") == {"a": 1}

    def test_temp_file_not_left_behind(self) -> None:
        """書き込み完了後に一時ファイル（`*.tmp`）が残らないこと。"""
        update_state("sid", lambda current: {**current, "a": 1})
        residual = list(state_path("sid").parent.glob("*.tmp"))
        assert not residual

    def test_corrupt_state_treated_as_empty(self) -> None:
        """破損ファイルは空辞書として扱われる。"""
        state_path("sid").write_text("{ not valid json", encoding="utf-8")
        captured: dict = {}

        def _mutator(current: dict) -> dict:
            captured.update(current)
            return {"recovered": True}

        update_state("sid", _mutator)
        assert not captured
        assert read_state("sid") == {"recovered": True}


class TestConcurrentWrites:
    """並行書き込み時に全キーが保持されること。"""

    @pytest.mark.parametrize("iterations", [1, 10, 100])
    def test_two_threads_distinct_keys(self, iterations: int) -> None:
        def _writer(key: str, value: int) -> None:
            def _m(current: dict) -> dict:
                current[key] = value
                return current

            for _ in range(iterations):
                update_state("concur", _m)

        t_a = threading.Thread(target=_writer, args=("a", 1))
        t_b = threading.Thread(target=_writer, args=("b", 2))
        t_a.start()
        t_b.start()
        t_a.join()
        t_b.join()
        state = read_state("concur")
        assert state == {"a": 1, "b": 2}

    def test_increment_under_contention(self) -> None:
        """同一キーに対する加算がロスト更新を起こさないこと。"""

        def _incr(current: dict) -> dict:
            current["count"] = current.get("count", 0) + 1
            return current

        def _worker() -> None:
            for _ in range(50):
                update_state("counter", _incr)

        threads = [threading.Thread(target=_worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert read_state("counter") == {"count": 200}


@pytest.mark.skipif(os.name == "nt", reason="POSIX固有のロック実装")
class TestPosixLock:
    """POSIX (`fcntl.flock`) のロック動作を確認する。"""

    def test_lock_file_created(self) -> None:
        update_state("posix", lambda current: {**current, "k": "v"})
        lock_path = _lock_path("posix")
        assert lock_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows固有のロック実装")
class TestWindowsLock:
    """Windows (`msvcrt.locking`) のロック動作を確認する。"""

    def test_lock_file_created(self) -> None:
        update_state("win", lambda current: {**current, "k": "v"})
        lock_path = _lock_path("win")
        assert lock_path.exists()


class TestReadState:
    """`read_state`の入力検証と破損ファイル処理。"""

    def test_empty_when_unset(self) -> None:
        assert read_state("missing") == {}

    def test_empty_session_id(self) -> None:
        assert read_state("") == {}

    def test_non_dict_payload_returns_empty(self) -> None:
        state_path("array").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        assert read_state("array") == {}


class TestStateInheritance:
    """背景化でsession_idが変わった場合の前身状態継承。"""

    def test_unique_predecessor_is_inherited_once(self, tmp_path: pathlib.Path) -> None:
        update_state("previous", lambda current: {**current, "plan_mode_skill_invoked": True})
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "\n".join((json.dumps({"sessionId": "previous"}), json.dumps({"sessionId": "current"}))) + "\n",
            encoding="utf-8",
        )

        assert inherit_state_from_transcript("current", str(transcript)) is True
        assert read_state("current") == {
            "plan_mode_skill_invoked": True,
            "inherited_from_session_id": "previous",
        }
        update_state("previous", lambda current: {**current, "later": True})
        assert inherit_state_from_transcript("current", str(tmp_path / "missing.jsonl")) is False
        assert "later" not in read_state("current")

    def test_ambiguous_or_missing_predecessor_is_not_inherited(self, tmp_path: pathlib.Path) -> None:
        update_state("first", lambda current: {**current, "first": True})
        update_state("second", lambda current: {**current, "second": True})
        transcript = tmp_path / "transcript.jsonl"
        transcript.write_text(
            "\n".join((json.dumps({"session_id": "first"}), json.dumps({"sessionId": "second"}))) + "\n",
            encoding="utf-8",
        )

        assert inherit_state_from_transcript("current", str(transcript)) is False
        assert read_state("current") == {}
        assert inherit_state_from_transcript("current", str(tmp_path / "missing.jsonl")) is False


class TestDeleteState:
    """状態JSONを削除する処理の入力と結果の境界。"""

    def test_existing_state_is_deleted_but_lock_is_kept(self) -> None:
        update_state("sid", lambda current: {**current, "a": 1})
        path = state_path("sid")
        lock_path = _lock_path("sid")

        assert delete_state("sid") is True
        assert not path.exists()
        assert lock_path.exists()

    def test_missing_state_is_success(self) -> None:
        assert delete_state("missing") is True

    def test_invalid_session_ids_fail(self) -> None:
        assert delete_state("") is False
        assert delete_state(cast(str, 123)) is False

    def test_other_session_is_preserved(self) -> None:
        update_state("target", lambda current: {**current, "target": True})
        update_state("other", lambda current: {**current, "other": True})

        assert delete_state("target") is True
        assert read_state("target") == {}
        assert read_state("other") == {"other": True}

    def test_delete_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        update_state("sid", lambda current: {**current, "a": 1})
        target = state_path("sid")
        original_unlink = pathlib.Path.unlink

        def _unlink(path: pathlib.Path, *, missing_ok: bool = False) -> None:
            if path == target:
                raise PermissionError("simulated failure")
            original_unlink(path, missing_ok=missing_ok)

        monkeypatch.setattr(pathlib.Path, "unlink", _unlink)
        assert delete_state("sid") is False
        assert read_state("sid") == {"a": 1}


class TestSweepStaleStates:
    """期限を過ぎた状態ファイルとロックファイルの回収。"""

    @staticmethod
    def _age(path: pathlib.Path, seconds: float) -> None:
        stamp = os.stat(path).st_mtime - seconds
        os.utime(path, (stamp, stamp))

    def test_fresh_state_and_lock_are_kept(self) -> None:
        """期限内の状態ファイルと対のロックは残す。"""
        update_state("fresh", lambda current: {**current, "a": 1})
        target = state_path("fresh")
        lock = _lock_path("fresh")

        assert sweep_stale_states() == 0
        assert target.exists()
        assert lock.exists()

    def test_stale_state_is_collected_but_lock_is_kept(self) -> None:
        """期限を過ぎた状態ファイルは回収するが、対応するロックは削除しない。"""
        update_state("stale", lambda current: {**current, "a": 1})
        target = state_path("stale")
        lock = _lock_path("stale")
        self._age(target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 1
        assert not target.exists()
        assert lock.exists()

    def test_old_lock_is_kept_while_state_is_fresh(self) -> None:
        """状態ファイルが期限内なら、対のロックが古くても残す。

        ロックは`open(path, "a+")`で開くだけなので更新時刻が進まず、
        再開したセッションでは作成時刻が期限を超えうる。
        """
        update_state("resumed", lambda current: {**current, "a": 1})
        target = state_path("resumed")
        lock = _lock_path("resumed")
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 0
        assert target.exists()
        assert lock.exists()

    def test_orphan_lock_is_never_collected(self, tmp_path: pathlib.Path) -> None:
        """状態ファイルの無いロックは、期限をいくら過ぎても削除しない。

        `update_state`はロックを先に作成するため、状態ファイルの無いロックは
        セッション開始直後にも生じる。ロックファイルを削除する処理がないため、
        回収対象からの除外に`keep_session_id`の指定有無は影響しない。
        """
        starting_name = SESSION_STATE_FILENAME_TEMPLATE.format(session_id="starting")
        abandoned_name = SESSION_STATE_FILENAME_TEMPLATE.format(session_id="abandoned")
        starting = tmp_path / f"{starting_name}.lock"
        abandoned = tmp_path / f"{abandoned_name}.lock"
        starting.write_text("", encoding="utf-8")
        abandoned.write_text("", encoding="utf-8")
        self._age(abandoned, STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 0
        assert starting.exists()
        assert abandoned.exists()

    def test_kept_session_survives_expiry(self) -> None:
        """`keep_session_id`の状態とロックは、期限を過ぎても回収しない。"""
        update_state("ending", lambda current: {**current, "a": 1})
        update_state("stale", lambda current: {**current, "a": 1})
        target = state_path("ending")
        lock = _lock_path("ending")
        self._age(target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(state_path("stale"), STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states(keep_session_id="ending") == 1
        assert read_state("ending") == {"a": 1}
        assert lock.exists()
        assert not state_path("stale").exists()

    def test_other_sessions_are_untouched(self) -> None:
        """期限内の別セッションの状態は回収しない。"""
        update_state("stale", lambda current: {**current, "a": 1})
        update_state("live", lambda current: {**current, "a": 1})
        self._age(state_path("stale"), STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 1
        assert not state_path("stale").exists()
        assert read_state("live") == {"a": 1}

    def test_sweep_rechecks_age_after_waiting_for_update(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """期限判定後に更新された通常状態を回収しない。"""
        session_id = "stale-refresh-race"
        update_state(session_id, lambda current: {**current, "expired": True})
        target = state_path(session_id)
        lock = _lock_path(session_id)
        self._age(target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        update_started = threading.Event()
        allow_update = threading.Event()
        sweep_waiting_for_lock = threading.Event()
        original_acquire_lock = vars(sys.modules["agent_toolkit._common.session_state"])["_acquire_lock"]

        def _acquire_lock(lock_file: Any) -> None:
            if threading.current_thread().name == "state-sweep":
                sweep_waiting_for_lock.set()
            original_acquire_lock(lock_file)

        monkeypatch.setattr("agent_toolkit._common.session_state._acquire_lock", _acquire_lock)

        def _refresh(current: dict) -> dict:
            update_started.set()
            assert allow_update.wait(timeout=5)
            return {**current, "refreshed": True}

        update = threading.Thread(target=update_state, args=(session_id, _refresh))
        update.start()
        assert update_started.wait(timeout=5)
        sweep = threading.Thread(target=sweep_stale_states, name="state-sweep")
        sweep.start()
        assert sweep_waiting_for_lock.wait(timeout=5)
        allow_update.set()

        update.join(timeout=5)
        sweep.join(timeout=5)
        assert not update.is_alive()
        assert not sweep.is_alive()
        assert read_state(session_id) == {"expired": True, "refreshed": True}
        assert lock.exists()


class TestClearSessionState:
    """会話破棄時の通常状態の削除。"""

    def test_state_is_deleted_but_lock_is_kept(self) -> None:
        update_state("sid", lambda current: {**current, "active": True})
        normal = state_path("sid")

        assert clear_session_state("sid") is True

        assert not normal.exists()
        assert _lock_path("sid").exists()

    def test_invalid_session_ids_fail(self) -> None:
        assert clear_session_state("") is False
        assert clear_session_state(cast(str, 123)) is False


def test_session_state_persists_plan_flags() -> None:
    """計画レビュー完了のフラグが永続化されることを確認する。"""
    session_id = "test-plan-session"

    def _set(state: dict) -> dict | None:
        state["plan_mode_skill_invoked"] = True
        return state

    assert update_state(session_id, _set) is True
    state = read_state(session_id)
    assert state["plan_mode_skill_invoked"] is True
    assert "codex_" + "exec_skill_invoked" not in state


def test_mod_title_state_shares_locked_state(capsys: pytest.CaptureFixture[str]) -> None:
    """modの読取・種別・命名の記録は他のhookの状態を保つ。"""
    update_state("sid", lambda state: {**state, "current_plan_file_path": "/plans/18-1921_計画.md", "other": True})
    assert session_state.main(["--session-id", "sid", "--kind=single-lane"]) == 0
    assert json.loads(capsys.readouterr().out)["session_title_kind"] == "single-lane"
    assert session_state.main(["--session-id", "sid", "--title=single-lane 計画"]) == 0
    assert json.loads(capsys.readouterr().out)["last_hook_session_title"] == "single-lane 計画"
    assert session_state.main(["--session-id", "sid"]) == 0
    assert json.loads(capsys.readouterr().out)["current_plan_file_path"] == "/plans/18-1921_計画.md"
    assert read_state("sid")["other"] is True
