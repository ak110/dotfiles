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


def test_candidate_events_keeps_answers_marked_as_intervention() -> None:
    """選択肢の外の回答と自由記述を伴う回答を問題候補として残し、選択肢どおりの回答だけを除く。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "main", "line": 2, "text": "質問: 方針\n回答: 既存機構へ統合"},
        {
            "kind": "user",
            "record": "main",
            "line": 3,
            "text": "質問: 方針\n回答: 対象範囲を広げる",
            "answer_intervention": True,
        },
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 3}]]
    assert candidates[-1]["excluded"] == {"initial-request": 1, "question-answer": 1}


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


def test_candidate_events_excludes_runtime_generated_user_records() -> None:
    """実行環境が生成した標識を持つ利用者イベントを候補から除き、除外種類別へ計上する。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "main", "line": 2, "text": "注記", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 3, "text": "実際の是正要求"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 3}]]
    assert candidates[-1]["excluded"]["runtime-meta"] == 1


def test_candidate_events_excludes_hook_notices_without_tag() -> None:
    """区分を持たないhook通知を候補から除き、区分を持つ通知の扱いを変えない。"""
    hook_notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 1,
            "text": "規範本文の配送",
            "hook": None,
            "hook_name": "SessionStart",
            "tag": None,
        },
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 2,
            "text": "遮断",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "block",
        },
    ]

    candidates = evidence._candidate_events([], [], hook_notices)  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 2}]]
    assert candidates[-1]["excluded"]["hook-notice-untagged"] == 1


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


def test_candidate_events_includes_delegate_returns_that_report_failure() -> None:
    """工程の不成立を返却値で表した最終返却を、候補と`included_locators`の双方へ含める。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 20, "text": "status: needs_escalation\nreason: 認可の不足"},
        {
            "kind": "final-result",
            "record": "agent-2",
            "line": 30,
            "text": "status: analysis_failed\nreason: 記録の取得に失敗した",
        },
        {"kind": "final-result", "record": "agent-3", "line": 40, "text": "status: failed"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["candidate_kind"] for candidate in candidates[:-1]] == ["delegate-return"] * 3
    assert candidates[-1]["included_locators"] == [
        {"record": "agent-1", "line": 20},
        {"record": "agent-2", "line": 30},
        {"record": "agent-3", "line": 40},
    ]


def test_candidate_events_excludes_delegate_returns_that_report_success() -> None:
    """工程の成立を表す返却値と、`status`行を持たない最終返却を候補にしない。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 20, "text": "status: completed\noutput_file: /tmp/out.md"},
        {"kind": "final-result", "record": "agent-2", "line": 30, "text": "実装完了\n検証結果: 終了コード0、警告なし"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert not candidates[:-1]
    assert not candidates[-1]["included_locators"]
    assert not candidates[-1]["excluded"]


def test_candidate_events_aggregates_delegate_returns_sharing_a_reason() -> None:
    """同じ理由の差し戻しを1候補へ集約し、理由が異なる差し戻しを別の候補へ分ける。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 20, "text": "status: needs_escalation\nreason: 認可の不足"},
        {"kind": "final-result", "record": "agent-2", "line": 30, "text": "status: needs_escalation\nreason: 認可の不足"},
        {"kind": "final-result", "record": "agent-3", "line": 40, "text": "status: needs_escalation\nreason: 入力の欠落"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["count"] for candidate in candidates[:-1]) == [1, 2]
    assert candidates[-1]["count"] == 2
    assert candidates[-1]["included_locator_count"] == 3


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
