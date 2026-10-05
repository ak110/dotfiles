"""session_review_evidenceの一次選別候補生成を検証する。"""

import json
import pathlib

import pytest
import session_review_evidence as evidence


def test_candidate_events_excludes_non_interventions_and_reports_counts() -> None:
    """委譲入力、環境挿入、回答および初期要求を決定的に除外する。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {"kind": "user", "record": "agent-1", "line": 1, "text": "委譲入力"},
        {"kind": "user", "record": "main", "line": 2, "text": "<normative-context>規範", "runtime_inserted": True},
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
    """process-loopの通知、定時promptおよび実行環境が挿入した本文をユーザー介入から除く。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "初期要求"},
        {
            "kind": "user",
            "record": "main",
            "line": 2,
            "text": "Goal check-in: «目標» is still active",
            "runtime_inserted": True,
        },
        {
            "kind": "user",
            "record": "main",
            "line": 3,
            "text": "Stop hook feedback:\n[目標]: incomplete evidence",
            "runtime_inserted": True,
        },
        {
            "kind": "user",
            "record": "main",
            "line": 4,
            "text": "<local-command-stdout>Goal set</local-command-stdout>",
            "runtime_inserted": True,
        },
        {
            "kind": "user",
            "record": "main",
            "line": 5,
            "text": "A session-scoped Stop hook is now active with条件",
            "runtime_inserted": True,
        },
        {"kind": "user", "record": "main", "line": 6, "text": "実際の是正要求"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 6}]]
    assert candidates[-1]["excluded"]["runtime-inserted"] == 4


def test_candidate_events_excludes_runtime_generated_user_records() -> None:
    """実行環境が生成した標識を持つユーザーイベントを候補から除き、除外種類別へ計上する。"""
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


def test_candidate_events_reports_no_detail_budget_exclusion_without_omitted_notices() -> None:
    """同じ種類の発生が1件だけのhook通知では、省略が無いため`hook-notice-detail-budget`を除外件数へ載せない。

    値0の区分が残ると、`candidates.md`の「候補から除いた件数」が除外の起きたように読める。
    """
    hook_notices = [
        {
            "kind": "hook-notice",
            "record": "main",
            "line": 3,
            "text": "遮断",
            "hook": "pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "block",
        }
    ]

    candidates = evidence._candidate_events([], [], hook_notices)  # pylint: disable=protected-access

    assert [candidate["occurrence_count"] for candidate in candidates[:-1]] == [1]
    assert not candidates[-1]["excluded"]


def test_candidate_events_separates_failures_with_different_exit_codes_and_diagnostics() -> None:
    """失敗署名を構成する終了コードと診断が異なる失敗は別候補にする。"""
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "tool": "toolu_01", "text": "Exit code 1\n詳細1"},
        {"kind": "failed-tool", "record": "main", "line": 5, "tool": "toolu_02", "text": "Exit code 2\n詳細2"},
        {"kind": "failed-tool", "record": "main", "line": 9, "tool": "toolu_03", "text": "Exit code 3\n詳細3"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 3
    assert {candidate["candidate_kind"] for candidate in candidates[:-1]} == {"tool-failure"}
    assert sorted(candidate["count"] for candidate in candidates[:-1]) == [1, 1, 1]
    assert candidates[-1]["included_locators"] == [
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
        {
            "kind": "final-result",
            "record": "agent-4",
            "line": 50,
            "text": "状態: needs_escalation\n続行できない理由: 入力の欠落",
        },
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["candidate_kind"] for candidate in candidates[:-1]) == ["delegate-return"] * 2 + ["escalation"] * 2
    assert candidates[-1]["included_locators"] == [
        {"record": "agent-1", "line": 20},
        {"record": "agent-2", "line": 30},
        {"record": "agent-3", "line": 40},
        {"record": "agent-4", "line": 50},
    ]


def test_candidate_events_excludes_delegate_returns_that_only_report_success() -> None:
    """正常な完了だけを示す返却を除外し、想定外事象、未解決の指摘、不適合の判定および自由記述を持つ返却は残す。

    前置きの文に続けて`status: completed`を返す形も、1回の配送で終えた委譲先では正常な完了として除く。
    この形は振り返りの候補の大半を占めた雑音である。
    再開された委譲先の前置き付きの返却は、受け取り済みの報告を返し直した異常を前置きで述べた実例があるため残す。
    """
    excluded_texts = [
        "status: completed\noutput_file: /tmp/out.md",
        "受け取り済みの結果を返し直す。\nstatus: completed\nunresolved: 0",
        "Review complete with no unresolved issues found. Final output:\n\nstatus: completed\nunresolved: 0",
        "status: completed\nreviewed_head: abc1234\nunresolved: 0",
        "状態: completed\nレビューしたHEAD: abc1234\n未解決の指摘数: 0",
        "受け取り済みの結果を返し直す。\n状態: completed\n未解決の指摘数: 0",
        "```text\n統合完了\nmerged_head: abc1234\n```",
        "実装完了\n検証結果: 終了コード0、警告なし",
        "```text\n判定: 合格\nround: 1\n```",
        "## 判定結果: 適合\n**判定1（読者適合）: 適合**。根拠は次のとおり。",
    ]
    kept_texts = [
        "統合完了\nmerged_head: abc1234\n想定外事象: 統合後の検査が1件失敗した",
        "status: completed\nreviewed_head: abc1234\nunresolved: 2",
        "状態: completed\nレビューしたHEAD: abc1234\n未解決の指摘数: 2",
        "判定1: 適合\n判定2: 不適合。参照先の見出しが無い",
        "## 判定結果: 一部不適合\n根拠を示す",
        "調査結果を報告する。対象の関数は3件だった。",
        "前置き\nstatus: completed\nunresolved: 1",
    ]
    resumed_preface = (
        "I already retrieved all results. The completion report stands as issued.\n\nstatus: completed\nunresolved: 0"
    )
    timeline = [
        {"kind": "final-result", "record": f"agent-{index}", "line": 10, "text": text}
        for index, text in enumerate([*excluded_texts, *kept_texts])
    ]
    timeline.extend(
        [
            {"kind": "user", "record": "agent-resumed", "line": 1, "text": "レビューを依頼する"},
            {"kind": "user", "record": "agent-resumed", "line": 8, "text": "再開の指示"},
            {"kind": "final-result", "record": "agent-resumed", "line": 10, "text": resumed_preface},
        ]
    )

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["text"] for candidate in candidates[:-1]) == sorted([*kept_texts, resumed_preface])
    assert candidates[-1]["excluded"] == {"delegated-record": 2, "normal-delegate-return": len(excluded_texts)}


def test_candidate_events_excludes_successful_shell_delegation_returns() -> None:
    """コマンド実行の委譲で、報告した終了コードが全て0で失敗・警告・診断が無い返却だけを除く。

    シェル実行の委譲は自由記述で結果を返すため、終了コードと件数の記述から成否を判定する。
    非0の終了コード、1件以上の失敗・警告・診断、終了コードの記述が無い返却、およびシェル実行でない委譲の
    同じ本文は、本文の判断を要するため残す。
    """
    delivery = (
        "<agent-toolkit-auto-inserted> 次のコマンドを実行し、結果を報告せよ。"
        " 実行するコマンド: make test </agent-toolkit-auto-inserted>"
    )
    returns = {
        "shell-ok-1": '- 終了コード: `0`（正常終了）\n- 警告行・失敗行: なし（`failed":0`, `warning":0`, `diagnostics":0`）',
        "shell-ok-2": "- 終了コード: `exit=0`\n- pyfltrサマリー: 失敗0件・警告0件、`diagnostics` = 0件",
        "shell-nonzero": "- 終了コード: 1\n- 失敗行: test_x",
        "shell-warning": "- 終了コード: 0\n- 警告: 2件",
        "shell-diagnostics": '- exit=0\n- summary: {"diagnostics":3}',
        "shell-no-code": "実行しました。問題はありませんでした。",
        "shell-unexpected": "- 終了コード: 0\n想定外事象: 初回は権限不足で失敗し、再実行した",
    }
    timeline: list[dict[str, object]] = []
    for record, text in returns.items():
        timeline.append({"kind": "user", "record": record, "line": 1, "text": delivery})
        timeline.append({"kind": "final-result", "record": record, "line": 5, "text": text})
    timeline.append({"kind": "user", "record": "not-shell", "line": 1, "text": "調査を依頼する"})
    timeline.append({"kind": "final-result", "record": "not-shell", "line": 5, "text": returns["shell-ok-1"]})

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept = sorted(locator["record"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept == ["not-shell", "shell-diagnostics", "shell-no-code", "shell-nonzero", "shell-unexpected", "shell-warning"]
    assert candidates[-1]["excluded"]["normal-delegate-return"] == 2


def test_codex_project_instructions_do_not_change_delegation_kind_or_resume() -> None:
    """Codexの先頭注入を除いた配送本文からshell委譲と再開を判定する。"""
    record = "codex-shell"
    timeline = [
        {"kind": "user", "record": record, "line": 1, "text": "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>"},
        {
            "kind": "user",
            "record": record,
            "line": 2,
            "text": "<atk-auto>次のコマンドを実行し、結果を報告せよ。 実行するコマンド: true</atk-auto>",
        },
        {"kind": "final-result", "record": record, "line": 3, "text": "- 終了コード: `0`\n- 警告: 0件"},
    ]

    shell_records, resumed_records = evidence._delegation_record_kinds(timeline)  # pylint: disable=protected-access
    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert shell_records == {record}
    assert not resumed_records
    assert candidates[-1]["excluded"]["normal-delegate-return"] == 1
    assert all(candidate["candidate_kind"] != "delegate-return" for candidate in candidates[:-1])


def test_shell_delegation_marker_matches_agents_server_prompt() -> None:
    """シェル実行の委譲の判定に使う冒頭の文が、agents_serverが委譲先へ渡す指示本文と一致する。"""
    from agent_toolkit import agents_server_mcp  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    prompt = agents_server_mcp._shell_prompt("true", "終了コードを返す")  # pylint: disable=protected-access
    assert prompt.startswith(evidence._SHELL_DELEGATION_MARKER)  # pylint: disable=protected-access


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
            "text": f"状態: needs_escalation\n続行できない理由: {shared_prefix}認可の不足",
        },
        {
            "kind": "final-result",
            "record": "agent-2",
            "line": 30,
            "text": f"状態: needs_escalation\n続行できない理由: {shared_prefix}認可の不足",
        },
        {
            "kind": "final-result",
            "record": "agent-3",
            "line": 40,
            "text": f"状態: needs_escalation\n続行できない理由: {shared_prefix}入力の欠落",
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


def test_first_human_request_after_automated_start_is_initial_request() -> None:
    """process-loop以外の自動挿入で始まる記録では、最初の人間の発話を初期要求として除く。"""
    timeline = [
        {
            "kind": "user",
            "record": "main",
            "line": 1,
            "text": "<agent-toolkit-auto-inserted>自動起動",
            "runtime_inserted": True,
        },
        {"kind": "user", "record": "main", "line": 2, "text": "最初の人間の依頼"},
        {"kind": "user", "record": "main", "line": 3, "text": "後続の人間の指摘"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert candidates[0]["locators"] == [{"record": "main", "line": 3}]
    assert candidates[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1}


def test_failed_tools_distinguish_operation_and_full_diagnostic() -> None:
    timeline = [
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 2,
            "text": "Exit code 1\n原因A",
            "tool_name": "Bash",
            "operation": json.dumps({"command": "git push --dry-run --porcelain"}),
        },
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 3,
            "text": "Exit code 1\n原因B",
            "tool_name": "Bash",
            "operation": json.dumps({"command": "git push --dry-run --porcelain"}),
        },
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 4,
            "text": "Exit code 1\n原因A",
            "tool_name": "Bash",
            "operation": json.dumps({"command": "git grep needle"}),
        },
        {
            "kind": "failed-tool",
            "record": "main",
            "line": 5,
            "text": "Exit code 1\n原因A",
            "tool_name": "Bash",
            "operation": json.dumps({"command": "git push --dry-run --porcelain"}),
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


@pytest.mark.parametrize(
    "body",
    [
        "対象の検査に該当した。" + "理由の説明を続ける。" * 12,
        "未完了のバックグラウンドタスクが書く /home/user/work/output.txt を読む前に完了通知を待つこと。",
        "未完了のバックグラウンドタスクの出力を読む前に完了通知を待つこと。\nこの通知は同一セッションで2件目である。",
    ],
    ids=["longer-than-kind-length", "with-path", "with-repeat-annotation"],
)
def test_candidate_events_counts_a_hook_notice_and_its_warning_line_once(body: str) -> None:
    """1回のhook通知は、同じ位置に同じ本文の警告行を伴っても候補の発生1件として数える。

    通知は切り詰め・可変部の置換・反復注記の除去を経た種類本文だけを保持するため、
    警告本文を同じ正規化で比べないと代表判定が外れ、同じ通知が警告からもう1件数えられる。
    """
    key = evidence._hook_notice_keys(  # pylint: disable=protected-access
        f'<atk-auto source="pretooluse" kind="warn">{body}</atk-auto>', "PreToolUse:Bash"
    )[0]
    notice = {
        "kind": "hook-notice",
        "record": "main",
        "line": 3,
        "text": key.kind_text,
        "hook": key.hook,
        "hook_name": key.hook_name,
        "tag": key.tag,
    }
    warning = {"kind": "warning", "record": "main", "line": 3, "text": " ".join(body.split())}

    candidates = evidence._candidate_events([], [warning], [notice])  # pylint: disable=protected-access

    assert [(candidate["candidate_kind"], candidate["occurrence_count"]) for candidate in candidates[:-1]] == [
        ("hook-notice", 1)
    ]
    assert candidates[-1]["excluded"]["hook-notice-represented"] == 1


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
    """検索が出力なしで一致0件を返した結果を除き、パイプ以外の連結・出力を伴う失敗と別の終了コードは残す。

    パイプラインは最終段が検索で終了コード1、または検索を起動する`xargs`で終了コード123の場合を一致0件とする。
    """
    timeline = [
        _bash_failure(1, "rg -n -F 'a|b' agent-toolkit"),
        _bash_failure(2, "git grep -n -F needle -- docs"),
        _bash_failure(3, "test -e /tmp/absent"),
        _bash_failure(4, "rg -n needle docs | head"),
        _bash_failure(5, "rg -n needle docs 2>/dev/null"),
        _bash_failure(6, "rg -n needle docs && echo found"),
        _bash_failure(7, "rg -n needle docs", "Exit code 2\nrg: docs: No such file or directory"),
        _bash_failure(8, "python3 check.py"),
        _bash_failure(9, "fd -e md . docs | xargs -r rg -n -F needle", "Exit code 123"),
        _bash_failure(10, "git ls-files -z | xargs -0 grep -n needle", "Exit code 123"),
        _bash_failure(11, "cat notes.txt | rg -n needle"),
        _bash_failure(12, "fd -e tmp . | xargs rm", "Exit code 123"),
        _bash_failure(13, "rg -l needle | xargs cat", "Exit code 123"),
        _bash_failure(14, "rg -n needle docs || true", "Exit code 123"),
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept_lines = sorted(locator["line"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept_lines == [4, 5, 6, 7, 8, 12, 13, 14]
    assert candidates[-1]["excluded"] == {"normal-negative-result": 6}


def test_candidate_events_treats_quoted_operator_shaped_search_terms_as_data() -> None:
    """引用・エスケープで渡した演算子形の検索語はデータとして扱い、実際の演算子を伴う失敗は残す。"""
    timeline = [
        _bash_failure(1, "rg -n -F '<<<<<<<' docs"),
        _bash_failure(2, "rg -n -F ';' docs"),
        _bash_failure(3, 'rg -n -F "&&" docs && git grep -n -F "<<<<<<<" -- docs'),
        _bash_failure(4, r"rg -n -F \| docs"),
        _bash_failure(5, "git ls-files | rg -F '<<<<<<<'"),
        _bash_failure(6, "git ls-files -z | xargs -0 rg -n -F '&&'", "Exit code 123"),
        _bash_failure(7, "rg -n -F '<<<<<<<' docs && echo found"),
        _bash_failure(8, "rg -n -F '<<<<<<<' docs > found.txt"),
        _bash_failure(9, "rg -n -F '<<<<<<<' docs", "Exit code 2"),
        _bash_failure(10, "rg -n -F ';' docs", "Exit code 1\nrg: docs: No such file or directory"),
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept_lines = sorted(locator["line"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept_lines == [7, 8, 9, 10]
    assert candidates[-1]["excluded"] == {"normal-negative-result": 6}


def test_public_bundle_excludes_claude_wait_continuations(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Bashの記録を公開bundleで処理し、包装と出力形式が違う継続を同じ区分へ集計する。"""
    entries = []
    cases = [
        ("atk agents wait", 3),
        ("/repo/agent-toolkit/bin/atk agents wait", 3),
        ("bash -lc 'atk agents wait'", 3),
        ("sh -c 'atk agents wait'", 3),
        ("atk agents wait && false", 3),
        ("atk agents wait", 2),
    ]
    for index, (command, code) in enumerate(cases):
        call_id = f"toolu_{index}"
        entries.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}},
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": call_id,
                                "is_error": True,
                                "content": f'Exit code {code}\n{{"status": "running"}}',
                            },
                        ],
                    },
                },
            ]
        )
    transcript = tmp_path / "claude-wait.jsonl"
    transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    records = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert {locator["line"] for item in records[:-1] for locator in item["locators"]} == {10, 12}
    assert records[-1]["excluded"]["normal-nonterminal-result"] == 4


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


def test_candidate_events_excludes_wi_body_style_diagnostics() -> None:
    """`atk wi add`・`edit`の本文表記診断の警告を除き、同じ語を含む別の警告は残す。

    表記診断は起草者が保存前に処置する警告で、振り返りで判定すべき事象を持たない。
    """
    warnings = [
        {
            "kind": "warning",
            "record": "main",
            "line": 1,
            "text": "警告: 本文:80:88: 口語表現 例示語（候補: 置換語）",
        },
        {"kind": "warning", "record": "main", "line": 2, "text": "警告: 本文:3:5: ダッシュ —"},
        {
            "kind": "warning",
            "record": "main",
            "line": 3,
            "text": "警告: WI本文の表記診断: 2件\n標準エラー保存先: /tmp/diagnostics/stderr.txt",
        },
        {"kind": "warning", "record": "main", "line": 4, "text": "警告: 設定キー`model`の候補はありません。本文:1:1の口語表現"},
    ]

    candidates = evidence._candidate_events([], warnings, [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 4}]]
    assert candidates[-1]["excluded"] == {"wi-style-diagnostic": 3}


def test_codex_record_excludes_wi_style_summary_but_keeps_other_warning(tmp_path: pathlib.Path) -> None:
    """Codexの実行結果を経由しても、WI表記診断だけを候補から除く。"""
    entries = [
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-1",
                "arguments": json.dumps({"cmd": "atk wi add --dry-run"}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-1",
                "output": json.dumps({"stderr": "警告: WI本文の表記診断: 2件"}, ensure_ascii=False),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-2",
                "arguments": json.dumps({"cmd": "other-command"}),
            },
        },
        {
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call_output",
                "call_id": "call-2",
                "output": json.dumps({"stderr": "警告: 別の警告"}, ensure_ascii=False),
            },
        },
    ]
    transcript = tmp_path / "codex.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]

    assert not any("WI本文の表記診断" in candidate.get("text", "") for candidate in candidates)
    assert any("別の警告" in candidate.get("text", "") for candidate in candidates)


@pytest.mark.parametrize(
    ("body", "shell"),
    [
        ("状態: completed\n未解決の指摘数: 0", False),
        ("status: completed\nunresolved: 0", False),
        ("統合完了", False),
        ("実装完了\n検証結果: 成功", False),
        ("判定: 合格", False),
        ("判定1: 適合\n判定2: 適合", False),
        ("終了コード: 0\n警告なし", True),
    ],
)
def test_candidate_keeps_improvement_in_every_normal_return(body: str, shell: bool) -> None:
    """完了・適合・成功したshellの返却でも、字下げ付き改善点を正常除外より先に保持する。"""
    note = "  気付いた改善点: CLIの回避操作を繰り返した。"
    text = body + "\n" + note
    timeline = [{"kind": "final-result", "record": "agent-case", "line": 2, "text": text}]
    if shell:
        timeline.insert(
            0,
            {
                "kind": "user",
                "record": "agent-case",
                "line": 1,
                "text": (
                    "<agent-toolkit-auto-inserted> 次のコマンドを実行し、結果を報告せよ。"
                    " 実行するコマンド: make test </agent-toolkit-auto-inserted>"
                ),
            },
        )
    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access
    assert len(candidates[:-1]) == 1
    assert candidates[0]["candidate_kind"] == "delegate-return"
    assert candidates[0]["text"] == text
    assert candidates[-1]["excluded"].get("normal-delegate-return", 0) == 0


@pytest.mark.parametrize(
    ("goal_event", "expected_excluded"),
    [
        # Claude Codeはスラッシュコマンドを生成本文として記録し、`_event`が実行環境の挿入へ分類する。
        (
            {
                "text": "<command-name>/goal</command-name>\n"
                "<command-args>`agent-toolkit:process-wi`を完遂してください。</command-args>",
                "runtime_inserted": True,
            },
            {"runtime-inserted": 2},
        ),
        # Codexは前置きのない本文として記録し、起動の目的文を初期要求として除く。
        (
            {"text": "/goal `agent-toolkit:process-wi`を完遂してください。", "runtime_inserted": False},
            {"runtime-inserted": 1, "initial-request": 1},
        ),
    ],
    ids=["claude", "codex"],
)
def test_human_intervention_after_process_loop_start_remains_candidate(
    goal_event: dict[str, object], expected_excluded: dict[str, int]
) -> None:
    """process-loopが自動起動した記録では、最初の人間の発話を初期要求へ除かず介入の候補に残す。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "<atk-auto>規範", "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 2, **goal_event},
        {"kind": "user", "record": "main", "line": 3, "text": "途中で割り込んだ人間の指摘"},
    ]

    candidates = evidence._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert [candidate["locators"] for candidate in candidates[:-1]] == [[{"record": "main", "line": 3}]]
    assert candidates[-1]["excluded"] == expected_excluded


def test_process_loop_goal_matches_launch_prompt_body() -> None:
    """起動の判定に使う目的文が、process-loopが子セッションへ渡す目的文と一致する。"""
    from agent_toolkit._atk.wi import process_loop  # pylint: disable=import-outside-toplevel

    prompt = process_loop._build_process_loop_prompt()  # pylint: disable=protected-access

    assert evidence._PROCESS_WI_GOAL_BODY in prompt  # pylint: disable=protected-access
