import io

import pytest

from agent_toolkit._common import next_action


@pytest.mark.parametrize("value", ["", "  ", "\n"])
def test_blank_next_action_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="次の操作"):
        next_action.next_action_line(value)
    with pytest.raises(ValueError, match="次の操作"):
        _ = next_action.ActionableError("理由", next_action=value)
    with pytest.raises(ValueError, match="次の操作"):
        next_action.report("理由", next_action=value, stream=io.StringIO())


def test_actionable_error_keeps_reason_as_str_and_formats_two_lines() -> None:
    error = next_action.ActionableError("対象が無い", next_action="`atk wi list`で名前を確かめる")

    assert str(error) == "対象が無い"
    assert isinstance(error, ValueError)
    assert error.message.splitlines() == ["対象が無い", "次の操作: `atk wi list`で名前を確かめる"]


def test_report_writes_reason_then_next_action_line() -> None:
    stream = io.StringIO()

    next_action.report("取得に失敗した", next_action="同じ引数で再実行する", stream=stream)

    assert stream.getvalue().splitlines() == ["取得に失敗した", "次の操作: 同じ引数で再実行する"]


def test_recover_from_failure_orders_recovery_before_retry_and_reporting() -> None:
    guide = next_action.recover_from_failure(
        component="試験対象",
        evidence="記録A",
        first_step="記録Aを読む",
        retry="未適用と分かる場合に限り操作Bを再試行する",
    )
    positions = [
        guide.index(step)
        for step in (
            "記録Aを読む",
            "原因を確かめる",
            "agent-basics:bugfix",
            "認可の範囲で回復する",
            "未適用と分かる場合に限り操作Bを再試行する",
            "回復結果を伝える",
        )
    ]
    assert positions == sorted(positions)
