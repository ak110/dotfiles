"""`atk wi process-loop`への追加指示の状態ファイルのテスト。"""

import pathlib
import subprocess
from typing import Any, cast

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi import readiness as _wi_readiness
from agent_toolkit._testing.process_loop_support import fake_run_with_remote_url, isolate_process_loop_commands
from agent_toolkit.atk_test import _setup_notes


@pytest.fixture(autouse=True)
def _isolate_process_loop_commands(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """外部コマンド・Claude設定・初期値のTTLをユーザー環境から分離する。"""
    isolate_process_loop_commands(monkeypatch, tmp_path)


_PROCESS_LOOP_INSTRUCTION_ENV = "AGENT_TOOLKIT_PROCESS_LOOP_INSTRUCTION"


def test_instruction_append_rejects_duplicate_and_over_limit(monkeypatch, tmp_path) -> None:
    """完全一致の再投入は追記せず、上限超過の投入は拒否する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert process_loop_log.append_instruction("既存のテストコードを先に読む") == (
        True,
        "保持中の追加指示は1件である",
    )
    appended, summary = process_loop_log.append_instruction("既存のテストコードを先に読む")
    assert appended is False
    assert summary == "同じ本文が保持済みである"

    appended, summary = process_loop_log.append_instruction("あ" * process_loop_log.INSTRUCTION_MAX_CHARS)
    assert appended is False
    assert "上限" in summary
    assert process_loop_log.read_instructions() == ["既存のテストコードを先に読む"]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("あ" * (process_loop_log.INSTRUCTION_MAX_CHARS + 1), "instruct-cancel"),
        ("   ", "本文を記入して再実行する"),
    ],
)
def test_instruct_rejection_reports_next_action(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    body: str,
    expected: str,
) -> None:
    """保持の上限超過と空の本文は、破棄か書き直しを次の操作として返して終了コード1で終わる。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    with pytest.raises(SystemExit) as exc_info:
        _pl_control.cmd_process_loop_instruct(body)  # pylint: disable=protected-access

    assert exc_info.value.code == 1
    stderr = capsys.readouterr().err
    assert stderr.startswith("失敗: 追加指示を保持しなかった: ")
    assert expected in stderr.split("\n次の操作: ", 1)[1]


def test_instructions_are_consumed_once(monkeypatch, tmp_path) -> None:
    """消費した追加指示は次の取得で残らない。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    process_loop_log.append_instruction("1件目")
    process_loop_log.append_instruction("2件目")

    consumed = process_loop_log.consume_instructions()

    assert consumed.split(process_loop_log.INSTRUCTION_SEPARATOR) == ["1件目", "2件目"]
    assert process_loop_log.read_instructions() == []
    assert process_loop_log.consume_instructions() == ""


def test_discard_instructions_reports_count(monkeypatch, tmp_path) -> None:
    """破棄は保持件数を返し、保持が無い場合は0を返す。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    process_loop_log.append_instruction("1件目")

    assert process_loop_log.discard_instructions() == 1
    assert process_loop_log.discard_instructions() == 0


def test_instruction_is_consumed_only_when_a_session_launches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """セッションを起動する反復だけが追加指示を消費し、0件で待機する反復は保持する。"""
    _setup_notes(tmp_path)
    myrepo = tmp_path / "myrepo"
    myrepo.mkdir()
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    process_loop_log.append_instruction("既存のテストコードを先に読む")
    claude_calls: list[dict[str, Any]] = []
    monkeypatch.setattr(subprocess, "run", fake_run_with_remote_url(myrepo, claude_calls, 0))

    count_calls: list[int] = []

    def fake_count_pending_entries(private_notes: pathlib.Path, target_repo: str | None = None) -> int:
        del private_notes, target_repo
        count_calls.append(len(count_calls))
        # 1回目は0件で待機へ入り、2回目で1件を返してセッションを起動する。
        return 1 if len(count_calls) == 2 else 0

    held_during_wait: list[list[str]] = []

    def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None) -> None:
        del private_notes, target_repo_id
        held_during_wait.append(process_loop_log.read_instructions())
        if len(held_during_wait) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(_wi_readiness, "count_pending_entries", fake_count_pending_entries)
    monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "process-loop", f"--target-repo={myrepo}", "--no-update"], home=tmp_path)

    assert exc_info.value.code == 0
    assert held_during_wait[0] == ["既存のテストコードを先に読む"]
    assert len(claude_calls) == 1
    launch_env = cast(dict[str, str], claude_calls[0]["env"])
    assert launch_env[_PROCESS_LOOP_INSTRUCTION_ENV] == "既存のテストコードを先に読む"
    assert held_during_wait[1] == []
    assert process_loop_log.read_instructions() == []
