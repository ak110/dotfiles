import pytest

from agent_toolkit._atk import outcome


def test_value_result_does_not_repeat_success(capsys: pytest.CaptureFixture[str]) -> None:
    print('{"status":"completed","record_path":"result.json"}')
    outcome.report_success("外部コマンドが終了した", outcome.ResultKind.VALUE_OUTPUT)

    captured = capsys.readouterr()
    assert captured.out == '{"status":"completed","record_path":"result.json"}\n'
    assert not captured.err


def test_state_change_keeps_result(capsys: pytest.CaptureFixture[str]) -> None:
    outcome.report_success("1件をadoptedへ移した")

    captured = capsys.readouterr()
    assert captured.out == "成功: 1件をadoptedへ移した\n"
    assert not captured.err


def test_failure_and_warning_require_next_action() -> None:
    with pytest.raises(ValueError, match="次の操作"):
        outcome.report_failure("理由", next_action=" ")
    with pytest.raises(ValueError, match="次の操作"):
        outcome.report_warning("理由", next_action="")


def test_failure_line_is_followed_by_next_action(capsys: pytest.CaptureFixture[str]) -> None:
    outcome.report_failure("対象が無い", next_action="`atk wi list`で名前を確かめる")

    assert capsys.readouterr().err.splitlines() == ["失敗: 対象が無い", "次の操作: `atk wi list`で名前を確かめる"]


def test_warning_line_is_followed_by_next_action(capsys: pytest.CaptureFixture[str]) -> None:
    outcome.report_warning("自動削除に失敗した", next_action="対応不要（処理は継続した）", to_stderr=False)

    assert capsys.readouterr().out.splitlines() == ["警告: 自動削除に失敗した", "次の操作: 対応不要（処理は継続した）"]


def test_warning_can_identify_its_producer_without_changing_stream(capsys: pytest.CaptureFixture[str]) -> None:
    outcome.report_warning("確認が必要", next_action="対象を確かめる", source="fixture-source")
    captured = capsys.readouterr()
    assert not captured.out
    assert captured.err.splitlines() == [
        '<atk-auto source="fixture-source" kind="warn">',
        "警告: 確認が必要",
        "次の操作: 対象を確かめる",
        "</atk-auto>",
    ]
