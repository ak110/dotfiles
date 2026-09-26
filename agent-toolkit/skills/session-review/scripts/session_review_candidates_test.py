"""session_review_evidenceの一次選別候補生成を検証する。"""

import json
import pathlib

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
    assert {item["line"] for item in by_kind["tool-failure"]} == {2, 5}


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


def test_candidate_events_excludes_delegate_returns_that_only_report_success() -> None:
    """成功の定型形式だけの返却を除外し、想定外事象、未解決の指摘、不適合の判定及び自由記述を持つ返却は残す。"""
    excluded_texts = [
        "status: completed\noutput_file: /tmp/out.md",
        "status: completed\nreviewed_head: abc1234\nunresolved: 0",
        "```text\n統合完了\nmerged_head: abc1234\n```",
        "実装完了\n検証結果: 終了コード0、警告なし",
        "```text\n判定: 合格\nround: 1\n```",
        "## 判定結果: 適合\n**判定1（読者適合）: 適合**。根拠は次のとおり。",
    ]
    kept_texts = [
        "統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した",
        "status: completed\nreviewed_head: abc1234\nunresolved: 2",
        "判定1: 適合\n判定2: 不適合。参照先の見出しが無い",
        "## 判定結果: 一部不適合\n根拠を示す",
        "調査結果を報告する。対象の関数は3件だった。",
        "受け取り済みの結果を返し直す。\nstatus: completed\nunresolved: 0",
    ]
    timeline = [
        {"kind": "final-result", "record": f"agent-{index}", "line": 10, "text": text}
        for index, text in enumerate([*excluded_texts, *kept_texts])
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["text"] for candidate in candidates[:-1]) == sorted(kept_texts)
    assert candidates[-1]["excluded"] == {"normal-delegate-return": len(excluded_texts)}


def test_delegate_completion_values_are_defined_by_task_documents() -> None:
    """除外に使う完了値が、委譲先のタスク文書が返却値として定める語と一致し続けることを確かめる。"""
    share = pathlib.Path(evidence.__file__).resolve().parents[3] / "share"
    documents = "\n".join(path.read_text(encoding="utf-8") for path in sorted(share.glob("*.md")))
    lines = set(documents.splitlines())

    missing = [
        value
        for value in evidence._DELEGATE_COMPLETION_VALUES  # pylint: disable=protected-access
        if value not in lines and f"`{value}`" not in documents
    ]

    assert not missing


def test_candidate_events_includes_delegate_returns_without_status_line() -> None:
    """委譲先の`status`行を持たない自由記述の最終返却を候補へ含め、メイン記録の最終出力は候補にしない。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 30, "text": "調査結果を報告する。\n対象の関数は3件だった。"},
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
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "Bash", "text": "失敗A\n詳細1"},
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


def test_first_human_message_after_automated_start_is_intervention() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "<agent-toolkit-auto-inserted>自動起動"},
        {"kind": "user", "record": "main", "line": 2, "text": "人間の指摘"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert candidates[0]["locators"] == [{"record": "main", "line": 2}]
    assert candidates[-1]["excluded"] == {"runtime-inserted": 1}


def test_failed_tools_distinguish_operation_and_full_diagnostic() -> None:
    timeline = [
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 2,
            "text": "Exit code 1\n原因A",
            "tool_name": "Bash",
            "operation": "cmd A",
        },
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 3,
            "text": "Exit code 1\n原因B",
            "tool_name": "Bash",
            "operation": "cmd A",
        },
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 4,
            "text": "Exit code 1\n原因A",
            "tool_name": "Bash",
            "operation": "cmd B",
        },
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 5,
            "text": "Exit code 1\n原因A",
            "tool_name": "Bash",
            "operation": "cmd A",
        },
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["count"] for candidate in candidates[:-1]) == [1, 1, 2]
    assert candidates[-1]["included_locators"] == [{"record": "main", "line": line} for line in (2, 3, 4, 5)]


def test_hook_notice_keeps_outer_reasons_and_ignores_nested_delivery_tag() -> None:
    body = (
        '<agent-toolkit-auto-inserted source="hook/a" kind="notice">参考</agent-toolkit-auto-inserted>'
        '<agent-toolkit-auto-inserted source="hook/b" kind="warn">理由B'
        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="rules-main">規範</agent-toolkit-auto-inserted>'
        "</agent-toolkit-auto-inserted>"
        '<agent-toolkit-auto-inserted source="hook/c" kind="block">理由C</agent-toolkit-auto-inserted>'
    )

    keys = evidence._hook_notice_keys(body, "PreToolUse:Bash")  # pylint: disable=protected-access

    assert [(key.hook, key.tag) for key in keys] == [("hook/a", "notice"), ("hook/b", "warn"), ("hook/c", "block")]
    assert [key.kind_text for key in keys][0] == "参考"
    assert [key.kind_text for key in keys][2] == "理由C"


def _hook_failure(line: int, tool: str, reason: str, *, tag: str = "block") -> dict[str, object]:
    """hookが遮断したツール呼び出しの失敗記録を返す。"""
    return {
        "kind": "failed-tool",
        "record": "main",
        "line": line,
        "tool": f"toolu_{line}",
        "tool_name": tool,
        "operation": "{}",
        "text": (
            f"PreToolUse:{tool} hook error: [uv run hook.py pretooluse]: "
            f'<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="{tag}">'
            f"blocked: {reason}</agent-toolkit-auto-inserted>"
        ),
    }


def test_candidate_events_bounds_hook_failures_with_hook_notices_and_keeps_rare_tools() -> None:
    """hookの遮断によるツール失敗をhook通知として上限の対象にし、件数の少ないツールの遮断も残す。"""
    timeline = [
        _hook_failure(line * 10 + repeat, "Bash", f"理由{chr(0x30A2 + line)}の遮断")
        for line in range(7)
        for repeat in range(7 - line)
    ]
    timeline.append(_hook_failure(900, "TaskStop", "所有記録の無いタスクの停止"))

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kinds = {candidate["candidate_kind"] for candidate in candidates[:-1]}
    reasons = [candidate["text"] for candidate in candidates[:-1]]
    assert kinds == {"hook-notice"}
    assert len(candidates[:-1]) == evidence._HOOK_NOTICE_VARIANT_LIMIT + 1  # pylint: disable=protected-access
    assert any("所有記録の無いタスクの停止" in reason for reason in reasons)
    assert sum(candidate["occurrence_count"] for candidate in candidates[:-1]) + candidates[-1]["excluded"][
        "hook-notice-detail-budget"
    ] - sum(candidate["omitted_locator_count"] for candidate in candidates[:-1]) == len(timeline)


def test_candidate_events_merges_warnings_and_repeats_into_the_originating_hook_notice() -> None:
    """hook通知と同じ本文の警告、反復注記付きの通知、UUIDだけが異なる通知を、同じhook通知の候補へまとめる。"""
    session_ids = ["83adb05b-c346-4d6a-9bf7-0dd7016c18ec", "67b0dc56-6a64-4606-8a3b-229829a56427"]
    notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 10 + index,
            "text": evidence._normalize_candidate_kind_text(f"`atk agents wait`は位置引数を受理しない。対象: {session_id}"),  # pylint: disable=protected-access
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "warn",
        }
        for index, session_id in enumerate(session_ids)
    ]
    warnings = [
        {
            "kind": "warning",
            "record": "main",
            "line": 20,
            "text": f"`atk agents wait`は位置引数を受理しない。対象: {session_ids[0]}",
        },
        {"kind": "warning", "record": "main", "line": 21, "text": "無関係な警告の本文である"},
    ]

    candidates = evidence._candidate_events([], warnings, notices)  # pylint: disable=protected-access

    by_kind = {candidate["candidate_kind"]: candidate for candidate in candidates[:-1]}
    assert set(by_kind) == {"hook-notice", "warning"}
    assert by_kind["hook-notice"]["occurrence_count"] == 3
    assert by_kind["warning"]["text"] == "無関係な警告の本文である"


def test_hook_repeat_annotation_does_not_split_notice_kinds() -> None:
    """2件目以降の通知に付く反復注記を種類の本文から除き、1件目と同じ種類として数える。"""
    opening = '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">'
    first = f"{opening}対象の検査に該当した。</agent-toolkit-auto-inserted>"
    repeated = (
        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">'
        "対象の検査に該当した。\nこの通知は同一セッションで2件目である。</agent-toolkit-auto-inserted>"
    )

    keys = [
        evidence._hook_notice_keys(body, "PreToolUse:Bash")[0].kind_text  # pylint: disable=protected-access
        for body in (first, repeated)
    ]

    assert keys[0] == keys[1]


def _bash_failure(line: int, command: str, text: str = "Exit code 1") -> dict[str, object]:
    return {
        "kind": "failed-tool",
        "record": "main",
        "line": line,
        "tool": f"toolu_{line}",
        "tool_name": "Bash",
        "operation": json.dumps({"command": command}, ensure_ascii=False),
        "text": text,
    }


def test_candidate_events_excludes_empty_negative_search_results_of_claude_bash() -> None:
    """単一の検索が出力なしで一致0件を返した結果を除き、連結・パイプ・出力を伴う失敗と別の終了コードは残す。"""
    timeline = [
        _bash_failure(1, "rg -n -F 'a|b' agent-toolkit"),
        _bash_failure(2, "git grep -n -F needle -- docs"),
        _bash_failure(3, "test -e /tmp/absent"),
        _bash_failure(4, "rg -n needle docs | head"),
        _bash_failure(5, "rg -n needle docs 2>/dev/null"),
        _bash_failure(6, "rg -n needle docs && echo found"),
        _bash_failure(7, "rg -n needle docs", "Exit code 2\nrg: docs: No such file or directory"),
        _bash_failure(8, "python3 check.py"),
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept_lines = sorted(locator["line"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept_lines == [4, 5, 6, 7, 8]
    assert candidates[-1]["excluded"] == {"normal-negative-result": 3}


def test_candidate_events_does_not_spend_hook_budget_on_repeat_summaries() -> None:
    """1件目の本文の要約で届く2件目以降の通知を同じ通知として扱い、上限の枠を別の通知へ残す。"""
    variants = [
        ("対象のファイルの全文取得を先頭の範囲へ補正した。範囲は次の組で取得する", 6),
        ("対象のファイルの全文取得を先頭の範囲へ補正した。", 5),
        ("別の検査Aに該当した。", 4),
        ("別の検査Bに該当した。", 4),
        ("別の検査Cに該当した。", 4),
        ("別の検査Dに該当した。", 4),
    ]
    notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": index * 10 + repeat,
            "text": text,
            "hook": "agent-toolkit/pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "warn",
        }
        for index, (text, count) in enumerate(variants)
        for repeat in range(count)
    ]

    candidates = evidence._candidate_events([], [], notices)  # pylint: disable=protected-access

    texts = {candidate["text"] for candidate in candidates[:-1]}
    assert "別の検査Dに該当した。" in texts
    assert "対象のファイルの全文取得を先頭の範囲へ補正した。" not in texts
