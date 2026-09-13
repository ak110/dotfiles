"""session_review_evidenceの一次選別候補生成を検証する。"""

import session_review_evidence as evidence


def test_candidate_events_excludes_non_interventions_and_reports_counts() -> None:
    """委譲入力、環境挿入、回答及び初期要求を決定的に除外する。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "agent-1", "line": 1, "text": "委譲入力"},
        {"kind": "user", "record": "main", "line": 2, "text": "<normative-context>規範"},
        {"kind": "user", "record": "main", "line": 3, "text": "質問: 選択\n回答: 推奨"},
        {"kind": "user", "record": "main", "line": 4, "text": "実際の是正要求"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert candidates[:-1] == [
        {
            "kind": "candidate",
            "candidate_kind": "user-intervention",
            "event_key": ["実際の是正要求"],
            "count": 1,
            "locators": [{"record": "main", "line": 4}],
            "text": "実際の是正要求",
        }
    ]
    assert candidates[-1]["excluded"] == {
        "delegated-record": 1,
        "initial-request": 1,
        "question-answer": 1,
        "runtime-inserted": 1,
    }


def test_candidate_events_aggregates_each_kind_and_preserves_all_locators() -> None:
    """同種の失敗・警告・通知を集約し、重複位置を一度だけ保持する。"""
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "tool": "Bash", "text": "失敗A\n詳細1"},
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "Bash", "text": "失敗A\n詳細2"},
    ]
    warnings = [
        {"kind": "warning", "record": "main", "line": 7, "text": "警告  A"},
        {"kind": "warning", "record": "main", "line": 8, "text": "警告 A"},
    ]
    hook_notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 8,
            "text": "警告 A",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "notice",
        },
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 9,
            "text": "通知",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "notice",
        },
    ]

    candidates = evidence._candidate_events(timeline, warnings, hook_notices)  # pylint: disable=protected-access

    by_kind = {candidate["candidate_kind"]: candidate for candidate in candidates[:-1]}
    assert by_kind["escalation"]["count"] == 2
    assert by_kind["escalation"]["locators"] == [
        {"record": "main", "line": 2},
        {"record": "main", "line": 5},
    ]
    assert by_kind["warning"]["count"] == 2
    assert by_kind["hook-notice"]["locators"] == [{"record": "main", "line": 9}]
    assert candidates[-1]["included_locator_count"] == 5
