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
from typing import cast

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _session_state import (  # noqa: E402  # pylint: disable=wrong-import-position,import-error
    read_state,
    state_path,
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


def test_session_state_persists_new_codex_flags() -> None:
    """FB[4]: 新規追加フラグ(plan_codex_delegate_invoked/blocked, recorded_codex_thread_id)が永続化されることを確認する。"""
    session_id = "test-fb4-session"

    def _set(state: dict) -> dict | None:
        state["plan_codex_delegate_invoked"] = True
        state["plan_codex_delegate_blocked"] = False
        state["recorded_codex_thread_id"] = "th_test123"
        return state

    assert update_state(session_id, _set) is True
    state = read_state(session_id)
    assert state["plan_codex_delegate_invoked"] is True
    assert state["plan_codex_delegate_blocked"] is False
    assert state["recorded_codex_thread_id"] == "th_test123"
