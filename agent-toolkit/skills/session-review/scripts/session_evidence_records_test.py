"""証拠抽出の対象記録の解決と付随記録の収集（`session_evidence_records.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib
from typing import Literal

import pytest
import session_evidence_detail as evidence_detail
import session_evidence_extract as evidence_extract
import session_evidence_records as evidence_records
import session_evidence_stats as evidence_stats
import session_evidence_warn as evidence_warn
import session_review_evidence as evidence
from session_review_evidence_test import (
    _bundle_delegate_return_texts,
    _user_events,
)

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    agy_delegation_transcript,
    agy_step,
    assistant_text,
    codex_tool_result_entry,
    codex_tool_use_entry,
    events_by_kind,
    execution_tool_use,
    read_jsonl,
    timestamped_entry,
    write_agy_log,
    write_jsonl,
    write_subagent,
)


def test_main_resolves_codex_transcript_from_thread_id(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """thread IDだけで一意な親rolloutを解決して抽出する。"""
    thread_id = "11111111-1111-4111-8111-111111111111"
    codex_home = tmp_path / "codex"
    write_jsonl(
        codex_home / "sessions" / "2026" / "09" / "01" / f"rollout-parent-{thread_id}.jsonl",
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "thread IDから解決した記録"},
            }
        ],
    )

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(codex_home)]) == 0

    assert read_jsonl(capsys) == [
        {
            "kind": "user",
            "text": "thread IDから解決した記録",
            "runtime_inserted": False,
            "line": 1,
            "timestamp": None,
            "sequence": 1,
        }
    ]


def test_main_rejects_ambiguous_or_missing_codex_thread_id(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """親rolloutが0件または複数件なら証拠不足として終了コード2を返す。"""
    thread_id = "22222222-2222-4222-8222-222222222222"
    codex_home = tmp_path / "codex"

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(codex_home)]) == 2
    missing = read_jsonl(capsys)
    assert missing == [
        {
            "kind": "error",
            "text": f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが"
            f"{codex_home / 'sessions'}配下に無い",
        }
    ]

    candidates = [codex_home / "sessions" / "2026" / "09" / day / f"rollout-parent-{thread_id}.jsonl" for day in ("01", "02")]
    for day, path in zip(("01", "02"), candidates, strict=True):
        write_jsonl(
            path,
            [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": day}}],
        )

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(codex_home)]) == 2
    ambiguous = read_jsonl(capsys)
    assert ambiguous == [
        {
            "kind": "error",
            "text": f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが複数ある: "
            + ", ".join(str(path) for path in sorted(candidates)),
        }
    ]


def test_codex_home_resolution_order(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示引数、空でない環境変数、ホームから求めた値の順に保存先を解決する。"""
    thread_id = "33333333-3333-4333-8333-333333333333"
    home = tmp_path / "home"
    roots = {
        "default": home / ".codex",
        "environment": tmp_path / "environment-codex",
        "explicit": tmp_path / "explicit-codex",
    }
    for label, root in roots.items():
        write_jsonl(
            root / "sessions" / "2026" / "09" / "01" / f"rollout-parent-{thread_id}.jsonl",
            [
                {
                    "type": "response_item",
                    "payload": {"type": "message", "role": "user", "content": label},
                }
            ],
        )
    monkeypatch.setenv("HOME", str(home))

    monkeypatch.delenv("CODEX_HOME", raising=False)
    assert evidence.main(["--codex-thread-id", thread_id]) == 0
    assert read_jsonl(capsys)[0]["text"] == "default"

    monkeypatch.setenv("CODEX_HOME", "")
    assert evidence.main(["--codex-thread-id", thread_id]) == 0
    assert read_jsonl(capsys)[0]["text"] == "default"

    monkeypatch.setenv("CODEX_HOME", str(roots["environment"]))
    assert evidence.main(["--codex-thread-id", thread_id]) == 0
    assert read_jsonl(capsys)[0]["text"] == "environment"

    assert evidence.main(["--codex-thread-id", thread_id, "--codex-home", str(roots["explicit"])]) == 0
    assert read_jsonl(capsys)[0]["text"] == "explicit"


def test_main_requires_exactly_one_transcript_source(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """メイン記録のパスとthread IDは同時指定も同時省略も拒否する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "入力"}}])
    thread_id = "44444444-4444-4444-8444-444444444444"

    assert evidence.main([str(transcript), "--codex-thread-id", thread_id]) == 2
    assert read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": (
                "transcript_path・--transcript・--claude-session-id・--codex-thread-id・カタログ走査はいずれか一つだけを指定する"
            ),
        }
    ]

    assert evidence.main([]) == 2
    assert read_jsonl(capsys) == [
        {
            "kind": "error",
            "text": (
                "transcript_path・--transcript・--claude-session-id・--codex-thread-id・カタログ走査はいずれか一つだけを指定する"
            ),
        }
    ]


def test_default_events_separate_main_user_message_from_subagent_task_prompt(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人間の入力と委譲先のタスク入力を由来記録で区別する。"""
    transcript = _write_transcript(tmp_path, [timestamped_entry("2026-09-01T00:00:01Z", "人間の入力")])
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [timestamped_entry("2026-09-01T00:00:02Z", "タスク入力")],
    )

    assert evidence.main([str(transcript)]) == 0

    user_events = [event for event in read_jsonl(capsys, raw=True) if event["kind"] == "user"]
    assert [(event["record"], event["text"]) for event in user_events] == [
        ("claude:transcript", "人間の入力"),
        ("claude:transcript/agent-child", "タスク入力"),
    ]


def test_reconciliation_repeats_until_no_main_user_intervention_is_added(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """再取得中に届いた人間の入力も次の境界で追加し、新しい入力が0件になるまで比較する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:01Z", "初回境界前の入力"),
            timestamped_entry("2026-09-01T00:00:03Z", "初回境界後の入力"),
            timestamped_entry("2026-09-01T00:00:05Z", "1回目の再取得中の入力"),
            timestamped_entry("2026-09-01T00:00:07Z", "2回目の再取得中の入力"),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [timestamped_entry("2026-09-01T00:00:07Z", "委譲先のタスク入力")],
    )

    assert evidence.main([str(transcript), "--observation-boundary", "2026-09-01T00:00:02Z"]) == 0
    initial_events = read_jsonl(capsys, raw=True)
    known_locators = {(event["record"], event["line"]) for event in initial_events if "line" in event}
    additions_by_reconciliation = []
    for boundary in [
        "2026-09-01T00:00:04Z",
        "2026-09-01T00:00:06Z",
        "2026-09-01T00:00:08Z",
        "2026-09-01T00:00:09Z",
    ]:
        assert evidence.main([str(transcript), "--observation-boundary", boundary]) == 0
        additional_users = [
            event
            for event in read_jsonl(capsys, raw=True)
            if event["kind"] == "user"
            and event["record"] == "claude:transcript"
            and (event["record"], event["line"]) not in known_locators
        ]
        additions_by_reconciliation.append([event["text"] for event in additional_users])
        known_locators.update((event["record"], event["line"]) for event in additional_users)

    assert additions_by_reconciliation == [
        ["初回境界後の入力"],
        ["1回目の再取得中の入力"],
        ["2回目の再取得中の入力"],
        [],
    ]


def test_user_events_resolves_claude_session_id(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """セッション識別子から親transcriptを解決し、パスを渡した場合と同じユーザーイベントを返す。

    呼び出し元は自身の`CLAUDE_CODE_SESSION_ID`から記録を指定するため、
    `projects`配下の作業ディレクトリを符号化した名前を組み立てずに同じ原文を得られる必要がある。
    """
    session_id = "11111111-2222-4333-8444-555555555555"
    home = tmp_path / "home"
    transcript = home / ".claude" / "projects" / "-repo" / f"{session_id}.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.write_text(
        "".join(
            json.dumps(entry, ensure_ascii=False) + "\n"
            for entry in (
                timestamped_entry("2026-08-31T23:59:59Z", "区間前"),
                timestamped_entry("2026-09-01T00:00:01Z", "依頼の原文"),
            )
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))

    by_session_id = _user_events(["--claude-session-id", session_id], capsys)
    by_path = _user_events(["--transcript", str(transcript)], capsys)

    assert by_session_id == by_path
    assert [event["text"] for event in by_session_id if event["kind"] == "user"] == ["依頼の原文"]


@pytest.mark.parametrize(
    ("name", "arguments", "expected"),
    [
        ("mcp__plugin_agent-toolkit_agents_server__start", {"mode": "shell", "command": "make test"}, True),
        ("mcp__agents_server__start", {"mode": "explore", "prompt": "調査"}, False),
        ("mcp__agents_server__start", {"subagent_md_path": "/plugin/share/exec.subagent.md"}, False),
        # 統合前の記録はツール名でコマンド実行を表す。
        ("mcp__agents_server__start_shell", {"command": "make test"}, True),
        ("mcp__agents_server__start_explore", {"prompt": "調査"}, False),
    ],
)
def test_execution_tool_kind_follows_start_mode_and_legacy_name(name: str, arguments: dict[str, str], expected: bool) -> None:
    """コマンド実行の結果として警告を走査するかを、`start`のmodeと統合前のツール名の双方から判定する。"""
    kind = evidence_warn._execution_kind_name(name, arguments)  # pylint: disable=protected-access
    assert evidence_warn._is_execution_tool_name(kind) is expected  # pylint: disable=protected-access


def test_agy_delegate_without_log_is_unresolved_record(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """状態ディレクトリにログが無いagyの委譲先は、記録を解決できない委譲先として報告する。"""
    session_id = "b9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    transcript = agy_delegation_transcript(tmp_path, session_id)

    assert evidence.main([str(transcript)]) == 0

    events = read_jsonl(capsys, raw=True)
    assert events_by_kind(events, "unresolved-record") == [{"kind": "unresolved-record", "record": session_id, "line": 3}]


def test_agy_grandchild_launch_is_neither_detected_nor_collected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """agyの委譲先が孫を起動しても、孫の記録を探さず、未解決の委譲としても報告しない。"""
    session_id = "c9244465-1577-44a9-a7b0-500b1f976b0e"
    grandchild = "d9244465-1577-44a9-a7b0-500b1f976b0e"
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    write_agy_log(
        tmp_path / "state",
        session_id,
        [
            agy_step(
                1,
                "tool",
                "DONE",
                tool_name="invoke_subagent",
                tool_info={"name": "invoke_subagent", "parameters": {"Prompt": "調査"}, "output": {"session_id": grandchild}},
            ),
            agy_step(
                2,
                "tool",
                "DONE",
                tool_name="call_mcp_tool",
                tool_info={
                    "name": "call_mcp_tool",
                    "parameters": {"ServerName": "agents_server", "ToolName": "start"},
                    "output": json.dumps({"session_id": grandchild}),
                },
            ),
            {"event": "result", "result": {"conversation_id": session_id, "status": "SUCCESS", "response": "完了した"}},
        ],
    )
    transcript = agy_delegation_transcript(tmp_path, session_id)

    assert evidence.main([str(transcript)]) == 0

    events = read_jsonl(capsys, raw=True)
    assert f"agy:{session_id}" in {event.get("record") for event in events}
    assert not events_by_kind(events, "unresolved-record")
    assert not events_by_kind(events, "unresolved-delegation")
    assert grandchild not in json.dumps(events, ensure_ascii=False)


# pylint: disable=protected-access
def test_stats_reports_critical_path() -> None:
    """並行threadの重複を二重計上せず、測定不能なthreadも分けて返す。"""

    def collected(
        record_id: str, timestamps: list[str], *, role: Literal["main", "subagent", "session"] = "session"
    ) -> evidence_extract._CollectedRecord:
        return evidence_extract._CollectedRecord(
            record_id,
            pathlib.Path(f"/{record_id}"),
            [evidence_extract._Record(index + 1, "", {"timestamp": timestamp}) for index, timestamp in enumerate(timestamps)],
            "codex",
            "main" if role == "session" else None,
            1 if role == "session" else None,
            None,
            role,
        )

    events = evidence_stats._stats_events(
        [
            collected("main", ["2026-09-02T00:00:00Z", "2026-09-02T00:01:00Z"], role="main"),
            collected("codex:first", ["2026-09-02T00:00:10Z", "2026-09-02T00:00:30Z"]),
            collected("codex:second", ["2026-09-02T00:00:20Z", "2026-09-02T00:00:40Z"]),
            collected("codex:unmeasured", []),
        ],
        pathlib.Path("/missing-compaction-records"),
    )

    assert events_by_kind(events, "stats-critical-path") == [
        {
            "kind": "stats-critical-path",
            "elapsed_seconds": 60.0,
            "main_only_seconds": 30.0,
            "overlap_seconds": 10.0,
            "segments": [
                {"owner": "first", "exclusive_seconds": 10.0},
                {"owner": "second", "exclusive_seconds": 10.0},
            ],
            "unmeasured_threads": ["unmeasured"],
        }
    ]


def test_claude_subagent_record_keeps_normal_entries(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲された依頼"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "実装を完了した"}]},
            },
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["委譲された依頼", "実装を完了した"]


# pylint: disable=protected-access
def test_claude_child_keeps_same_canonical_id_when_opened_directly_or_from_parent(tmp_path: pathlib.Path) -> None:
    """同じ物理記録は収集元の指定に依存せず同じ正規IDを持ち、収集元固有名は別名としてだけ残す。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "message": {"role": "user", "content": "親の依頼"}}],
    )
    child_dir = transcript.with_suffix("") / "subagents"
    write_subagent(
        child_dir,
        "agent-child",
        [{"type": "user", "message": {"role": "user", "content": "子の依頼"}}],
    )
    child_path = child_dir / "agent-child.jsonl"
    parent_records = evidence_extract._load_records(str(transcript))
    child_records = evidence_extract._load_records(str(child_path))
    assert parent_records is not None
    assert child_records is not None

    collected_from_parent, _ = evidence_records._collect_records(str(transcript), parent_records)
    collected_directly, _ = evidence_records._collect_records(str(child_path), child_records)
    nested = next(item for item in collected_from_parent if item.path == child_path)
    direct = collected_directly[0]

    assert nested.record_id == direct.record_id == "claude:transcript/agent-child"
    assert nested.aliases == ("agent-child",)
    assert direct.aliases == ("main",)


def test_detail_rejects_ambiguous_legacy_record_alias(tmp_path: pathlib.Path) -> None:
    """同名の旧サブエージェントIDが複数ある場合は誤った記録を選ばず拒否する。"""
    record = evidence_extract._Record(1, "", {"type": "user", "message": {"role": "user", "content": "依頼"}})
    collected = [
        evidence_extract._CollectedRecord(
            f"claude:parent-{index}/agent-child",
            tmp_path / f"parent-{index}" / "subagents" / "agent-child.jsonl",
            [record],
            "claude",
            f"claude:parent-{index}",
            None,
            None,
            "subagent",
            ("agent-child",),
        )
        for index in (1, 2)
    ]

    events, exit_code = evidence_detail._detail_collection_events(collected, ["agent-child:1"])

    assert exit_code == 2
    assert events[0]["text"] == "記録別名が曖昧: agent-child"


def test_claude_subagent_handback_message_becomes_final_result(tmp_path: pathlib.Path) -> None:
    """`SubagentHandback`で渡した報告本文を最終結果とし、送信後の定型文を最終結果にしない。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲された依頼"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "SubagentHandback",
                            "input": {"message": "判定: 適合\n根拠: 全行を確認した"},
                        }
                    ],
                },
            },
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Report delivered."}]},
            },
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert [(event["kind"], event["text"]) for event in events] == [
        ("user", "委譲された依頼"),
        ("final-result", "判定: 適合\n根拠: 全行を確認した"),
        ("assistant", "Report delivered."),
    ]


def test_bundle_excludes_interim_text_of_running_delegate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最後の行動がツール呼び出しである委譲先（抽出時点で稼働中）の途中発話を委譲返却の候補にしない。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            assistant_text("本文の起草に入ります。"),
            execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}]},
            },
            assistant_text("続行できない理由: 途中の見込み"),
            execution_tool_use("call-2"),
        ],
    )
    capsys.readouterr()

    assert texts == []


def test_bundle_keeps_final_text_of_finished_delegate(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """最後の本文の後にツール呼び出しが無い委譲先では、その本文を委譲返却の候補にし、途中発話は候補にしない。"""
    texts = _bundle_delegate_return_texts(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "委譲された依頼"}},
            assistant_text("調査を始めます。"),
            execution_tool_use("call-1"),
            {
                "type": "user",
                "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}]},
            },
            assistant_text("調査結果を報告する。\n対象の関数は3件だった。"),
        ],
    )
    capsys.readouterr()

    assert texts == ["調査結果を報告する。\n対象の関数は3件だった。"]


def test_claude_main_record_keeps_only_completion_from_subagent_entries(tmp_path: pathlib.Path) -> None:
    transcript = _write_transcript(
        tmp_path,
        [
            {"type": "user", "message": {"role": "user", "content": "依頼"}},
            {"type": "user", "isSidechain": True, "message": {"role": "user", "content": "委譲された依頼"}},
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {"role": "assistant", "content": [{"type": "text", "text": "実装を完了した"}]},
            },
        ],
    )

    events = evidence_extract.load_and_extract(str(transcript))

    assert [event["text"] for event in events] == ["依頼"]


def test_delegate_record_with_ambiguous_thread_id_is_unresolved(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """委譲先rolloutの複数一致では先頭を採用せず未解決として報告する。"""
    thread_id = "55555555-5555-4555-8555-555555555555"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = _write_transcript(
        tmp_path,
        [
            codex_tool_use_entry("2026-09-01T00:00:00Z", "ambiguous", thread_id),
            codex_tool_result_entry("2026-09-01T00:00:00Z", "ambiguous", thread_id),
        ],
    )
    for day, text in (("01", "混入してはならない記録1"), ("02", "混入してはならない記録2")):
        write_jsonl(
            codex_home / "sessions" / "2026" / "09" / day / f"rollout-child-{thread_id}.jsonl",
            [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": text}}],
        )

    assert evidence.main([str(transcript)]) == 0

    events = read_jsonl(capsys, raw=True)
    assert events[-1] == {"kind": "unresolved-record", "record": thread_id, "line": 2}
    serialized = json.dumps(events, ensure_ascii=False)
    assert "混入してはならない記録1" not in serialized
    assert "混入してはならない記録2" not in serialized


def test_explicit_codex_home_applies_to_parent_and_delegate_records(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """明示した保存先から親rolloutと委譲先rolloutを再帰収集する。"""
    parent_id = "77777777-7777-4777-8777-777777777777"
    child_id = "88888888-8888-4888-8888-888888888888"
    explicit_home = tmp_path / "explicit-codex"
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "environment-codex"))
    rollout_dir = explicit_home / "sessions" / "2026" / "09" / "01"
    write_jsonl(
        rollout_dir / f"rollout-parent-{parent_id}.jsonl",
        [
            codex_tool_use_entry("2026-09-01T00:00:00Z", "parent-child", child_id),
            codex_tool_result_entry("2026-09-01T00:00:00Z", "parent-child", child_id),
        ],
    )
    write_jsonl(
        rollout_dir / f"rollout-child-{child_id}.jsonl",
        [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": "明示先の委譲記録"},
            }
        ],
    )

    assert evidence.main(["--codex-thread-id", parent_id, "--codex-home", str(explicit_home)]) == 0

    events = [event for event in read_jsonl(capsys, raw=True) if event["kind"] == "user"]
    assert events == [
        {
            "kind": "user",
            "text": "明示先の委譲記録",
            "runtime_inserted": False,
            "line": 1,
            "timestamp": None,
            "sequence": 1,
            "record": f"codex:{child_id}",
        }
    ]


def test_backup_only_thread_id_is_evidence_insufficient(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """backupに写しだけがあるthread IDは親・委譲先のどちらから解決しても証拠不足とする。"""
    thread_id = "66666666-6666-4666-8666-666666666666"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    write_jsonl(
        codex_home / "backups" / "transcripts" / f"rollout-backup-{thread_id}.jsonl",
        [{"type": "response_item", "payload": {"type": "message", "role": "user", "content": "backupの写し"}}],
    )

    assert evidence.main(["--codex-thread-id", thread_id]) == 2
    parent_events = read_jsonl(capsys)
    assert parent_events == [
        {
            "kind": "error",
            "text": f"対象記録を解決できない: Codex thread ID {thread_id}に一致するrolloutが"
            f"{codex_home / 'sessions'}配下に無い",
        }
    ]

    transcript = _write_transcript(
        tmp_path,
        [
            codex_tool_use_entry("2026-09-01T00:00:00Z", "backup-only", thread_id),
            codex_tool_result_entry("2026-09-01T00:00:00Z", "backup-only", thread_id),
        ],
    )
    assert evidence.main([str(transcript)]) == 0
    delegate_events = read_jsonl(capsys, raw=True)
    assert delegate_events[-1] == {"kind": "unresolved-record", "record": thread_id, "line": 2}
    assert "backupの写し" not in json.dumps(delegate_events, ensure_ascii=False)


@pytest.mark.parametrize("runtime", ["claude", "handback", "codex", "agy"])
@pytest.mark.parametrize("long_body", [False, True])
def test_bundle_preserves_improvement_lines_of_every_runtime(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], runtime: str, long_body: bool
) -> None:
    """未転記の改善点を、各記録形式の短い返却と境界・末尾を含む長い返却から候補まで通す。"""
    notes = ["気付いた改善点: 同じ操作を繰り返した。", "  気付いた改善点: 手順に不要な回避があった。"]
    body = "状態: completed\n未解決の指摘数: 0\n"
    if long_body:
        boundary_note = "気付いた改善点: 短縮境界をまたぐ行も全文で保持する。"
        body += "x" * (1994 - len(body)) + "\n" + boundary_note + "\n" + "後続" * 400 + "\n"
        notes.insert(0, boundary_note)
    body += "\n".join(notes[-2:])
    entries: list[dict[str, object]]
    if runtime == "claude":
        entries = [assistant_text(body)]
    elif runtime == "handback":
        entries = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "name": "SubagentHandback", "id": "handback", "input": {"message": body}}],
                },
            },
            assistant_text("返却を渡した。"),
        ]
    elif runtime == "codex":
        entries = [
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final",
                    "content": [{"type": "output_text", "text": body}],
                },
            }
        ]
    else:
        entries = [{"event": "result", "result": {"status": "SUCCESS", "response": body}}]
    texts = _bundle_delegate_return_texts(tmp_path, entries)
    capsys.readouterr()
    assert len(texts) == 1
    for note in notes:
        assert note in texts[0]
    assert texts[0].count("気付いた改善点:") == len(notes)
    if long_body:
        assert "…[省略]" in texts[0]
