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
            "candidate_id": "c0001",
            "candidate_kind": "user-intervention",
            "analysis_group_hint": ["実際の是正要求"],
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


def test_candidate_events_excludes_runtime_generated_user_messages() -> None:
    """常駐処理の通知、定時prompt及び実行環境が挿入した本文を利用者介入から除く。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "main", "line": 2, "text": "Goal check-in: «目標» is still active"},
        {"kind": "user", "record": "main", "line": 3, "text": "Stop hook feedback:\n[目標]: incomplete evidence"},
        {"kind": "user", "record": "main", "line": 4, "text": "<local-command-stdout>Goal set</local-command-stdout>"},
        {"kind": "user", "record": "main", "line": 5, "text": "A session-scoped Stop hook is now active with条件"},
        {"kind": "user", "record": "main", "line": 6, "text": "実際の是正要求"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 6}]]
    assert candidates[-1]["excluded"]["runtime-inserted"] == 4


def test_candidate_events_groups_failures_sharing_a_cause_across_tool_calls() -> None:
    """呼び出しごとに一意な識別子が異なっても、同じ原因の失敗を1候補へ集約する。"""
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "tool": "toolu_01", "text": "Exit code 1\n詳細1"},
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "toolu_02", "text": "Exit code 2\n詳細2"},
        {"kind": "failed-tool", "record": "main", "line": 9, "tool": "toolu_03", "text": "Exit code 3\n詳細3"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 1
    assert candidates[0]["count"] == 3
    assert candidates[0]["locators"] == [
        {"record": "main", "line": 2},
        {"record": "main", "line": 5},
        {"record": "main", "line": 9},
    ]


def test_candidate_events_assigns_shared_locator_to_hook_notice() -> None:
    """hook通知と同じ位置の失敗は、発生源ごとの限定を持つhook通知として扱う。"""
    timeline = [
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 3,
            "tool": "toolu_01",
            "text": "PreToolUse:Bash hook error: 遮断",
        },
    ]
    hook_notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 3,
            "text": "遮断",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "block",
        },
    ]

    candidates = evidence._candidate_events(timeline, [], hook_notices)  # pylint: disable=protected-access

    assert [candidate["candidate_kind"] for candidate in candidates[:-1]] == ["hook-notice"]
    assert candidates[0]["occurrence_count"] == 1


def test_candidate_events_aggregates_each_kind_and_preserves_all_locators() -> None:
    """同種の失敗・警告・通知を集約し、重複位置を一度だけ保持する。

    位置が重複する警告とhook通知は、先に走査するhook通知の候補として一度だけ数える。
    """
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "tool": "Bash", "text": "失敗A\n詳細1"},
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "Bash", "text": "失敗A\n詳細2"},
    ]
    warnings = [
        {"kind": "warning", "record": "main", "line": 7, "text": "警告  A"},
        {"kind": "warning", "record": "main", "line": 8, "text": "警告 A"},
        {"kind": "warning", "record": "main", "line": 12, "text": "警告 A"},
    ]
    hook_notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 8,
            "text": "警告 A",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "block",
        },
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 9,
            "text": "遮断",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "block",
        },
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 10,
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
    assert by_kind["warning"]["locators"] == [
        {"record": "main", "line": 7},
        {"record": "main", "line": 12},
    ]
    assert by_kind["hook-notice"]["locators"] == [{"record": "main", "line": 9}]
    assert candidates[-1]["included_locator_count"] == 6
    assert candidates[-1]["excluded"]["hook-notice-informational"] == 1
