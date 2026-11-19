"""セッション振り返りの試験（session_evidence_candidates_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_evidence_candidates as evidence_candidates
import session_evidence_hook_notices as evidence_hook_notices
import session_review_evidence as evidence


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

    candidates = evidence_candidates._candidate_events([], [], hook_notices)  # pylint: disable=protected-access

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

    candidates = evidence_candidates._candidate_events([], [], hook_notices)  # pylint: disable=protected-access

    assert [candidate["occurrence_count"] for candidate in candidates[:-1]] == [1]
    assert not candidates[-1]["excluded"]


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

    candidates = evidence_candidates._candidate_events(timeline, [], hook_notices)  # pylint: disable=protected-access

    assert [candidate["candidate_kind"] for candidate in candidates[:-1]] == ["hook-notice"]
    assert candidates[0]["occurrence_count"] == 1
    assert candidates[-1]["excluded"]["hook-notice-represented"] == 1


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

    candidates = evidence_candidates._candidate_events([], [], notices)  # pylint: disable=protected-access

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

    candidates = evidence_candidates._candidate_events(timeline, warnings, hook_notices)  # pylint: disable=protected-access

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

    candidates = evidence_candidates._candidate_events(timeline, warnings, [])  # pylint: disable=protected-access

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

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    failures = [candidate for candidate in candidates[:-1] if candidate["candidate_kind"] == "tool-failure"]
    assert len(failures) == 2
    assert all("hook error" not in candidate["event_key"][0] for candidate in failures)


def test_candidate_events_keeps_hook_failure_bodies_distinct_after_long_shared_prefix() -> None:
    prefix = "PreToolUse:Bash hook error: [hook.py]: " + "共通の診断" * 20
    timeline = [
        {"kind": "failed-tool", "record": "main", "line": 2, "text": prefix + "。原因A"},
        {"kind": "failed-tool", "record": "main", "line": 3, "text": prefix + "。原因B"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 2
    assert candidates[0]["analysis_group_hint"] != candidates[1]["analysis_group_hint"]


def test_candidate_events_counts_only_identical_candidate_identity_as_duplicate() -> None:
    """位置・種別・タグが同じ重複だけを機械除外件数へ加える。"""
    event = {"kind": "warning", "record": "main", "line": 8, "text": "同じ警告"}

    candidates = evidence_candidates._candidate_events([], [event, dict(event)], [])  # pylint: disable=protected-access

    assert len(candidates[:-1]) == 1
    assert candidates[-1]["excluded"] == {"duplicate-candidate": 1}


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

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

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

    keys = evidence_hook_notices._hook_notice_keys(body, "PreToolUse:Bash")  # pylint: disable=protected-access

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

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    kinds = {candidate["candidate_kind"] for candidate in candidates[:-1]}
    reasons = [candidate["text"] for candidate in candidates[:-1]]
    assert kinds == {"hook-notice"}
    assert len(candidates[:-1]) == evidence_candidates._HOOK_NOTICE_VARIANT_LIMIT + 1  # pylint: disable=protected-access
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
            "text": evidence_hook_notices._normalize_candidate_kind_text(  # pylint: disable=protected-access
                f"`atk agents wait`は位置引数を受理しない。対象: {session_id}"
            ),
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

    candidates = evidence_candidates._candidate_events([], warnings, notices)  # pylint: disable=protected-access

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
        evidence_hook_notices._hook_notice_keys(body, "PreToolUse:Bash")[0].kind_text  # pylint: disable=protected-access
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
    key = evidence_hook_notices._hook_notice_keys(  # pylint: disable=protected-access
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

    candidates = evidence_candidates._candidate_events([], [warning], [notice])  # pylint: disable=protected-access

    assert [(candidate["candidate_kind"], candidate["occurrence_count"]) for candidate in candidates[:-1]] == [
        ("hook-notice", 1)
    ]
    assert candidates[-1]["excluded"]["hook-notice-represented"] == 1


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

    candidates = evidence_candidates._candidate_events([], [], notices)  # pylint: disable=protected-access

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
            "text": (
                '警告: WI本文の表記診断: 入力元="原稿.md" 論理本文:80:88: '
                "口語表現 例示語（候補: 置換語） | 文脈: 前【例示語】後"
            ),
        },
        {
            "kind": "warning",
            "record": "main",
            "line": 2,
            "text": '警告: WI本文の表記診断: 入力元="エディター入力" 論理本文:3:5: ダッシュ — | 文脈: 前【—】後',
        },
        {
            "kind": "warning",
            "record": "main",
            "line": 3,
            "text": "警告: WI本文の表記診断: 2件\n標準エラー保存先: /tmp/diagnostics/stderr.txt",
        },
        {"kind": "warning", "record": "main", "line": 4, "text": "警告: 設定キー`model`の候補はありません。本文:1:1の口語表現"},
    ]

    candidates = evidence_candidates._candidate_events([], warnings, [])  # pylint: disable=protected-access

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
