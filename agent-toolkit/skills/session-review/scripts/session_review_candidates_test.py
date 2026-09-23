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
    assert candidates[0]["candidate_kind"] == "tool-failure"
    assert candidates[0]["count"] == 3
    assert candidates[0]["locators"] == [
        {"record": "main", "line": 2},
        {"record": "main", "line": 5},
        {"record": "main", "line": 9},
    ]


def test_candidate_events_classifies_auto_mode_denial_as_permission_denial() -> None:
    """auto mode classifierの拒否は、他の失敗と分かれた候補種別として抽出する。"""
    timeline = [
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 2,
            "tool": "toolu_01",
            "text": (
                "Permission for this action was denied by the Claude Code auto mode classifier. Reason: [Self-Modification]."
            ),
        },
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "toolu_02", "text": "Exit code 1\n詳細"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    by_kind: dict[str, list[dict[str, object]]] = {}
    for candidate in candidates[:-1]:
        by_kind.setdefault(candidate["candidate_kind"], []).extend(candidate["locators"])
    assert by_kind["permission-denial"] == [{"record": "main", "line": 2}]
    assert by_kind["tool-failure"] == [{"record": "main", "line": 5}, {"record": "main", "line": 2}]


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
    assert candidates[-1]["excluded"]["hook-notice-represented"] == 1


def test_candidate_events_separates_escalations_from_unsuccessful_delegate_returns() -> None:
    """上位判断を求める返却だけをエスカレーションとし、通常の不成功返却から分離する。"""
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

    assert [candidate["candidate_kind"] for candidate in candidates[:-1]] == ["delegate-return"] * 2 + ["escalation"]
    assert candidates[-1]["included_locators"] == [
        {"record": "agent-1", "line": 20},
        {"record": "agent-2", "line": 30},
        {"record": "agent-3", "line": 40},
    ]


def test_candidate_events_excludes_delegate_returns_that_report_success() -> None:
    """工程の成立を表す`status`値の最終返却を候補にしない。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 20, "text": "status: completed\noutput_file: /tmp/out.md"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert not candidates[:-1]
    assert not candidates[-1]["included_locators"]
    assert not candidates[-1]["excluded"]


def test_candidate_events_includes_delegate_returns_without_status_line() -> None:
    """委譲先の`status`行を持たない最終返却を候補へ含め、メイン記録の最終出力は候補にしない。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 30, "text": "実装完了\n検証結果: 終了コード0、警告なし"},
        {"kind": "final-result", "record": "main", "line": 40, "text": "対応を完了した。"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["candidate_kind"] for candidate in candidates[:-1]] == ["delegate-return"]
    assert candidates[-1]["included_locators"] == [{"record": "agent-1", "line": 30}]


def test_candidate_events_aggregates_delegate_returns_sharing_a_reason() -> None:
    """同じ理由の差し戻しを1候補へ集約し、理由が異なる差し戻しを別の候補へ分ける。"""
    shared_prefix = "確認に必要な条件 " * 15
    timeline = [
        {
            "kind": "final-result",
            "record": "agent-1",
            "line": 20,
            "text": f"status: needs_escalation\nreason: {shared_prefix}認可の不足",
        },
        {
            "kind": "final-result",
            "record": "agent-2",
            "line": 30,
            "text": f"status: needs_escalation\nreason: {shared_prefix}認可の不足",
        },
        {
            "kind": "final-result",
            "record": "agent-3",
            "line": 40,
            "text": f"status: needs_escalation\nreason: {shared_prefix}入力の欠落",
        },
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["count"] for candidate in candidates[:-1]) == [1, 2]
    assert candidates[-1]["count"] == 2
    assert candidates[-1]["included_locator_count"] == 3


def test_candidate_events_separates_block_reasons_after_shared_long_prefix() -> None:
    """定型接頭辞が同じblock通知も、理由が異なれば別候補へ分ける。"""
    prefix = "処理対象の検査 " * 15
    notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": line,
            "text": f"{prefix} block: {reason}",
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "block",
        }
        for line, reason in [
            (10, "対象ファイルが未読"),
            (11, "対象ファイルが未読"),
            (12, "対象の書込権限がない"),
            (13, ""),
        ]
    ]

    candidates = evidence._candidate_events([], [], notices)  # pylint: disable=protected-access

    assert sorted(candidate["occurrence_count"] for candidate in candidates[:-1]) == [1, 1, 2]
    assert candidates[-1]["count"] == 3
    assert {candidate["locators"][0]["line"] for candidate in candidates[:-1]} == {10, 12, 13}


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
    assert by_kind["tool-failure"]["count"] == 2
    assert by_kind["tool-failure"]["locators"] == [
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
    assert candidates[-1]["excluded"]["hook-notice-represented"] == 1


def test_candidate_events_keeps_distinct_kinds_at_the_same_locator() -> None:
    """同じ記録位置でも候補種別が異なる事象は別候補として保持する。"""
    timeline = [{"kind": "failed-tool", "record": "main", "line": 4, "text": "実行失敗"}]
    warnings = [{"kind": "warning", "record": "main", "line": 4, "text": "実行時警告"}]

    candidates = evidence._candidate_events(timeline, warnings, [])  # pylint: disable=protected-access

    assert {candidate["candidate_kind"] for candidate in candidates[:-1]} == {"tool-failure", "warning"}
    assert candidates[-1]["included_locator_count"] == 1
    assert candidates[-1]["included_locators"] == [{"record": "main", "line": 4}]


def test_candidate_events_classifies_hook_failures_from_the_reason_after_the_command_prefix() -> None:
    """hook起動コマンドが同じでも、後続の遮断理由が異なる失敗を別候補にする。"""
    prefix = "PreToolUse:Bash hook error: [uv run --project /plugin --locked hook.py]: "
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "text": prefix + "再帰検索は除外設定を反映しない"},
        {"kind": "failed-tool", "record": "main", "line": 3, "text": prefix + "python -cへ複数文を渡している"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    failures = [candidate for candidate in candidates[:-1] if candidate["candidate_kind"] == "tool-failure"]
    assert len(failures) == 2
    assert all("hook error" not in candidate["event_key"][0] for candidate in failures)


def test_candidate_events_keeps_hook_failure_bodies_distinct_after_long_shared_prefix() -> None:
    prefix = "PreToolUse:Bash hook error: [hook.py]: " + "共通の診断" * 20
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "text": prefix + "。原因A"},
        {"kind": "failed-tool", "record": "main", "line": 3, "text": prefix + "。原因B"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 2
    assert candidates[0]["analysis_group_hint"] != candidates[1]["analysis_group_hint"]


def test_candidate_events_counts_only_identical_candidate_identity_as_duplicate() -> None:
    """位置・種別・タグが同じ重複だけを機械除外件数へ加える。"""
    event = {"kind": "warning", "record": "main", "line": 8, "text": "同じ警告"}

    candidates = evidence._candidate_events([], [event, dict(event)], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 1
    assert candidates[-1]["excluded"] == {"duplicate-candidate": 1}
