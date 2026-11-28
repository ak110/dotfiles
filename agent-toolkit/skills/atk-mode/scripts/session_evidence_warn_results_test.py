from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence
from agent_toolkit._atk.wi.add_reports import print_draft_openings
from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import execution_tool_use, read_jsonl
from session_evidence_warn import warning_count_main


def _execution_result_transcript(tmp_path: pathlib.Path, *contents: str) -> pathlib.Path:
    return _write_transcript(
        tmp_path,
        [
            execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": content} for content in contents],
                },
            },
        ],
    )


def test_query_modes_search_hook_notice_stored_under_attachment(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 検証コマンドの出力を切り詰めている"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "uuid": "hook-success",
                "attachment": {
                    "type": "hook_success",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notice}}, ensure_ascii=False),
                    "stderr": "",
                    "exitCode": 0,
                },
            },
            {
                "type": "attachment",
                "uuid": "hook-context",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0
    warnings = read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1]
    assert notice in warnings[0]["text"]

    assert evidence.main([str(transcript), "--grep", "切り詰めている"]) == 0
    matches = read_jsonl(capsys)
    assert [event["line"] for event in matches[:2]] == [1, 2]
    assert matches[-1] == {"kind": "summary", "count": 2}


def test_warn_mode_keeps_hook_notices_with_distinct_or_missing_tool_use_ids(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    notice = "[auto-generated: agent-toolkit/pretooluse][warn] 検証コマンドの出力を切り詰めている"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-1",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "toolUseID": "call-2",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [notice],
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PreToolUse:Bash",
                    "content": [notice],
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    warnings = read_jsonl(capsys)
    assert [event["line"] for event in warnings] == [1, 2, 3, 4]
    assert [event["text"] for event in warnings] == [notice] * 4


def test_warn_mode_reports_matches_found_only_in_tool_use_result(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 退避された警告", "stderr": "警告: 標準エラー"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "<persisted-output>"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    events = read_jsonl(capsys)
    assert [event["text"] for event in events] == ["warning: 退避された警告", "警告: 標準エラー"]
    assert [event["line"] for event in events] == [1, 1]


def test_warn_mode_ignores_read_result_body_and_keeps_hook_notice(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    file_body = 'warning: 文書内の例示\n{"warning_message": "文書内の構造化警告"}'
    notice = "[auto-generated: agent-toolkit/posttooluse][warn] 読み取り後の通知"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {
                    "type": "text",
                    "file": {"filePath": "/tmp/rules.md", "content": file_body, "numLines": 2},
                },
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "read-1", "content": file_body}],
                },
                "attachment": {
                    "type": "hook_additional_context",
                    "hookName": "PostToolUse:Read",
                    "toolUseID": "read-1",
                    "content": [notice],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == [notice]


def test_warn_mode_does_not_duplicate_tool_use_result_already_visible(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "warning: 同じ警告\n"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "warning: 同じ警告\n"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--warn"]) == 0

    assert [event["text"] for event in read_jsonl(capsys)] == ["warning: 同じ警告"]


@pytest.mark.parametrize(
    ("option", "pattern"),
    [("--warn", None), ("--grep", "warning")],
)
def test_query_modes_normalize_line_number_prefix_across_body_fields(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    pattern: str | None,
) -> None:
    transcript = _execution_result_transcript(tmp_path, "12\twarning: 同じ本文", "warning: 同じ本文")

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = read_jsonl(capsys)
    expected_text = "warning: 同じ本文" if option == "--warn" else "12\twarning: 同じ本文"
    expected_event: dict[str, object] = {
        "kind": "warning" if option == "--warn" else "match",
        "line": 2,
        "text": expected_text,
    }
    if option == "--warn":
        expected_event["tool"] = "call-1"
    else:
        expected_event["timestamp"] = None
    assert events[0] == expected_event
    if option == "--grep":
        assert events[-1] == {"kind": "summary", "count": 1}
        assert len(events) == 2
    else:
        assert len(events) == 1


@pytest.mark.parametrize(
    ("option", "pattern"),
    [("--warn", None), ("--grep", "warning")],
)
def test_query_modes_keep_numbered_and_unnumbered_lines_in_one_body_distinct(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    option: str,
    pattern: str | None,
) -> None:
    transcript = _execution_result_transcript(tmp_path, "12\twarning: 本文\nwarning: 本文")

    arguments = [str(transcript), option]
    if pattern is not None:
        arguments.append(pattern)
    assert evidence.main(arguments) == 0

    events = read_jsonl(capsys)
    expected = ["warning: 本文"] if option == "--warn" else ["12\twarning: 本文", "warning: 本文"]
    assert [event["text"] for event in events if event["kind"] in {"warning", "match"}] == expected
    if option == "--grep":
        assert events[-1] == {"kind": "summary", "count": 1}


def test_grep_mode_searches_tool_use_result_output(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "user",
                "toolUseResult": {"stdout": "退避された本文の照合語"},
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "<persisted-output>"}],
                },
            }
        ],
    )

    assert evidence.main([str(transcript), "--grep", "照合語"]) == 0

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["match", "summary"]
    assert events[0] == {"kind": "match", "line": 1, "timestamp": None, "text": "退避された本文の照合語"}
    assert events[-1]["count"] == 1


def test_replaced_persisted_output_is_read_from_saved_file(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """PostToolUseが退避した出力の`stdout`を通知へ置き換えた記録でも、保存先の内容を検索・詳細・警告の実体とする。

    置き換え後の記録は元の出力を`persistedOutputPath`のファイルだけに持つ。記録の`stdout`だけを読むと、
    保存先の末尾にだけある文字列が検索で見つからず、振り返りが出力の不在を誤って結論づける。
    記録の形は、Claude Code 2.1.291でPostToolUseの`updatedToolOutput`が`stdout`を置き換えたときのtranscriptの
    `toolUseResult`（置き換えた`stdout`と元の`persistedOutputPath`・`persistedOutputSize`）から写した。
    """
    saved = tmp_path / "tool-results" / "b1.txt"
    saved.parent.mkdir()
    saved.write_text("先頭の行\n" * 3000 + "warning: tail-only-marker\n", encoding="utf-8")
    notice = f"このコマンドの出力はホストの上限を超えたため保存先へ退避された。保存先: {saved}"
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "cat large.md"}}],
                },
            },
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "c1",
                            "content": f"<persisted-output>\nOutput too large (48.8KB). Full output saved to: {saved}\n\n"
                            f"Preview (first 2KB):\n{notice}\n</persisted-output>",
                        }
                    ],
                },
                "toolUseResult": {
                    "stdout": notice,
                    "stderr": "",
                    "persistedOutputPath": str(saved),
                    "persistedOutputSize": saved.stat().st_size,
                },
            },
        ],
    )

    assert evidence.main([str(transcript), "--fixed-string", "tail-only-marker"]) == 0
    assert read_jsonl(capsys)[0]["count"] == 1
    assert evidence.main([str(transcript), "--detail", "2"]) == 0

    assert read_jsonl(capsys)[0]["text"].startswith("先頭の行")
    assert evidence.main([str(transcript), "--warn"]) == 0
    assert "tail-only-marker" in capsys.readouterr().out


def test_query_modes_with_distinct_results_can_be_combined(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])

    assert evidence.main([str(transcript), "--warn", "--grep", "依頼"]) == 0

    events = read_jsonl(capsys)
    assert {event["kind"] for event in events} >= {"warning", "match", "summary"}


def test_bundle_preserves_each_hook_warning_and_its_full_body(tmp_path, capsys):
    tail = "この対応指示を全文で取得する。"
    notices = (
        "警告: 3件\n"
        '<atk-auto source="first" kind="warn">短い警告</atk-auto>\n'
        '<atk-auto source="first" kind="notice">警告ではない</atk-auto>\n'
        f'<atk-auto source="first" kind="warn">{"長い内容" * 500}\n{tail}</atk-auto>\n'
        '<atk-auto source="second" kind="warn">短い警告</atk-auto>'
    )
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_success",
                    "toolUseID": "call-1",
                    "hookName": "PreToolUse:Bash",
                    "stdout": json.dumps({"hookSpecificOutput": {"additionalContext": notices}}),
                    "stderr": "",
                },
            },
            {
                "type": "attachment",
                "attachment": {
                    "type": "hook_additional_context",
                    "toolUseID": "call-1",
                    "hookName": "PreToolUse:Bash",
                    "content": [notices],
                },
            },
        ],
    )
    output = tmp_path / "bundle"
    output.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(output)]) == 0
    capsys.readouterr()
    warnings = [
        event
        for line in (output / "warnings.jsonl").read_text(encoding="utf-8").splitlines()
        if (event := json.loads(line))["kind"] == "warning"
    ]
    assert [(event["source"], event["line"]) for event in warnings] == [("first", 1), ("first", 1), ("second", 1)]
    assert warnings[0]["full_text"] == warnings[2]["full_text"] == "短い警告"
    assert warnings[1]["full_text"].endswith("\n" + tail) and tail not in warnings[1]["text"]
    assert warnings[1]["record"] == warnings[0]["record"]


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize(
    ("draft_eol", "stdout_eol"),
    [("\n", "\n"), ("\r\n", "\n"), ("\n", "\r\n"), ("\r\n", "\r\n")],
    ids=["lf", "crlf-draft", "crlf-transport", "crlf-draft-and-transport"],
)
def test_warning_count_pairs_emitter_with_result_and_keeps_repeated_markers(
    tmp_path, capsys, monkeypatch, engine, draft_eol, stdout_eol
):
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    transcript = (
        claude / "projects" / "fixture" / "session.jsonl"
        if engine == "claude"
        else codex / "sessions" / "2026" / "11" / "27" / "rollout-fixture.jsonl"
    )
    transcript.parent.mkdir(parents=True)
    timestamp = "2026-11-27T12:00:00+00:00"
    records = []
    notice = '<atk-auto source="fixture" kind="warn">対象を確認する</atk-auto>'
    after_preview = notice.replace("対象を確認する", "冒頭表示後の警告")
    block = notice.replace('kind="warn"', 'kind="block"')
    quoted = notice.replace("対象を確認", "原稿を引用")
    reprint = "失敗: remote同期に失敗した。確定済みの本文を以下に再表示する"
    print_draft_openings(
        [f"# 原稿\n\n冒頭本文終了\n{reprint}\n{quoted}\n".replace("\n", draft_eol)],
        ["draft.md"],
        output_paths=[str(tmp_path / "opening.md")],
    )
    draft = capsys.readouterr().out
    host_block = (
        "PreToolUse:Bash hook error: " if engine == "claude" else "Script error:\nCommand blocked by PreToolUse hook: "
    ) + block
    for index, (command, output, refused) in enumerate(
        [
            ("atk wi add --dry-run", notice, False),
            ("atk wi add --dry-run", draft, False),
            ("atk wi edit example.md", f"{notice}\n{draft}{after_preview}\n情報: 先送りの候補 8行目: {quoted}", False),
            ("atk wi add", f"{notice}\n{reprint}\n次の操作: 保存して再投入する\n---\n{quoted}", False),
            ("git -C repo commit --allow-empty -m check", notice, False),
            ("git --no-pager commit -m check", notice, False),
            ("set -o pipefail; atk wi add --dry-run", f"{notice}\n{draft}", False),
            ("cd repo && atk run-script commit-with-timeout -- --timeout 30 -- -m check", notice, False),
            ("cat captured.txt && atk wi add --dry-run", quoted, False),
            ("set -o pipefail && git commit -m check", host_block, True),
            ("cat captured.txt", notice, False),
            ("atk run-script session-review-evidence -- --warn", notice, False),
            ("atk run-script warning-count -- --source fixture", notice, False),
            ("git status", notice, False),
        ]
    ):
        call_id = f"call-{index}"
        output = output.replace("\n", stdout_eol)
        if engine == "claude":
            records.extend(
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
                                {"type": "tool_result", "tool_use_id": call_id, "content": output, "is_error": refused},
                            ],
                        },
                        "toolUseResult": {"stdout": output},
                    },
                ]
            )
        elif command.startswith("git -C "):
            records.append(
                {
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "CommandExecution",
                            "id": call_id,
                            "command": command,
                            "aggregated_output": output,
                            "exit_code": 0,
                        },
                    },
                }
            )
        else:
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call",
                        "name": "exec_command",
                        "call_id": call_id,
                        "arguments": json.dumps({"cmd": command}),
                    },
                }
            )
            records.append(
                {
                    "type": "response_item",
                    "payload": {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": output,
                    },
                }
            )
        records.append(records[-1].copy())
    records.append({"type": "assistant", "message": {"role": "assistant", "content": notice}})
    if engine == "claude":
        hook = {
            "type": "attachment",
            "attachment": {"type": "hook_additional_context", "toolUseID": "hook-1", "content": [notice, notice]},
        }
    else:
        hook = {
            "type": "response_item",
            "payload": {
                "type": "message",
                "role": "developer",
                "id": "hook-1",
                "content": [{"type": "input_text", "text": notice}],
                "internal_chat_message_metadata_passthrough": {
                    "content_item_kinds": ["hooks.additional_context"],
                    "content_item_metadata": [{"provenance": {"type": "harness", "hook": True}}],
                },
            },
        }
    records += [hook, hook, {**hook, "timestamp": "2026-11-28T00:00:00Z"}]
    transcript.write_text(
        "".join(json.dumps({"timestamp": timestamp, **record}, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    assert (
        warning_count_main(
            [
                "--source",
                "fixture",
                "--source",
                "absent",
                "--since",
                "2026-11-27T00:00:00Z",
                "--until",
                "2026-11-28T00:00:00Z",
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["sources"] == {
        "absent": {"occurrences": 0, "conversations": 0},
        "fixture": {"occurrences": 9 + (engine == "claude") + 1, "conversations": 1},
    }
    assert sum(row["kind"] == "block" for row in result["occurrences"]) == 1
    texts = [row["text"] for row in result["occurrences"]]
    assert texts.count("冒頭表示後の警告") == 1
    assert texts.count("対象を確認する") == len(texts) - 1
    assert all(row["record"].startswith(str(transcript)) for row in result["occurrences"])
    assert result["partial_records"] == result["undated_occurrences"] == 0
