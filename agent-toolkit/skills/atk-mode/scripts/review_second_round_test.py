from datetime import UTC, datetime, timedelta

import pytest
from agent_toolkit._agents_server import wait_summary
from agent_toolkit._atk import review_table_format as fmt
from agent_toolkit._testing.session_evidence_support import write_jsonl
from draft_review_rounds import _Collected, _Receipt, _request, collect
from review_second_round import _other_review_period, exec_period


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


def test_other_rounds_distinguish_rework_missing_and_extra_requirements() -> None:
    start = datetime(2026, 11, 27, tzinfo=UTC)
    next_turn = start + timedelta(minutes=1)
    reader = "状態: completed\n- 成果物: /doc.md\n未解決の指摘数: "
    screen = "レビューしたHEAD: aaaaaaa\n判定の回数: "
    plan = "対象資料: /plan.md\n判定: "
    post = "状態: completed\n未解決の指摘数: "
    bodies = {
        "reader": (reader + "1", reader + "0\n新しい要件: 別の要求"),
        "screen": (screen + "1\n判定: 不合格", screen + "2\n判定: 不合格\n区分: 前回から残存"),
        "plan": (plan + "修正要", plan + "合格"),
        "missing": (plan + "修正要", ""),
        "post": (post + "1", "再レビュー: 要\n根拠: 差し替え", post + "0"),
    }
    roles = {
        "reader": "reader-fit-review",
        "screen": "usability-review",
        "plan": "plan-review",
        "missing": "plan-review",
        "post": "external-write-review",
    }
    receipts = sorted(
        (
            _Receipt(start if i == 0 else next_turn, name, body, [], body, i)
            for name, pair in bodies.items()
            for i, body in enumerate(pair)
            if body
        ),
        key=lambda row: row.timestamp,
    )
    collected = _Collected(
        {name: name for name in roles},
        receipts,
        {
            "reader": [_request(next_turn, "対象を更新して再レビューせよ")],
            "post": [_request(next_turn, "変更箇所を再レビューしてください。")],
        },
        roles,
    )
    result = _other_review_period(collected, "reader-fit-review", start, next_turn)
    assert result["passed"] == result["second_classifications"]["要求追加"] == 1
    result = _other_review_period(collected, "usability-review", start, next_turn)
    assert result["failed"] == result["second_classifications"]["前回から残存"] == 1
    result = _other_review_period(collected, "plan-review", start, next_turn)
    assert result["pass_rate"] == "判定不能"
    assert result["unconfirmed_second"] == result["no_second"] == 1
    result = _other_review_period(collected, "external-write-review", start, next_turn)
    assert result["passed"] == 1
    assert result["unassigned"] == 0


@pytest.mark.parametrize("late_read", [False, True])
def test_review_round_retains_saved_time_and_uses_native_wait_only_once(tmp_path, late_read) -> None:
    start = datetime(2026, 11, 27, tzinfo=UTC)
    review_body = (
        f"{wait_summary.TERMINAL_LINE_PREFIX}session_id=reader label=reader-fit-review status=completed\n"
        f"{wait_summary.WAIT_BODY_START_PREFIX}reader\n"
        "状態: completed\n未解決の指摘数: {issues}\n- 成果物: /docs/source.md\n"
        f"{wait_summary.WAIT_BODY_END_PREFIX}reader\n"
    )
    sequence = 0

    def item(kind: str, **payload):
        nonlocal sequence
        sequence += 1
        return {
            "type": "event_msg",
            "timestamp": (start + timedelta(seconds=sequence)).isoformat(),
            "payload": {"type": "item_completed", "item": {"type": kind, "id": str(sequence), **payload}},
        }

    def shell(command: str, output: str, exit_code: int = 0):
        return item("CommandExecution", command=["/bin/bash", "-lc", command], aggregated_output=output, exit_code=exit_code)

    def request(tool: str, arguments: dict, result: dict):
        return item("McpToolCall", server="agents_server", tool=tool, arguments=arguments, result={"structuredContent": result})

    records = [
        request("start", {"subagent_md_path": "reader-fit-review"}, {"session_id": "reader"}),
        shell("/runtime/atk agents wait > '/saved/result.out' 2> '/saved/result.err'", ""),
        shell("cat '/saved/other.out'", review_body.format(issues=0)),
    ]
    if not late_read:
        records.append(shell("cat '/saved/result.out'; cat '/saved/result.err'", review_body.format(issues=1)))
    records.append(
        request("send_message", {"session_id": "reader", "instruction": "再レビューせよ"}, {"delivery": "reply_started"})
    )
    log = tmp_path / "review.jsonl"
    write_jsonl(
        log,
        [
            *records,
            shell("cat '/saved/result.out'", review_body.format(issues=1)),
            shell("/runtime/atk agents wait > '/saved/failed.out'", "", exit_code=1),
            shell("cat '/saved/failed.out'", review_body.format(issues=0)),
            shell("/runtime/atk agents wait", review_body.format(issues=0)),
        ],
    )
    observed = collect([log])
    assert len(observed.receipts) == 2
    assert observed.receipts[0].timestamp == start + timedelta(seconds=2)
    period = _other_review_period(observed, "reader-fit-review", start, start + timedelta(minutes=1))
    assert (period["passed"], period["failed"], period["first_pass"]) == (1, 0, 0)
    index = tmp_path / "index.json"
    for _ in range(2):
        assert collect([log], index=index) == observed
