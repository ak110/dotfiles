"""セッション振り返りの試験（session_evidence_bundle_conversation_test）。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    claude_call,
    claude_result,
    codex_item,
    codex_tool_result_entry,
    codex_tool_use_entry,
    events_by_kind,
    hook_attachment,
    read_jsonl,
    timestamped_entry,
    write_jsonl,
    write_subagent,
)


def test_common_runtime_inserted_classifier(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """画面と共有する判定で新しい挿入本文を会話から除き、ユーザーの入力は残す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "最初の依頼"}},
            {"type": "user", "message": {"role": "user", "content": "<multi_agent_mode>自動配送"}},
            {"type": "user", "message": {"role": "user", "content": "<permissions instructions>権限設定"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": (
                        "You are `/root`, the primary agent in a team of agents collaborating to fulfill the user's goals."
                    ),
                },
            },
            {"type": "user", "message": {"role": "user", "content": "$agent-toolkit:process-wi"}},
            {"type": "user", "message": {"role": "user", "content": "後続のユーザー発話"}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()
    conversation = [json.loads(line) for line in (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [item["text"] for item in conversation] == ["最初の依頼", "$agent-toolkit:process-wi", "後続のユーザー発話"]


def test_bundle_writes_conversation_of_main_utterances_with_full_text_detail(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """会話の流れはメイン記録の発話を全文で、ツール呼び出しを代表入力で、失敗したツール結果を診断の1行で時系列に載せる。

    振り返りは会話の流れからセッション全体の試行と遠回りを探すため、ツール呼び出しと失敗が流れに欠けると
    候補の観点に当たらない問題が分析の入力から欠ける。成功した結果の本文、書き込む本文、配送本文、実行環境の挿入、
    スキル展開を含めると量が膨らみ、ユーザーの発話と区別できなくなる。
    長い発話は会話の流れの表示で先頭と末尾だけになるため、記録位置の照会が全文を返す必要がある。
    """
    long_reply = "長い応答の先頭。" + "あ" * 1500 + "長い応答の末尾。"
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "振り返りを速くしたい"}},
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": '<agent-toolkit-auto-inserted kind="notice">配送</agent-toolkit-auto-inserted>',
                },
            },
            {"type": "user", "isMeta": True, "message": {"role": "user", "content": "実行環境の注記"}},
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": "Base directory for this skill: /x"}]},
            },
            {"type": "user", "message": {"role": "user", "content": "<system-reminder>注入</system-reminder>"}},
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "調べます。"},
                        {"type": "tool_use", "id": "call-1", "name": "Bash", "input": {"command": "ls"}},
                    ],
                },
            },
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "a.txt"}]},
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-2",
                            "name": "Write",
                            "input": {"file_path": "/x/a.md", "content": "書き込む本文"},
                        }
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
                            "tool_use_id": "call-2",
                            "is_error": True,
                            "content": "Exit code 1\n書込失敗の診断",
                        }
                    ],
                },
            },
            {"type": "user", "message": {"role": "user", "content": "全文抽出すべきでは？"}},
            {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": long_reply}]}},
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    capsys.readouterr()

    conversation = [json.loads(line) for line in (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(item["kind"], item.get("role") or item.get("tool"), item["line"], item["text"]) for item in conversation] == [
        ("utterance", "user", 1, "振り返りを速くしたい"),
        ("utterance", "assistant", 6, "調べます。"),
        ("tool-call", "Bash", 6, '{"command": "ls"}'),
        ("tool-result", "Bash", 7, "a.txt"),
        ("tool-call", "Write", 8, '{"file_path": "/x/a.md", "content": "書き込む本文"}'),
        ("tool-failure", None, 9, "Exit code 1\n書込失敗の診断"),
        ("tool-result", "Write", 9, "Exit code 1\n書込失敗の診断"),
        ("utterance", "user", 10, "全文抽出すべきでは？"),
        ("utterance", "assistant", 11, long_reply),
    ]
    assert conversation[5]["call_id"] == "call-2"
    serialized = (bundle_dir / "conversation.jsonl").read_text(encoding="utf-8")
    assert "a.txt" in serialized and "書き込む本文" in serialized

    assert evidence.main([str(transcript), "--detail", "main:11"]) == 0
    assert read_jsonl(capsys) == [{"kind": "detail", "line": 11, "timestamp": None, "role": "assistant", "text": long_reply}]


@pytest.mark.parametrize("existing", [False, True])
def test_bundle_rejects_output_that_is_not_an_existing_directory(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    existing: bool,
) -> None:
    """出力先が実在しない場合とディレクトリでない場合は、ファイルを生成せず終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    destination = tmp_path / ("regular.txt" if existing else "missing")
    if existing:
        destination.write_text("", encoding="utf-8")

    assert evidence.main([str(transcript), "--bundle", str(destination)]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert str(destination) in events[0]["text"]
    assert list(tmp_path.rglob("*.jsonl")) == [transcript]


@pytest.mark.parametrize(
    "conflicting",
    [
        ["--warn"],
        ["--grep", "依頼"],
        ["--detail", "1"],
        ["--fixed-string", "依頼"],
        ["--record-schema", "1"],
        ["--stats"],
        ["--hook-notices"],
    ],
)
def test_bundle_is_exclusive_with_other_query_modes(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    conflicting: list[str],
) -> None:
    """集約実行と他の照会モードの併用は引数誤用として終了コード2で返す。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir), *conflicting]) == 2

    events = read_jsonl(capsys)
    assert [event["kind"] for event in events] == ["error"]
    assert "--bundle" in events[0]["text"]
    assert not list(bundle_dir.iterdir())


_ADHOC_THREAD = "77777777-7777-4777-8777-777777777777"


_CODEX_EXEC_INPUT = (
    'const r=await tools.exec_command({cmd:"python3 -c \'print(open(\\"draft.md\\").read().count(\\"x\\"))\'"});'
)


def test_bundle_collects_adhoc_processing_per_record_including_delegates(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """委譲先を含む記録ごとに、その場のコードによる加工を1件の候補へまとめ、読むだけの呼び出しを含めない。

    Codex形式の委譲先記録は、Codex CLIのrollout（2026年10月4日の記録）の`exec`ツールがJavaScript本文から
    `exec_command`を呼ぶ形を写した。
    """
    root = tmp_path / "project"
    transcript = root / "parent-session.jsonl"
    saved_output = "/home/u/.cache/agent-toolkit/managed-temp/atk-output-abc/output.txt"
    write_jsonl(
        transcript,
        [
            {"type": "user", "timestamp": "2026-10-06T00:00:00Z", "message": {"role": "user", "content": "依頼"}},
            claude_call("2026-10-06T00:00:01Z", "jq-1", "Bash", {"command": f"jq -r '.line' {saved_output}"}),
            claude_result("2026-10-06T00:00:02Z", "jq-1", "12"),
            claude_call("2026-10-06T00:00:03Z", "cat-1", "Bash", {"command": "cat agent-toolkit/README.md"}),
            claude_result("2026-10-06T00:00:04Z", "cat-1", "本文"),
            claude_call("2026-10-06T00:00:05Z", "rg-1", "Bash", {"command": "rg -n needle . | cut -c1-200"}),
            claude_result("2026-10-06T00:00:06Z", "rg-1", "a.py:1:needle"),
            claude_call("2026-10-06T00:00:07Z", "sed-1", "Bash", {"command": "sed -n 1,20p agent-toolkit/x.py"}),
            claude_result("2026-10-06T00:00:08Z", "sed-1", "本文"),
            codex_tool_use_entry("2026-10-06T00:00:09Z", "call-codex", _ADHOC_THREAD),
            codex_tool_result_entry("2026-10-06T00:00:10Z", "call-codex", _ADHOC_THREAD),
        ],
    )
    heredoc = "python3 - <<'EOF'\nimport json\nprint(json.dumps({'start': 1, 'end': 48}))\nEOF"
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            claude_call("2026-10-06T00:00:02Z", "heredoc-1", "Bash", {"command": heredoc}),
            claude_result("2026-10-06T00:00:03Z", "heredoc-1", '{"start": 1, "end": 48}'),
            claude_call("2026-10-06T00:00:04Z", "inline-1", "Bash", {"command": "uv run --frozen python -c 'print(1)'"}),
        ],
    )
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    write_jsonl(
        codex_home / "sessions" / "2026" / "10" / "06" / f"rollout-2026-10-06T00-00-09-{_ADHOC_THREAD}.jsonl",
        [
            {"timestamp": "2026-10-06T00:00:09Z", "type": "session_meta", "payload": {"id": _ADHOC_THREAD}},
            codex_item(
                "2026-10-06T00:00:11Z",
                {
                    "type": "custom_tool_call",
                    "name": "exec",
                    "call_id": "c-exec",
                    "input": _CODEX_EXEC_INPUT,
                },
            ),
            codex_item("2026-10-06T00:00:12Z", {"type": "custom_tool_call_output", "call_id": "c-exec", "output": "1"}),
            codex_item(
                "2026-10-06T00:00:13Z",
                {"type": "function_call", "name": "exec_command", "call_id": "c-rg", "arguments": '{"cmd": "rg needle"}'},
            ),
        ],
    )
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()

    assert evidence.main([str(transcript), "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys, raw=True)

    candidates = [
        json.loads(line)
        for line in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
        if json.loads(line).get("candidate_kind") == "adhoc-processing"
    ]
    assert [(item["analysis_group_hint"], item["count"], item["calls"]) for item in candidates] == [
        (
            ["claude:parent-session"],
            1,
            [{"record": "claude:parent-session", "line": 2, "text": f"jq -r '.line' {saved_output}"}],
        ),
        (
            ["claude:parent-session/agent-child"],
            2,
            [
                {
                    "record": "claude:parent-session/agent-child",
                    "line": 1,
                    "text": "python3 - <<'EOF'\nimport json\nprint(json.dumps({'start': 1, 'end': 48}))\nEOF",
                },
                {
                    "record": "claude:parent-session/agent-child",
                    "line": 3,
                    "text": "uv run --frozen python -c 'print(1)'",
                },
            ],
        ),
        (
            [f"codex:{_ADHOC_THREAD}"],
            1,
            [
                {
                    "record": f"codex:{_ADHOC_THREAD}",
                    "line": 2,
                    "text": _CODEX_EXEC_INPUT,
                }
            ],
        ),
    ]


def test_observation_boundary_does_not_apply_to_delegate_records(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """境界前に始まった委譲先の終了記録を保持し、後発の委譲先を除く。"""
    transcript = _write_transcript(
        tmp_path,
        [timestamped_entry("2026-09-01T00:00:01Z", "メイン記録")],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            timestamped_entry("2026-09-01T00:00:01Z", "境界前の委譲先記録"),
            timestamped_entry("2026-09-01T00:00:03Z", "境界後の委譲先結果"),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-review",
        [
            timestamped_entry("2026-09-01T00:00:04Z", "後発の振り返り失敗"),
            hook_attachment(
                {
                    "type": "hook_system_message",
                    "hookName": "After",
                    "toolUseID": "review",
                    "content": "[auto-generated: test/review][warn] 後発の振り返り失敗",
                }
            )
            | {"timestamp": "2026-09-01T00:00:05Z"},
        ],
    )

    base = [str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]
    assert evidence.main(base) == 0

    events = read_jsonl(capsys, raw=True)
    assert [
        event["text"] for event in events if event["kind"] == "user" and event["record"] == "claude:transcript/agent-child"
    ] == [
        "境界前の委譲先記録",
        "境界後の委譲先結果",
    ]
    assert all(event["record"] != "agent-review" for event in events)

    assert evidence.main([*base, "--warn"]) == 0
    assert read_jsonl(capsys) == [{"kind": "warning", "text": "一致なし"}]
    assert evidence.main([*base, "--grep", "後発"]) == 0
    assert read_jsonl(capsys)[-1] == {"kind": "summary", "count": 0}
    assert evidence.main([*base, "--stats"]) == 0
    assert [event["agent"] for event in events_by_kind(read_jsonl(capsys), "stats-subagent")] == [
        "claude:transcript/agent-child"
    ]

    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    assert evidence.main([*base, "--bundle", str(bundle_dir)]) == 0
    read_jsonl(capsys)
    assert "後発の振り返り失敗" not in (bundle_dir / "candidates.jsonl").read_text(encoding="utf-8")
