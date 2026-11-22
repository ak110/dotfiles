"""`atk wi process-loop`への追加指示の状態ファイルのテスト。"""

import pathlib

import pytest

from agent_toolkit._atk.wi import process_loop_control as _pl_control
from agent_toolkit._atk.wi import process_loop_log
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
    """起動失敗時は指示を保持し、成功した次の起動だけへ配送する。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    process_loop_log.append_instruction("1件目")
    process_loop_log.append_instruction("2件目")

    with pytest.raises(OSError), process_loop_log.launch_instructions():
        raise OSError("CLIを起動できない")
    assert process_loop_log.read_instructions() == ["1件目", "2件目"]
    with process_loop_log.launch_instructions() as consumed:
        assert consumed.split(process_loop_log.INSTRUCTION_SEPARATOR) == ["1件目", "2件目"]
    assert not process_loop_log.read_instructions()
    with process_loop_log.launch_instructions() as consumed:
        assert not consumed


def test_discard_instructions_reports_count(monkeypatch, tmp_path) -> None:
    """破棄は保持件数を返し、保持が無い場合は0を返す。"""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    process_loop_log.append_instruction("1件目")

    assert process_loop_log.discard_instructions() == 1
    assert process_loop_log.discard_instructions() == 0
