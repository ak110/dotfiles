from agent_toolkit._atk import review_table_format as fmt
from review_second_round import exec_period


def _row(round_value: int, issue: str, response: str = "", ignored: str = "") -> list[str]:
    return [str(round_value), "exec-review", "module", issue, "", response, ignored]


def test_second_round_counts_only_completed_or_rejected_tables() -> None:
    response = fmt.response_with_history("応答")
    complete = response + "\n" + fmt.bind_recheck("再確認: ラウンド2 完了", 1, response)
    plain = "再確認: ラウンド2 完了"
    tables = [
        [_row(1, "指摘", complete), _row(2, fmt.carried_issue("残り", 1), ignored=fmt.withdrawn_reason(1, 2))],
        [_row(1, "初回"), _row(2, "区分: 修正に起因\n指摘")],
        [_row(1, "完了", complete), _row(1, "未判定")],
        [_row(1, "別の応答", response + "\n" + plain)],
    ]
    result = exec_period(tables)
    assert (result["passed"], result["failed"], result["undetermined"], result["pass_rate"]) == (1, 1, 2, 0.5)
    assert result["later_classifications"]["修正に起因"] == 1
    assert result["later_classifications"]["前回から残存"] == 0
    assert exec_period([])["pass_rate"] == "判定不能"
