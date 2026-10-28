"""`atk wi process-loop`への追加指示の状態ファイルのテスト。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit import atk
from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._atk.wi import process_loop_watch as _pl_watch
from agent_toolkit._atk.wi.process_loop_test import _manifest, _targets
from agent_toolkit._testing.process_loop_support import isolate_process_loop_commands


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
    # 上限超過の本文が自動node IDへ展開されると、CIの詳細進捗の1行が本文長に比例して伸びる。
    ids=["over-limit", "blank"],
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
    notes, snapshots = _targets(tmp_path, monkeypatch, (0,))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    process_loop_log.append_instruction("既存のテストコードを先に読む")
    instructions: list[str] = []
    held_during_wait: list[list[str]] = []

    def fake_wait_for_changes(private_notes: pathlib.Path, target_repo_id: str | None, **_kwargs: Any) -> bool:
        del target_repo_id
        held_during_wait.append(process_loop_log.read_instructions())
        if len(held_during_wait) >= 2:
            raise KeyboardInterrupt
        (private_notes / "2026/10/arrived.md").write_text(
            f"---\ntype: awi\nstate: inbox\ntarget_repo: {snapshots[0].repository.repo_id}\n---\n\n# 作業\n", encoding="utf-8"
        )
        return True

    def execute(_args: Any, _cwd: pathlib.Path, prompt: str, _env: dict[str, str], **_kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt).read_text(encoding="utf-8"))
        instructions.append(data["instruction"])
        (notes / "2026/10/arrived.md").unlink()
        result = {snapshots[0].repository.repo_id: {"status": "completed", "writer_sessions": []}}
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": result}), encoding="utf-8")
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    monkeypatch.setattr(_pl_watch, "wait_for_start_permission", lambda *_args: True)
    monkeypatch.setattr(_pl_watch, "wait_for_changes", fake_wait_for_changes)

    with pytest.raises(SystemExit) as exc_info:
        atk.main(["wi", "process-loop", "--no-update", "--no-alerts"], home=tmp_path)

    assert exc_info.value.code == 0
    assert held_during_wait[0] == ["既存のテストコードを先に読む"]
    assert instructions == ["既存のテストコードを先に読む"]
    assert held_during_wait[1] == []
    assert process_loop_log.read_instructions() == []
