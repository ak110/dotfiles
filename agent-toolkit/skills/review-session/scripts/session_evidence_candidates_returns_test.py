"""セッション振り返りの試験（session_evidence_candidates_returns_test）。"""

from __future__ import annotations

import pytest
import session_evidence_candidates as evidence_candidates

from agent_toolkit._agents_server import tool_descriptions


def test_candidate_events_separates_escalations_from_unsuccessful_delegate_returns() -> None:
    """上位判断を求める返却だけをエスカレーションとし、通常の不成功返却から分離する。

    `続行できない理由:`の行を持つ返却に加え、過去の記録に残る旧形式の状態値の返却も読み取り互換としてエスカレーションとする。
    """
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
        {
            "kind": "final-result",
            "record": "agent-5",
            "line": 60,
            "text": "未解決の指摘数: 0\n続行できない理由: 認可範囲の外にある不良",
        },
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["candidate_kind"] for candidate in candidates[:-1]) == ["delegate-return"] * 2 + ["escalation"] * 3
    assert candidates[-1]["included_locators"] == [
        {"record": "agent-1", "line": 20},
        {"record": "agent-2", "line": 30},
        {"record": "agent-3", "line": 40},
        {"record": "agent-4", "line": 50},
        {"record": "agent-5", "line": 60},
    ]


def test_candidate_events_excludes_delegate_returns_that_only_report_success() -> None:
    """正常な完了だけを示す返却を除外し、想定外事象、未解決の指摘、不適合の判定および自由記述を持つ返却は残す。

    前置きの文に続けて`status: completed`を返す形も、1回の配送で終えた委譲先では正常な完了として除く。
    この形は恒久対策を要しないまま振り返りの候補の大半を占めていた。
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

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

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

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kept = sorted(locator["record"] for candidate in candidates[:-1] for locator in candidate["locators"])
    assert kept == ["not-shell", "shell-diagnostics", "shell-no-code", "shell-nonzero", "shell-unexpected", "shell-warning"]
    assert candidates[-1]["excluded"]["normal-delegate-return"] == 2


@pytest.mark.parametrize(
    "inserted",
    [
        "# AGENTS.md instructions for /repo\n\n<INSTRUCTIONS>",
        "<environment_context>設定</environment_context>",
        "<atk-auto source='rules_context'>規範</atk-auto>",
    ],
)
def test_codex_project_instructions_do_not_change_delegation_kind_or_resume(inserted: str) -> None:
    """Codexの先頭注入を除いた配送本文からshell委譲と再開を判定する。"""
    record = "codex-shell"
    timeline = [
        {"kind": "user", "record": record, "line": 1, "text": inserted, "runtime_inserted": True},
        {
            "kind": "user",
            "record": record,
            "line": 2,
            "text": "<atk-auto>次のコマンドを実行し、結果を報告せよ。 実行するコマンド: true</atk-auto>",
        },
        {"kind": "final-result", "record": record, "line": 3, "text": "- 終了コード: `0`\n- 警告: 0件"},
    ]

    shell_records, resumed_records = evidence_candidates._delegation_record_kinds(timeline)  # pylint: disable=protected-access
    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert shell_records == {record}
    assert not resumed_records
    assert candidates[-1]["excluded"]["normal-delegate-return"] == 1
    assert all(candidate["candidate_kind"] != "delegate-return" for candidate in candidates[:-1])
    timeline.append({"kind": "user", "record": record, "line": 4, "text": "再開して確認する"})
    assert evidence_candidates._delegation_record_kinds(timeline) == ({record}, {record})  # pylint: disable=protected-access


def test_shell_delegation_marker_matches_agents_server_prompt() -> None:
    """シェル実行の委譲の判定に使う冒頭の文が、agents_serverが委譲先へ渡す指示本文と一致する。"""

    prompt = tool_descriptions.shell_prompt("true", "終了コードを返す")
    assert prompt.startswith(evidence_candidates._SHELL_DELEGATION_MARKER)  # pylint: disable=protected-access


def test_candidate_events_includes_delegate_returns_without_status_line() -> None:
    """委譲先の`status`行を持たない自由記述の最終返却を候補へ含め、メイン記録の最終出力は候補にしない。"""
    timeline = [
        {"kind": "final-result", "record": "agent-1", "line": 30, "text": "調査結果を報告する。\n対象の関数は3件だった。"},
        {"kind": "final-result", "record": "main", "line": 40, "text": "対応を完了した。"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

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
            "text": f"続行できない理由: {shared_prefix}認可の不足",
        },
        {
            "kind": "final-result",
            "record": "agent-2",
            "line": 30,
            "text": f"続行できない理由: {shared_prefix}認可の不足",
        },
        {
            "kind": "final-result",
            "record": "agent-3",
            "line": 40,
            "text": f"続行できない理由: {shared_prefix}入力の欠落",
        },
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert sorted(candidate["count"] for candidate in candidates[:-1]) == [1, 2]
    assert candidates[-1]["count"] == 2
    assert candidates[-1]["included_locator_count"] == 3


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
    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access
    assert len(candidates[:-1]) == 1
    assert candidates[0]["candidate_kind"] == "delegate-return"
    assert candidates[0]["text"] == text
    assert candidates[-1]["excluded"].get("normal-delegate-return", 0) == 0
