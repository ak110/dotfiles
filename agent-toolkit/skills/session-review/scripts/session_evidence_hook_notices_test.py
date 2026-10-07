"""証拠抽出の`--hook-notices`照会（`session_evidence_hook_notices.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    assistant_usage_entry,
    codex_tool_result_entry,
    events_by_kind,
    hook_attachment,
    read_jsonl,
    timestamped_entry,
    usage_record,
    write_jsonl,
    write_subagent,
)


def test_observation_boundary_excludes_later_main_records_in_all_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """観測境界より後のメイン記録を全照会モードから除外する。"""
    before_notice = "[auto-generated: test/before][warn] warning: before needle"
    after_notice = "[auto-generated: test/after][warn] warning: after needle"
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry(None, "時刻なしの管理相当レコード"),
            timestamped_entry("2026-09-01T00:00:00Z", "before needle"),
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "Before",
                    "toolUseID": "before",
                    "content": before_notice,
                }
            )
            | {"timestamp": "2026-09-01T00:00:01Z"},
            timestamped_entry("2026-09-01T00:00:02Z", "boundary needle"),
            timestamped_entry("2026-09-01T00:00:03Z", "after needle"),
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "After",
                    "toolUseID": "after",
                    "content": after_notice,
                }
            )
            | {"timestamp": "2026-09-01T00:00:04Z"},
        ],
    )
    base = [str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]

    assert evidence.main(base) == 0
    default_events = read_jsonl(capsys)
    assert "時刻なしの管理相当レコード" in json.dumps(default_events, ensure_ascii=False)
    assert "boundary needle" in json.dumps(default_events, ensure_ascii=False)
    assert "after needle" not in json.dumps(default_events, ensure_ascii=False)

    assert evidence.main([*base, "--warn"]) == 0
    assert [event["text"] for event in read_jsonl(capsys)] == [before_notice]

    assert evidence.main([*base, "--grep", "needle"]) == 0
    grep_events = read_jsonl(capsys)
    assert [event["line"] for event in grep_events if event["kind"] == "match"] == [2, 3, 4]
    assert grep_events[-1] == {"kind": "summary", "count": 3}

    assert evidence.main([*base, "--detail", "5"]) == 2
    assert read_jsonl(capsys) == [{"kind": "error", "text": "行番号5は範囲外"}]

    assert evidence.main([*base, "--stats"]) == 0
    summary = events_by_kind(read_jsonl(capsys), "stats-summary")[0]
    assert summary["end"] == "2026-09-01T00:00:02Z"

    assert evidence.main([*base, "--hook-notices"]) == 0
    hook_events = read_jsonl(capsys)
    assert [event["hook"] for event in hook_events if event["kind"] == "hook-notice"] == ["test/before"]


@pytest.mark.parametrize("element", ["agent-toolkit-hook-message", "agent-toolkit-auto-inserted"])
@pytest.mark.parametrize(
    "attributes",
    ['source="agent-toolkit/pretooluse" kind="warn"', 'kind="warn" source="agent-toolkit/pretooluse"'],
)
def test_hook_notices_mode_parses_xml_boundary_without_closing_tag(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    element: str,
    attributes: str,
) -> None:
    """XML境界の属性と本文を分類し、閉じタグを種別本文から除く。"""
    notice = f"<{element} {attributes}>\n入力を補正した\n</{element}>"
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-xml-notice",
                    "content": [notice],
                }
            )
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "hook-notice",
            "hook": "pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "warn",
            "kind_text": "入力を補正した",
            "count": 1,
        },
        {"kind": "summary", "count": 1},
    ]


def test_new_and_old_hook_boundaries_share_one_source(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    notices = [
        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">\n同じ警告\n</agent-toolkit-auto-inserted>',
        '<atk-auto source="pretooluse" kind="warn">\n同じ警告\n</atk-auto>',
    ]
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": f"call-{index}",
                    "content": [notice],
                }
            )
            for index, notice in enumerate(notices)
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    assert read_jsonl(capsys) == [
        {
            "kind": "hook-notice",
            "hook": "pretooluse",
            "hook_name": "PreToolUse:Bash",
            "tag": "warn",
            "kind_text": "同じ警告",
            "count": 2,
        },
        {"kind": "summary", "count": 2},
    ]
    assert evidence.main([str(transcript), "--warn"]) == 0
    assert len(read_jsonl(capsys)) == 2


def test_hook_notices_mode_is_exclusive_with_other_query_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """構造化集計モードも他の照会モードとの併用を引数誤用として拒否する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--hook-notices", "--warn"]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "--hook-notices" in events[0]["text"]


@pytest.mark.parametrize(
    ("query_args", "event_index", "expected_event"),
    [
        (
            ["--warn"],
            0,
            {
                "kind": "warning",
                "line": 4,
                "text": "warning: successful command warning",
                "tool": "call-1",
            },
        ),
        (
            ["--stats"],
            0,
            {
                "kind": "stats-total",
                "tokens": {},
                "subagent_count": 0,
                "agent_thread_count": 0,
                "agent_thread_counts": {},
            },
        ),
        (
            ["--hook-notices"],
            0,
            {
                "kind": "hook-notice",
                "hook": "posttooluse",
                "hook_name": "PostToolUse:Bash",
                "tag": "warn",
                "kind_text": "warn: 成功結果を確認する",
                "count": 1,
            },
        ),
        (
            ["--detail", "4"],
            0,
            {
                "kind": "detail",
                "line": 4,
                "timestamp": None,
                "tool": "call-1",
                "text": "warning: successful command warning",
            },
        ),
    ],
)
def test_query_event_is_a_stable_problem_locator(
    query_args: list[str],
    event_index: int,
    expected_event: dict,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """既存照会結果の同じ位置から統計、警告、hook通知および詳細を再取得する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "locatorへ含めないユーザー本文1"}},
            {"type": "user", "message": {"role": "user", "content": "locatorへ含めないユーザー本文2"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "call-1", "input": {"command": "true"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call-1", "content": "warning: successful command warning"}
                    ],
                },
            },
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "PostToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": "[auto-generated: agent-toolkit/posttooluse][warn] warn: 成功結果を確認する",
                }
            ),
        ],
    )

    locator = {"event_index": event_index}

    assert evidence.main([str(transcript), *query_args]) == 0
    first = read_jsonl(capsys)
    assert evidence.main([str(transcript), *query_args]) == 0
    second = read_jsonl(capsys)

    assert first == second
    assert second[locator["event_index"]] == expected_event
    assert set(locator) == {"event_index"}
    assert "successful command warning" not in json.dumps(locator)
    assert "locatorへ含めないユーザー本文" not in json.dumps(locator, ensure_ascii=False)


def test_hook_notices_mode_counts_notices_by_hook_origin_tag_and_kind(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """hook実行の記録4種の通知だけを、hook識別子・発動元・タグ・種別ごとに数える。

    同一ツール呼び出しの標準出力と追加コンテキストへ重複して格納された通知は1件へ集約し、
    追加コンテキストを伴わない標準エラー出力の通知と、標識を持たないシステムメッセージも計上する。
    hookの記録でない本文（会話中の引用）は同じ文字列でも母集団へ含めない。
    """
    truncation = "[auto-generated: agent-toolkit/pretooluse][warn] warn: 出力を切り詰めている"
    fixed_wait = "[auto-generated: agent-toolkit/pretooluse][block] block: 固定待機を検出した"
    stop_notice = "[auto-generated: dotfiles/claude_hook_stop] 応答の完了可否を明示すること"
    system_message = "[agent-toolkit] auto-inserted --decorate into git log."
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": truncation}}, ensure_ascii=False),
                    "stderr": "",
                }
            ),
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [truncation],
                }
            ),
            hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "stdout": "{}",
                    "stderr": fixed_wait,
                }
            ),
            hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-3",
                    "stdout": "{}",
                    "stderr": fixed_wait,
                }
            ),
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-4",
                    "content": system_message,
                }
            ),
            hook_attachment(
                {
                    "type": "hook_blocking_error",
                    "hookName": "Stop",
                    "toolUseID": "call-5",
                    "blockingError": {"blockingError": stop_notice},
                }
            ),
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": truncation}]}},
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = read_jsonl(capsys)
    assert events[-1] == {"kind": "summary", "count": 5}
    assert events[0] == {
        "kind": "hook-notice",
        "hook": "pretooluse",
        "hook_name": "PreToolUse:Bash",
        "tag": "block",
        "kind_text": "block: 固定待機を検出した",
        "count": 2,
    }
    assert sorted(json.dumps(event, ensure_ascii=False, sort_keys=True) for event in events[1:-1]) == sorted(
        json.dumps(event, ensure_ascii=False, sort_keys=True)
        for event in [
            {
                "kind": "hook-notice",
                "hook": "pretooluse",
                "hook_name": "PreToolUse:Bash",
                "tag": "warn",
                "kind_text": "warn: 出力を切り詰めている",
                "count": 1,
            },
            {
                "kind": "hook-notice",
                "hook": "dotfiles/claude_hook_stop",
                "hook_name": "Stop",
                "tag": None,
                "kind_text": "応答の完了可否を明示すること",
                "count": 1,
            },
            {
                "kind": "hook-notice",
                "hook": None,
                "hook_name": "PreToolUse:Bash",
                "tag": None,
                "kind_text": system_message,
                "count": 1,
            },
        ]
    )


def test_hook_notices_mode_separates_kinds_by_leading_body_and_skips_empty_bodies(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """先頭一定長が異なる通知を別種別として数え、本文が空の記録は数えない。"""
    prefix = "[auto-generated: agent-toolkit/pretooluse][warn] "
    head = "a" * 79  # 種別キーの長さ80文字の直前まで同一とし、80文字目だけを違えて別種別とする
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [f"{prefix}{head}x 対象A", f"{prefix}{head}y 対象B"],
                }
            ),
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": [f"{prefix}{head}x 対象C"],
                }
            ),
            hook_attachment(
                {"type": "hook_success", "hookName": "Stop", "toolUseID": "call-3", "stdout": "{}", "stderr": "   "}
            ),
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = read_jsonl(capsys)
    assert [(event["kind_text"], event["count"]) for event in events[:-1]] == [
        (f"{head}x", 2),
        (f"{head}y", 1),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


def test_hook_notices_mode_counts_each_marker_of_a_multi_marker_body(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """1つの本文が複数の標識を持つ場合、標識ごとに別の発生源として数える。"""
    body = (
        "[auto-generated: agent-toolkit/pretooluse][warn] 入力を補正した "
        "[auto-generated: agent-toolkit/pretooluse][block] 固定待機を検出した"
    )
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [body],
                }
            )
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = read_jsonl(capsys)
    assert sorted(event["tag"] for event in events[:-1]) == ["block", "warn"]
    assert {event["kind_text"] for event in events[:-1]} == {"入力を補正した", "固定待機を検出した"}
    assert events[-1] == {"kind": "summary", "count": 2}


def test_hook_notices_mode_ignores_nested_delivery_and_deduplicates_same_call(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    body = (
        '<agent-toolkit-auto-inserted kind="notice" source="hook/a">参考</agent-toolkit-auto-inserted>'
        '<agent-toolkit-auto-inserted kind="warn" source="hook/b">理由B'
        '<agent-toolkit-auto-inserted kind="rules-main" source="agent-toolkit">規範</agent-toolkit-auto-inserted>'
        "</agent-toolkit-auto-inserted>"
        '<agent-toolkit-auto-inserted kind="block" source="hook/c">理由C</agent-toolkit-auto-inserted>'
    )
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {"type": "hook_additional_context", "hookName": "PreToolUse:Bash", "toolUseID": "call-1", "content": [body]}
            ),
            hook_attachment(
                {"type": "hook_additional_context", "hookName": "PreToolUse:Bash", "toolUseID": "call-1", "content": [body]}
            ),
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = read_jsonl(capsys)
    assert [(event["hook"], event["tag"], event["count"]) for event in events[:-1]] == [
        ("hook/a", "notice", 1),
        ("hook/b", "warn", 1),
        ("hook/c", "block", 1),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


def test_hook_notices_mode_merges_kinds_differing_only_by_variable_parts(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """パスや識別子だけが異なる同種の通知を1つの種別へ集約する。"""
    prefix = "[auto-generated: agent-toolkit/posttooluse][notice] "
    bodies = [
        f"{prefix}plan file /home/aki/.claude/plans/alpha-1.md was written.",
        f"{prefix}plan file /home/aki/.claude/plans/beta-2.md was written.",
        f"{prefix}plan file ~/.claude/plans/gamma-3.md was written.",
    ]
    transcript = _write_transcript(
        tmp_path,
        [
            hook_attachment(
                {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Write",
                    "toolUseID": f"call-{index}",
                    "content": [body],
                }
            )
            for index, body in enumerate(bodies)
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0

    events = read_jsonl(capsys)
    assert [(event["kind_text"], event["count"]) for event in events[:-1]] == [
        ("plan file <var> was written.", 3),
    ]
    assert events[-1] == {"kind": "summary", "count": 3}


def test_all_modes_recursively_scan_cross_engine_delegations(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """全照会が3段の実行系横断委譲と委譲先サブエージェントを1回で走査する。"""
    codex_a = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    claude_b = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    codex_c = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    missing = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    unrelated = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    home = tmp_path / "home"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "main needle"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "main-a",
                            "input": {"engine": "codex", "threadId": codex_a},
                        }
                    ],
                },
            },
            codex_tool_result_entry("2026-08-30T00:00:00Z", "main-a", codex_a),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-root",
        [
            {"type": "user", "message": {"role": "user", "content": "root record"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "root-warning", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "root-warning", "content": "[warn] root warning"}],
                },
            },
        ],
    )

    rollout_dir = codex_home / "sessions" / "2026" / "08" / "30"
    write_jsonl(
        rollout_dir / f"rollout-test-{codex_a}.jsonl",
        [
            {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "codex-a needle"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "exec-agents-server",
                    "arguments": {"engine": "claude"},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "exec-agents-server",
                    "output": [
                        {"type": "input_text", "text": "Script completed"},
                        {"type": "input_text", "text": json.dumps({"session_id": claude_b})},
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "exec-unrelated",
                    "input": "text('unrelated')",
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "exec-unrelated",
                    "output": [{"type": "input_text", "text": json.dumps({"engine": "claude", "session_id": unrelated})}],
                },
            },
        ],
    )
    claude_path = home / ".claude" / "projects" / "repo" / f"{claude_b}.jsonl"
    write_jsonl(
        claude_path,
        [
            {"type": "user", "message": {"role": "user", "content": "claude-b needle"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "mcp__agents_server__start",
                            "id": "b-c",
                            "input": {"engine": "codex", "threadId": codex_c},
                        }
                    ],
                },
            },
            codex_tool_result_entry("2026-08-30T00:00:00Z", "b-c", codex_c),
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "PostToolUse:Bash",
                    "toolUseID": "b-c",
                    "content": "[auto-generated: agent-toolkit/posttooluse][warn] nested notice",
                }
            ),
        ],
    )
    write_jsonl(
        home / ".claude" / "projects" / "repo" / f"{unrelated}.jsonl",
        [{"type": "user", "message": {"role": "user", "content": "unrelated needle"}}],
    )
    write_subagent(
        claude_path.with_suffix("") / "subagents",
        "agent-child",
        [
            assistant_usage_entry("2026-08-30T00:00:00Z", "child", usage_record(2, 3)),
            {"type": "user", "message": {"role": "user", "content": "child record"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "Bash", "id": "child-warning", "input": {}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "child-warning", "content": "[warning] child warning"}],
                },
            },
        ],
    )
    write_jsonl(
        rollout_dir / f"rollout-test-{codex_c}.jsonl",
        [
            {"type": "response_item", "payload": {"type": "message", "role": "user", "content": "codex-c needle"}},
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "missing",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "missing",
                    "output": {"engine": "codex", "session_id": missing},
                },
            },
        ],
    )

    assert evidence.main([str(transcript)]) == 0
    default_events = read_jsonl(capsys, raw=True)
    assert {event["record"] for event in default_events if event["kind"] != "unresolved-record"} == {
        "claude:transcript",
        "claude:transcript/agent-root",
        f"codex:{codex_a}",
        f"claude:{claude_b}",
        f"claude:{claude_b}/agent-child",
        f"codex:{codex_c}",
    }
    assert default_events[-1] == {"kind": "unresolved-record", "record": missing, "line": 3}
    assert unrelated not in {event["record"] for event in default_events}

    assert evidence.main([str(transcript), "--warn"]) == 0
    warning_events = read_jsonl(capsys, raw=True)
    assert [event["record"] for event in warning_events if event["kind"] == "warning"] == [
        "claude:transcript/agent-root",
        f"claude:{claude_b}",
        f"claude:{claude_b}/agent-child",
    ]

    assert evidence.main([str(transcript), "--grep", "needle"]) == 0
    grep_events = read_jsonl(capsys, raw=True)
    assert {event["record"] for event in grep_events if event["kind"] == "match"} == {
        "claude:transcript",
        f"codex:{codex_a}",
        f"claude:{claude_b}",
        f"codex:{codex_c}",
    }
    assert [event["record"] for event in grep_events if event["kind"] == "summary" and "record" in event] == [
        "claude:transcript",
        f"codex:{codex_a}",
        f"claude:{claude_b}",
        f"codex:{codex_c}",
    ]
    assert grep_events[-2] == {"kind": "summary", "count": 4}

    assert evidence.main([str(transcript), "--detail", f"claude:{claude_b}:1"]) == 0
    assert read_jsonl(capsys, raw=True)[0]["record"] == f"claude:{claude_b}"
    assert evidence.main([str(transcript), "--detail", "unknown:1"]) == 2
    assert [{key: value for key, value in event.items() if key != "next_action"} for event in read_jsonl(capsys, raw=True)] == [
        {"kind": "error", "text": "記録が不明: unknown"}
    ]

    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    hook_events = read_jsonl(capsys, raw=True)
    assert next(event for event in hook_events if event["kind"] == "summary")["count"] == 1

    assert evidence.main([str(transcript), "--stats"]) == 0
    stats_events = read_jsonl(capsys, raw=True)
    assert {event["agent"] for event in stats_events if event["kind"] == "stats-subagent"} == {
        "claude:transcript/agent-root",
        f"claude:{claude_b}/agent-child",
    }
    assert {event["session_id"] for event in stats_events if event["kind"] == "stats-agent-thread"} == {
        codex_a,
        claude_b,
        codex_c,
    }


def test_hook_record_scan_tolerates_non_string_type_values(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`type`の値がdictの記録を含むtranscriptでも警告走査とhook通知集計が完遂する。

    ツール定義を含む記録では`schema.input_schema.properties`配下に`type`という名前の
    プロパティ定義が現れ、その値がJSON Schemaのdictになる。
    """
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] warn: 出力を切り詰めている"
    tool_schema_entry = {
        "type": "attachment",
        "attachment": {
            "tools": [
                {
                    "name": "feedback",
                    "schema": {
                        "input_schema": {
                            "properties": {
                                "type": {"type": "string", "enum": ["bug", "idea"]},
                                "title": {"type": "string"},
                            }
                        }
                    },
                }
            ]
        },
    }
    transcript = _write_transcript(
        tmp_path,
        [
            tool_schema_entry,
            hook_attachment(
                {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notice}}, ensure_ascii=False),
                    "stderr": "",
                }
            ),
        ],
    )

    assert evidence.main([str(transcript), "--hook-notices"]) == 0
    hook_events = read_jsonl(capsys)
    assert hook_events[-1] == {"kind": "summary", "count": 1}

    assert evidence.main([str(transcript), "--warn"]) == 0
    warn_events = read_jsonl(capsys)
    assert [event["text"] for event in warn_events if event["kind"] == "warning"] == [notice]
