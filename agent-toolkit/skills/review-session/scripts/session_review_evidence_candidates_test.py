"""セッション振り返りの試験（session_review_evidence_candidates_test）。"""

from __future__ import annotations

import pathlib

import pytest
import session_evidence_candidates as evidence_candidates
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import hook_attachment, read_jsonl
from agent_toolkit._testing.session_review_evidence_support import _bundle_candidates_and_evidence


def test_candidates_bound_hook_notice_variants_and_keep_each_emitter() -> None:
    """block/warn通知は発生源ごとに上位5種類の代表位置へ限定し、発生総数を保持する。"""
    notices: list[dict] = []
    line = 1
    for variant, count in enumerate(range(7, 0, -1)):
        for _ in range(count):
            notices.append(
                {
                    "kind": "hook-notice",
                    "record": "main",
                    "line": line,
                    "text": f"warn: variant-{variant}",
                    "hook": "agent-toolkit/pretooluse",
                    "hook_name": "PreToolUse:Bash" if variant % 2 == 0 else "PostToolUse:Bash",
                    "tag": "warn",
                }
            )
            line += 1
    notices.append(
        {
            "kind": "hook-notice",
            "record": "main",
            "line": line,
            "text": "block: another-emitter",
            "hook": "dotfiles/pretooluse",
            "hook_name": "PreToolUse:Write",
            "tag": "block",
        }
    )

    events = evidence_candidates._candidate_events([], [], notices)  # pylint: disable=protected-access
    candidates = [event for event in events if event["kind"] == "candidate"]
    first_emitter = [candidate for candidate in candidates if candidate["event_key"][0] == "agent-toolkit/pretooluse"]

    assert [candidate["occurrence_count"] for candidate in first_emitter] == [7, 6, 5, 4, 3]
    assert all(candidate["count"] == 1 and len(candidate["locators"]) == 1 for candidate in candidates)
    assert any(candidate["event_key"][0] == "dotfiles/pretooluse" for candidate in candidates)
    assert events[-1]["included_locator_count"] == 6
    assert events[-1]["excluded"] == {"hook-notice-detail-budget": 23}


def test_transcript_alias_selects_the_single_record_source(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--transcript`を位置引数と同じ単一記録を指定する手段として扱う。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])

    assert evidence.main(["--transcript", str(transcript)]) == 0
    assert read_jsonl(capsys)[0]["text"] == "入力"
    assert evidence.main([str(transcript), "--transcript", str(transcript)]) == 2


def test_candidates_exclude_runtime_inputs_before_selecting_initial_request() -> None:
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "環境情報", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 2, "text": "<skill>\n本文\n</skill>", "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 3, "text": "最初の依頼"},
        {"kind": "user", "record": "main", "line": 4, "text": "後続の訂正"},
        {"kind": "user", "record": "main", "line": 5, "text": "通常文中の <skill> という表記"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    items = [item for item in candidates if item["kind"] == "candidate"]
    assert sorted(item["locators"][0]["line"] for item in items) == [4, 5]
    assert candidates[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1, "runtime-meta": 1}


def test_candidates_exclude_boundary_marked_injections() -> None:
    """属性を伴う境界の要素で囲んだ自動注入本文を、実行環境の挿入として除外する。"""
    normative = '<normative-context source="agent-toolkit" kind="rules-main">\n条文\n</normative-context>'
    hook_notice = (
        '<agent-toolkit-hook-message source="agent-toolkit/rules_context" kind="notice">\n注記\n</agent-toolkit-hook-message>'
    )
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": normative, "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 2, "text": hook_notice, "runtime_inserted": True},
        {"kind": "user", "record": "main", "line": 3, "text": "最初の依頼"},
        {"kind": "user", "record": "main", "line": 4, "text": "後続の訂正"},
    ]

    candidates = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    items = [item for item in candidates if item["kind"] == "candidate"]
    assert sorted(item["locators"][0]["line"] for item in items) == [4]
    assert candidates[-1]["excluded"]["runtime-inserted"] == 2
    assert candidates[-1]["excluded"]["initial-request"] == 1


def test_candidates_exclude_initial_codex_skill_pair_without_hiding_later_intervention() -> None:
    """先頭スキル要求と対応本文を別区分で除外し、後続のユーザー介入を保持する。"""
    timeline = [
        {"kind": "user", "record": "main", "line": 1, "text": "環境情報", "runtime_generated": True},
        {"kind": "user", "record": "main", "line": 2, "text": "$agent-toolkit:process-wi"},
        {
            "kind": "user",
            "record": "main",
            "line": 3,
            "text": "<skill>\n<name>agent-toolkit:process-wi</name>\n本文は後段で省略",
        },
        {"kind": "user", "record": "main", "line": 4, "text": "途中で方針を変更する"},
    ]

    records = evidence_candidates._candidate_events(timeline, [], [])  # pylint: disable=protected-access

    candidates = [record for record in records if record["kind"] == "candidate"]
    assert [candidate["locators"] for candidate in candidates] == [[{"record": "main", "line": 4}]]
    assert records[-1]["excluded"] == {
        "initial-skill-body": 1,
        "initial-skill-request": 1,
        "runtime-meta": 1,
    }


@pytest.mark.parametrize(
    "injection", ["# AGENTS.md instructions\n規範", '<atk-auto source="runtime" kind="notice">注入</atk-auto>']
)
def test_bundle_selects_initial_request_after_runtime_injections(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], injection: str
) -> None:
    """Codexの先行注入と最初の依頼を候補から除き、後続是正と通常文の標識を保持する。"""
    texts = [injection, "最初の依頼", "後続の訂正", "通常文中の <skill> という表記"]
    records, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}} for text in texts],
    )

    assert sorted(locator["line"] for item in records if item["kind"] == "candidate" for locator in item["locators"]) == [3, 4]
    assert records[-1]["excluded"] == {"initial-request": 1, "runtime-inserted": 1}


@pytest.mark.parametrize("closing", ["", "\n</skill>"])
def test_bundle_selects_initial_skill_after_runtime_injections(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], closing: str
) -> None:
    """注入後の初期スキルと対応本文は元の区分で除き、後続の是正を保持する。"""
    texts = [
        "# AGENTS.md instructions\n規範",
        '<atk-auto source="runtime" kind="notice">注入</atk-auto>',
        "$agent-toolkit:process-wi",
        f"<skill>\n<name>agent-toolkit:process-wi</name>\n本文{closing}",
        "後続の訂正",
    ]
    records, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}} for text in texts],
    )

    assert [item["locators"] for item in records if item["kind"] == "candidate"] == [
        [{"record": "codex:transcript", "line": 5}]
    ]
    assert records[-1]["excluded"] == {"initial-skill-body": 1, "initial-skill-request": 1, "runtime-inserted": 2}


def test_hook_notice_evidence_includes_tool_use_input(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """PreToolUseのwarn候補の個別証拠が、同じ呼び出し識別子のコマンドと記録位置を持つ。

    hook実行記録だけでは警告の対象を特定できず、分析主体が元記録を検索し直すことになる。
    """
    candidates, items = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [
            {"type": "user", "message": {"role": "user", "content": "初期要求"}},
            {
                "type": "assistant",
                "timestamp": "2026-09-25T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "id": "toolu_target",
                            "input": {"command": "find . -name '*.md'", "description": "Markdownを探す"},
                        }
                    ],
                },
            },
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "toolu_target",
                    "content": ["[auto-generated: agent-toolkit/pretooluse][warn] warn: 検索対象を限定する"],
                }
            ),
        ],
    )

    hook_candidates = [item for item in candidates if item.get("candidate_kind") == "hook-notice"]
    assert len(hook_candidates) == 1
    tool_uses = [event for event in items[hook_candidates[0]["candidate_id"]]["events"] if event["kind"] == "tool-use"]
    assert tool_uses == [
        {
            "record": "claude:transcript",
            "kind": "tool-use",
            "line": 2,
            "timestamp": "2026-09-25T00:00:01Z",
            "name": "Bash",
            "input": {"command": "find . -name '*.md'", "description": "Markdownを探す"},
        }
    ]


def test_context_hook_output_is_excluded(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """blockまたはwarn以外の区分を持つhook出力は候補へ残らず、区分の種類によらず除外件数へ計上される。"""
    candidates, _ = _bundle_candidates_and_evidence(
        tmp_path,
        capsys,
        [
            {"type": "user", "message": {"role": "user", "content": "初期要求"}},
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "SessionStart",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="rules-main">\n# 規範\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "SubagentStart",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit" kind="auto-resume">\n再開\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [
                        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="block">\nblock: 停止\n'
                        "</agent-toolkit-auto-inserted>"
                    ],
                }
            ),
        ],
    )

    hook_candidates = [item for item in candidates if item.get("candidate_kind") == "hook-notice"]
    assert [item["locators"] for item in hook_candidates] == [[{"record": "claude:transcript", "line": 4}]]
    assert candidates[-1]["excluded"]["hook-notice-context"] == 2
