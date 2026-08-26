"""agent-toolkit/scripts/_session_state.py のテスト。

並行書き込み時のキー保持・アトミック書き込みの保証・OS別ロックの動作を検証する。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import tempfile
import threading
from typing import Any, cast

import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    STALE_STATE_MAX_AGE_SECONDS,
    claim_session_title,
    clear_session_state,
    delete_state,
    read_state,
    state_path,
    sweep_stale_states,
    title_state_path,
    update_state,
)


@pytest.fixture(autouse=True)
def _redirect_tempdir(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """`tempfile.gettempdir()`が`tmp_path`を返すよう差し替える。"""
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))


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
        # `cast`で型チェックを回避して入力検証経路の動作を直接検証する。
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

    def test_temp_file_not_left_behind(self, tmp_path: pathlib.Path) -> None:
        """書き込み完了後に一時ファイル（`*.tmp`）が残らないこと。"""
        update_state("sid", lambda current: {**current, "a": 1})
        residual = list(tmp_path.glob("*.tmp"))
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
        lock_path = state_path("posix").parent / (state_path("posix").name + ".lock")
        assert lock_path.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows固有のロック実装")
class TestWindowsLock:
    """Windows (`msvcrt.locking`) のロック動作を確認する。"""

    def test_lock_file_created(self) -> None:
        update_state("win", lambda current: {**current, "k": "v"})
        lock_path = state_path("win").parent / (state_path("win").name + ".lock")
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


class TestClaimSessionTitle:
    """計画名の独立記録と失敗時の抑止。"""

    def test_first_claim_wins_and_normal_state_is_unchanged(self) -> None:
        update_state("sid", lambda current: {**current, "active": True})

        assert claim_session_title("sid", "first-plan") is True
        assert claim_session_title("sid", "second-plan") is False

        assert read_state("sid") == {"active": True}
        assert json.loads(title_state_path("sid").read_text(encoding="utf-8")) == {"last_hook_session_title": "first-plan"}

    def test_corrupt_record_fails_without_replacement(self) -> None:
        path = title_state_path("sid")
        path.parent.mkdir(parents=True)
        path.write_text("{", encoding="utf-8")

        assert claim_session_title("sid", "plan") is False
        assert path.read_text(encoding="utf-8") == "{"

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def _fail_write(_path: pathlib.Path, _content: str) -> None:
            raise PermissionError("simulated failure")

        monkeypatch.setattr("_session_state._atomic_write", _fail_write)

        assert claim_session_title("sid", "plan") is False
        assert not title_state_path("sid").exists()


class TestDeleteState:
    """状態JSON削除の入力・寿命境界。"""

    def test_existing_state_is_deleted_but_lock_is_kept(self) -> None:
        update_state("sid", lambda current: {**current, "a": 1})
        path = state_path("sid")
        lock_path = path.parent / (path.name + ".lock")

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
        lock = target.parent / (target.name + ".lock")

        assert sweep_stale_states() == 0
        assert target.exists()
        assert lock.exists()

    def test_stale_state_is_collected_but_lock_is_kept(self) -> None:
        """期限を過ぎた状態ファイルは回収するが、対応するロックは削除しない。"""
        update_state("stale", lambda current: {**current, "a": 1})
        target = state_path("stale")
        lock = target.parent / (target.name + ".lock")
        self._age(target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 1
        assert not target.exists()
        assert lock.exists()

    def test_stale_state_and_title_record_are_both_collected(self) -> None:
        """期限切れの通常状態と計画名記録の双方を回収し、対応するロックは残す。"""
        update_state(
            "titled",
            lambda current: {
                **current,
                "current_plan_file_path": "/home/example/.claude/plans/plan.md",
            },
        )
        assert claim_session_title("titled", "plan") is True
        target = state_path("titled")
        lock = target.parent / (target.name + ".lock")
        title_target = title_state_path("titled")
        title_lock = title_target.with_name(title_target.name + ".lock")
        self._age(target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(title_target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(title_lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 2
        assert read_state("titled") == {}
        assert not target.exists()
        assert lock.exists()
        assert not title_target.exists()
        assert title_lock.exists()

    def test_old_lock_is_kept_while_state_is_fresh(self) -> None:
        """状態ファイルが期限内なら、対のロックが古くても残す。

        ロックは`open(path, "a+")`で開くだけなので更新時刻が進まず、
        再開したセッションでは作成時刻が期限を超えうる。
        """
        update_state("resumed", lambda current: {**current, "a": 1})
        target = state_path("resumed")
        lock = target.parent / (target.name + ".lock")
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        assert sweep_stale_states() == 0
        assert target.exists()
        assert lock.exists()

    def test_orphan_lock_is_never_collected(self, tmp_path: pathlib.Path) -> None:
        """状態ファイルの無いロックは、期限をいくら過ぎても削除しない。

        `update_state`はロックを先に作成するため、状態ファイルの無いロックは
        セッション開始直後にも生じる。ロックファイルの削除経路自体を持たないため、
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
        lock = target.parent / (target.name + ".lock")
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
        lock = target.with_name(target.name + ".lock")
        self._age(target, STALE_STATE_MAX_AGE_SECONDS + 60)
        self._age(lock, STALE_STATE_MAX_AGE_SECONDS + 60)

        update_started = threading.Event()
        allow_update = threading.Event()
        sweep_waiting_for_lock = threading.Event()
        original_acquire_lock = vars(sys.modules["_session_state"])["_acquire_lock"]

        def _acquire_lock(lock_file: Any) -> None:
            if threading.current_thread().name == "state-sweep":
                sweep_waiting_for_lock.set()
            original_acquire_lock(lock_file)

        monkeypatch.setattr("_session_state._acquire_lock", _acquire_lock)

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
    """会話破棄時の通常状態と計画名記録の一括削除。"""

    def test_state_and_title_are_deleted_but_locks_are_kept(self) -> None:
        update_state("sid", lambda current: {**current, "active": True})
        assert claim_session_title("sid", "plan") is True
        normal = state_path("sid")
        title = title_state_path("sid")

        assert clear_session_state("sid") is True

        assert not normal.exists()
        assert normal.with_name(normal.name + ".lock").exists()
        assert not title.exists()
        assert title.with_name(title.name + ".lock").exists()

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
