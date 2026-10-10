"""証拠抽出の`--user-events`照会（`session_evidence_user_events.py`）を、`session_review_evidence.py`の起動で検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import (
    local_time_transcript,
    read_jsonl,
    timestamped_entry,
    write_subagent,
)


@pytest.mark.parametrize("command", ["/compact", "/compact 指示", "/status"])
@pytest.mark.parametrize("content_array", [False, True])
def test_plugin_origin_and_human_inputs(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], command: str, *, content_array: bool
) -> None:
    """Claude Code 2.1.295のorigin付き記録と同形の人間入力を、生成元だけ変えて対照する。"""
    content = [{"type": "text", "text": command}] if content_array else command
    entries = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
    for origin in (
        {"kind": "plugin", "name": "agent-toolkit"},
        {"kind": "plugin", "name": "別プラグイン"},
        {"kind": "human"},
        None,
    ):
        entry = {"type": "user", "message": {"role": "user", "content": content}}
        if origin is not None:
            entry["origin"] = origin
        entries.append(entry)
    transcript = _write_transcript(tmp_path, entries)
    assert evidence.main([str(transcript), "--user-events"]) == 0
    users = [event for event in read_jsonl(capsys) if event["kind"] == "user"]
    assert [event["text"] for event in users] == ["依頼", command, command]
    assert [event["line"] for event in users] == [1, 4, 5]
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    assert evidence.main([str(transcript), "--bundle", str(bundle)]) == 0
    capsys.readouterr()
    conversation = [json.loads(line) for line in (bundle / "conversation.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [event["text"] for event in conversation if event.get("role") == "user"] == ["依頼", command, command]
    candidates = [json.loads(line) for line in (bundle / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all(
        locator["line"] not in {2, 3}
        for event in candidates
        if event.get("candidate_kind") == "user-intervention"
        for locator in event["locators"]
    )


@pytest.mark.usefixtures("local_time_jst")
@pytest.mark.parametrize("since", ["2026-10-07T00:00:00", "2026-10-07"])
def test_since_without_timezone_is_local_time(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    since: str,
) -> None:
    """タイムゾーンを省いた`--since`は、ローカルタイムゾーンのオフセットを付けた値と同じ結果を返す。"""
    transcript = local_time_transcript(tmp_path)

    assert evidence.main([str(transcript), "--user-events", "--since", since]) == 0
    naive = read_jsonl(capsys)
    assert evidence.main([str(transcript), "--user-events", "--since", "2026-10-07T00:00:00+09:00"]) == 0

    assert naive == read_jsonl(capsys)
    assert [event["text"] for event in naive if event["kind"] == "user"] == ["JSTの10月7日0時1分の発話"]


def test_user_events_returns_main_user_events_in_range(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """指定区間のメイン記録にあるユーザーイベントだけを由来位置付きで返す。"""
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:00Z", "開始前"),
            timestamped_entry("2026-09-01T00:00:01Z", "開始境界"),
            timestamped_entry("2026-09-01T00:00:02Z", "区間内1"),
            timestamped_entry(None, "時刻なし"),
            {
                "type": "assistant",
                "timestamp": "2026-09-01T00:00:03Z",
                "message": {"role": "assistant", "content": "中間報告"},
            },
            timestamped_entry("2026-09-01T00:00:04Z", "区間内2"),
            timestamped_entry("2026-09-01T00:00:05Z", "終了後"),
        ],
    )
    write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [timestamped_entry("2026-09-01T00:00:03Z", "委譲先の入力")],
    )

    assert (
        evidence.main(
            [
                str(transcript),
                "--user-events",
                "--since",
                "2026-09-01T00:00:01Z",
                "--observation-boundary",
                "2026-09-01T00:00:04Z",
            ]
        )
        == 0
    )

    events = read_jsonl(capsys, raw=True)
    assert [(event["record"], event["line"], event["text"]) for event in events[:-1]] == [
        ("claude:transcript", 3, "区間内1"),
        ("claude:transcript", 6, "区間内2"),
    ]
    assert events[-1] == {"kind": "summary", "count": 2}


def test_claude_session_id_unknown_returns_error(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一致する記録が無いセッション識別子は、エラーイベントと終了コード2で拒否する。"""
    (tmp_path / "home" / ".claude" / "projects" / "-repo").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))

    assert (
        evidence.main(
            ["--claude-session-id", "99999999-2222-4333-8444-555555555555", "--user-events", "--since", "2026-09-01T00:00:00Z"]
        )
        == 2
    )
    events = read_jsonl(capsys, raw=True)
    assert [event["kind"] for event in events] == ["error"]
    assert "99999999-2222-4333-8444-555555555555" in events[0]["text"]


def _claude_user_record(content: object, timestamp: str, **fields: object) -> dict:
    """Claude Codeのユーザーロールの記録を返す。"""
    return {"type": "user", "message": {"role": "user", "content": content}, "timestamp": timestamp, **fields}


def _codex_user_record(text: str, timestamp: str) -> dict:
    """Codexのユーザーロールのmessage記録を返す。"""
    return {
        "timestamp": timestamp,
        "type": "response_item",
        "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
    }


# 中断の標識、`<bash-stdout>`、`<command-message>`、`<bash-input>`、`<pasted_content`の各記録は
# Claude Code 2.1.281〜2.1.291の実記録（`~/.claude/projects`配下）から、本文の格納先（文字列かtext要素の配列か）と
# 中断の標識に付く`interruptedMessageId`、人間の入力に付く`origin`の形を写した。本文は短縮した。
_CLAUDE_INTERRUPT = "[Request interrupted by user]"


_CLAUDE_TOOL_INTERRUPT = "[Request interrupted by user for tool use]"


_CLAUDE_COMMAND_WITHOUT_ARGS = (
    "<command-message>agent-toolkit:add-awi-by-user</command-message>\n"
    "<command-name>/agent-toolkit:add-awi-by-user</command-name>"
)


_CLAUDE_COMMAND_WITH_ARGS = (
    "<command-message>agent-toolkit:add-awi-by-user</command-message>\n"
    "<command-name>/agent-toolkit:add-awi-by-user</command-name>\n<command-args>atk serve</command-args>"
)


_CLAUDE_BASH_INPUT = "<bash-input>! sudo bash fix-boot.sh</bash-input>"


_CLAUDE_BASH_STDOUT = "<bash-stdout>+ FS_UUID=24d572ab\n+ MD_SECTORS=620976256</bash-stdout><bash-stderr></bash-stderr>"


_CLAUDE_PASTED = '\n\n<pasted_content id="261b">\n言ってること逆では…？\n</pasted_content id="261b">\n'


# Codexの各記録は2026年8月〜10月のrollout（`~/.codex/sessions`配下）の`response_item`の`message`から形を写した。
_CODEX_INTERNAL_CONTEXT = (
    '<codex_internal_context source="goal">\nContinue working toward the active thread goal.\n</codex_internal_context>'
)


_CODEX_HOOK_PROMPT = '<hook_prompt hook_run_id="stop:8:hooks.codex.json">&lt;atk-auto kind="block"&gt;通知</hook_prompt>'


_CODEX_SUBAGENT_NOTIFICATION = '<subagent_notification>\n{"agent_path":"01a0280f","status":{"completed":"status: completed"}}'


_CODEX_QUESTION_REPLY = (
    '<send_user_message_question_reply>\n[{"answer":"選択後の1回だけ有効（推奨）","question":"どの状態ですか"}]'
)


@pytest.mark.parametrize("host", ["claude", "codex"])
def test_user_events_excludes_runtime_markers_and_keeps_human_forms(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    """実行環境の出力と中断の標識を発話から除き、人間が入力した形式は本文を変えずに返す。

    除き損なうと、WI投入担当が実行環境の出力や中断の定型文を人間の発話として逐語引用する。
    人間の形式を除くと、手動のスキル起動や貼り付けた本文が出所から失われる。
    `--since`を省略した照会で、記録の最初の発話から返ることも確かめる。
    """
    if host == "claude":
        entries = [
            _claude_user_record("最初の依頼", "2026-09-01T00:00:01Z", origin={"kind": "human"}),
            _claude_user_record(
                [{"type": "text", "text": _CLAUDE_INTERRUPT}], "2026-09-01T00:00:02Z", interruptedMessageId="msg_1"
            ),
            _claude_user_record(
                [{"type": "text", "text": _CLAUDE_TOOL_INTERRUPT}], "2026-09-01T00:00:03Z", interruptedMessageId="msg_2"
            ),
            _claude_user_record(_CLAUDE_COMMAND_WITHOUT_ARGS, "2026-09-01T00:00:04Z", origin={"kind": "human"}),
            _claude_user_record(_CLAUDE_COMMAND_WITH_ARGS, "2026-09-01T00:00:05Z", origin={"kind": "human"}),
            _claude_user_record(_CLAUDE_BASH_INPUT, "2026-09-01T00:00:06Z"),
            _claude_user_record(_CLAUDE_BASH_STDOUT, "2026-09-01T00:00:07Z", turnOrigin="human"),
            _claude_user_record(_CLAUDE_PASTED, "2026-09-01T00:00:08Z", origin={"kind": "human"}),
        ]
        expected = [
            (1, "最初の依頼"),
            (4, _CLAUDE_COMMAND_WITHOUT_ARGS),
            (5, _CLAUDE_COMMAND_WITH_ARGS),
            (6, _CLAUDE_BASH_INPUT),
            (8, _CLAUDE_PASTED.strip()),
        ]
    else:
        entries = [
            _codex_user_record("最初の依頼", "2026-09-01T00:00:01Z"),
            _codex_user_record(_CODEX_INTERNAL_CONTEXT, "2026-09-01T00:00:02Z"),
            _codex_user_record(_CODEX_HOOK_PROMPT, "2026-09-01T00:00:03Z"),
            _codex_user_record(_CODEX_SUBAGENT_NOTIFICATION, "2026-09-01T00:00:04Z"),
            _codex_user_record(_CODEX_QUESTION_REPLY, "2026-09-01T00:00:05Z"),
        ]
        expected = [(1, "最初の依頼"), (5, _CODEX_QUESTION_REPLY)]
    transcript = _write_transcript(tmp_path, entries)

    assert evidence.main([str(transcript), "--user-events"]) == 0

    events = read_jsonl(capsys, raw=True)
    assert [(event["line"], event["text"]) for event in events if event["kind"] == "user"] == expected
    assert events[-1] == {"kind": "summary", "count": len(expected)}


def test_user_events_without_since_starts_at_first_record(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--since`を省略すると記録の最初から、指定すると指定時刻より後だけを、同じ形の行で返す。

    省略時に開始境界を推測で補うと最初の発話が欠け、指定時の範囲が変わると既存の呼び出しの出所が変わる。
    """
    transcript = _write_transcript(
        tmp_path,
        [
            timestamped_entry("2026-09-01T00:00:00Z", "最初の発話"),
            timestamped_entry(None, "時刻なし"),
            timestamped_entry("2026-09-01T00:00:02Z", "後の発話"),
        ],
    )

    assert evidence.main([str(transcript), "--user-events"]) == 0
    without_since = read_jsonl(capsys, raw=True)
    assert evidence.main([str(transcript), "--user-events", "--since", "2026-09-01T00:00:01Z"]) == 0
    with_since = read_jsonl(capsys, raw=True)

    assert [(event["line"], event["text"]) for event in without_since[:-1]] == [
        (1, "最初の発話"),
        (2, "時刻なし"),
        (3, "後の発話"),
    ]
    assert [(event["line"], event["text"]) for event in with_since[:-1]] == [(3, "後の発話")]
    assert with_since[-1] == {"kind": "summary", "count": 1}


def test_user_events_rejects_misused_since(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """照会開始境界の誤用・不正値と他モード併用を拒否する。`--since`の省略は受理する。"""
    transcript = _write_transcript(
        tmp_path,
        [timestamped_entry("2026-09-01T00:00:01Z", "入力")],
    )

    invocations = (
        [str(transcript), "--since", "2026-09-01T00:00:00Z"],
        [str(transcript), "--user-events", "--since", "不正な時刻"],
        [str(transcript), "--user-events", "--since", "2026-09-01T00:00:00Z", "--warn"],
    )
    for arguments in invocations:
        assert evidence.main(arguments) == 2
        assert read_jsonl(capsys)[0]["kind"] == "error"
    assert evidence.main([str(transcript), "--user-events"]) == 0
    assert [event["text"] for event in read_jsonl(capsys) if event["kind"] == "user"] == ["入力"]


def test_saved_user_events_preserves_original_positions_and_all_fields(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """保存済み出力の物理行ではなく元の組を選び、重複と確認回答を入力順・全情報で返す。"""
    # 保存形式は同モジュールの--user-events出力。質問と選択肢の欄も照会では再解釈しない。
    first = {"kind": "user", "record": "codex:parent:child", "line": 91, "text": "長い原文\n" * 2000}
    answer = {
        "kind": "user",
        "record": "codex:parent:child",
        "line": 91,
        "text": "確認回答",
        "assistant_context": [{"question": "どれを選びますか", "options": ["A", "B", "C"]}],
        "user_response": [{"answers": ["B"], "notes": "自由記述\nの全文"}],
        "extra": {"retained": True},
    }
    path = tmp_path / "events.jsonl"
    entries = [{**first, "record": "claude:other"}, first, {**first, "line": 2}, answer]
    path.write_text("\n".join(json.dumps(event, ensure_ascii=False) for event in entries), encoding="utf-8")

    assert evidence.main(["--user-events-file", str(path), "--user-event-at", "codex:parent:child:91"]) == 0
    assert read_jsonl(capsys, raw=True) == [first, answer]


@pytest.mark.parametrize(
    "position",
    [
        "missing:91",
        "codex:parent:child:0",
        "codex:parent:child:-1",
        "codex:parent:child:x",
        "91",
        "codex:parent:child:１",
        ":91",
    ],
)
def test_saved_user_events_rejects_unknown_or_invalid_position(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], position: str
) -> None:
    """不在と不正位置は成功や部分出力にせず、次の操作を持つerrorだけを返す。"""
    path = tmp_path / "events.jsonl"
    path.write_text(json.dumps({"kind": "user", "record": "codex:parent:child", "line": 91, "text": "本文"}), encoding="utf-8")
    assert evidence.main(["--user-events-file", str(path), "--user-event-at", position]) == 2
    events = read_jsonl(capsys, raw=True)
    assert len(events) == 1 and events[0]["kind"] == "error"
    assert events[0]["next_action"]


@pytest.mark.parametrize("invalid", [None, b"\xff", b'{"record":"main","line":1}\n{broken', b"[]"])
def test_saved_user_events_rejects_unreadable_or_invalid_jsonl(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], invalid: bytes | None
) -> None:
    """一致行の後ろも含めて保存ファイルを読み、壊れた入力を成功にしない。"""
    path = tmp_path / "events.jsonl"
    if invalid is not None:
        path.write_bytes(invalid)
    assert evidence.main(["--user-events-file", str(path), "--user-event-at", "main:1"]) == 2
    events = read_jsonl(capsys, raw=True)
    assert len(events) == 1 and events[0]["kind"] == "error"
    assert events[0]["next_action"]


@pytest.mark.parametrize(
    "extra",
    [
        ["--transcript", "missing"],
        ["--warn"],
        ["--grep", ""],
        ["--user-events"],
        ["--since", "2026-10-08"],
        ["--detail", "1"],
        ["--bundle", "missing"],
        ["--codex-thread-id", "missing"],
    ],
)
def test_saved_user_events_rejects_original_record_modes(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], extra: list[str]
) -> None:
    """元記録の照会と保存済み照会を混ぜず、空の文字列の引数も指定として拒否する。"""
    path = tmp_path / "events.jsonl"
    path.write_text('{"record":"main","line":1,"text":"本文"}', encoding="utf-8")
    assert evidence.main(["--user-events-file", str(path), "--user-event-at", "main:1", *extra]) == 2
    events = read_jsonl(capsys, raw=True)
    assert len(events) == 1 and events[0]["kind"] == "error" and events[0]["next_action"]


@pytest.mark.parametrize(
    "arguments",
    [
        ["--user-events-file", "/missing.jsonl"],
        ["--user-event-at", "main:1"],
        ["--user-events-file", "relative.jsonl", "--user-event-at", "main:1"],
    ],
)
def test_saved_user_events_requires_paired_arguments_and_absolute_path(
    capsys: pytest.CaptureFixture[str], arguments: list[str]
) -> None:
    """入力の組と絶対パスを呼出境界で検査する。"""
    assert evidence.main(arguments) == 2
    events = read_jsonl(capsys, raw=True)
    assert len(events) == 1 and events[0]["kind"] == "error" and events[0]["next_action"]
