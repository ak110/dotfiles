"""公開run-scriptから会話の選択・有限表示・同一bundleの継続と詳細取得を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import shlex
from typing import Any

import pytest
import session_review_evidence as evidence

from agent_toolkit._atk import run_script
from agent_toolkit._testing import session_evidence_support as support


def _run(arguments: list[str], script: str = "session-review-prepare") -> int:
    return run_script.dispatch(argparse.Namespace(script_name=script, script_args=arguments))


def _prepared(capsys: pytest.CaptureFixture[str]) -> tuple[dict[str, Any], str]:
    captured = capsys.readouterr()
    assert not captured.err
    result = json.loads(captured.out)
    document = pathlib.Path(result["conversation_path"]).read_bytes().decode("utf-8")
    assert len(document.encode("utf-8")) == result["conversation_utf8_bytes"]
    assert result["conversation_utf8_bytes"] <= result["conversation_limit_bytes"]
    return result, document


def test_window_continuation_and_full_detail(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """長文・絵文字・合法なJSON内改行文字の省略分へ、公開の継続とdetailから到達する。"""
    transcript = tmp_path / "main.jsonl"
    text = "先頭😀\u2028中間\u2029" + "長文😀" * 4000 + "末尾の原文"
    command_text = "操作😀" * 1500
    entries = [
        support.timestamped_entry("2026-11-01T00:00:00Z", text),
        support.timestamped_entry(
            "2026-11-01T00:00:00Z", '<atk-auto source="test" kind="notice">表示から除く機械資料</atk-auto>'
        ),
        support.claude_call("2026-11-01T00:00:01Z", "long-input", "Bash", {"command": command_text}),
    ]
    for index in range(30):
        entry = support.assistant_text(f"途中発話{index}" + "継続中" * 500)
        entry["timestamp"] = f"2026-11-01T00:{index + 2:02}:00Z"
        entries.append(entry)
    support.write_jsonl(transcript, entries)
    work = tmp_path / "work"
    work.mkdir()
    arguments = ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-window"]
    assert _run(arguments) == 0
    result, document = _prepared(capsys)
    window = result["conversation_window"]
    assert result["conversation_limit_bytes"] == 16000
    assert window["target_count"] == 32 and 0 < window["displayed_count"] < 32
    assert window["excerpt_count"] > 0 and window["has_more"]
    assert "末尾の原文" not in document
    assert "表示から除く機械資料" not in document
    bundle = work / "bundle" / "conversation.jsonl"
    assert json.loads(bundle.read_text(encoding="utf-8").split("\n")[0])["text"] == text
    assert _run([str(transcript), "--detail", "claude:main:1"], "session-review-evidence") == 0
    details = [json.loads(line) for line in capsys.readouterr().out.split("\n") if line.strip()]
    assert any(text in event.get("text", "") for event in details)
    assert _run([str(transcript), "--detail", "claude:main:3"], "session-review-evidence") == 0
    details = [json.loads(line) for line in capsys.readouterr().out.split("\n") if line.strip()]
    assert any(event.get("input", {}).get("command") == command_text for event in details)
    # 原記録の増加は保存bundleのイベント番号へ混ぜず、同じ抽出結果で継続する。
    support.write_jsonl(transcript, [*entries, support.assistant_text("後から追記した発話")])
    command = re.search(r"同じbundle・条件の続き: `([^`]+)`", document)
    assert command is not None
    command_arguments = shlex.split(command[1])[4:]
    assert _run(command_arguments) == 0
    later, continuation = _prepared(capsys)
    assert later["conversation_window"]["observed_at"] == window["observed_at"]
    positions = [int(value) for value in re.findall(r"^- \[(\d+)\]", continuation, re.MULTILINE)]
    assert positions and min(positions) > window["next_after"]
    assert "後から追記した発話" not in continuation


def test_window_selection_unknown_time_and_incomplete_call(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """記録の複数指定・timezone付き期間・recentを適用し、空・未知時刻・未完了呼出を区別する。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(
        transcript,
        [
            support.timestamped_entry("2026-11-01T00:00:00Z", "境界前"),
            support.timestamped_entry("2026-11-01T00:01:00Z", "開始境界"),
            support.timestamped_entry("2026-11-01T00:02:00Z", "終了境界"),
            support.assistant_text("未知時刻の途中発話"),
            support.claude_call("2026-11-01T00:02:00Z", "pending", "Bash", {"command": "処理中の操作"}),
        ],
    )
    support.write_subagent(transcript.with_suffix("") / "subagents", "agent-child", [support.assistant_text("子の途中発話")])
    work = tmp_path / "work"
    work.mkdir()
    base = ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-window"]
    options = [
        "--conversation-record",
        "claude:main",
        "--conversation-record",
        "claude:main/agent-child",
        "--conversation-since",
        "2026-11-01T09:01:00+09:00",
        "--conversation-until",
        "2026-11-01T00:02:00Z",
        "--conversation-recent",
        "2",
    ]
    assert _run([*base, *options]) == 0
    result, document = _prepared(capsys)
    window = result["conversation_window"]
    assert window["target_count"] == 3 and window["displayed_count"] == 2
    assert window["unknown_timestamp_count"] == 2
    assert "開始境界" not in document and "境界前" not in document
    assert "終了境界" in document and "処理中の操作" in document
    assert "結果位置未確認" in document and "最終観測発話=" not in document
    assert _run([*base, "--conversation-after", "0", "--conversation-record", "存在しない担当"]) == 0
    empty, document = _prepared(capsys)
    assert empty["conversation_window"]["target_count"] == 0
    assert empty["conversation_window"]["unknown_record_count"] == 1
    assert "指定記録未確認: 1件" in document


@pytest.mark.parametrize(
    "option,value",
    [
        ("--conversation-recent", "0"),
        ("--conversation-after", "-1"),
        ("--conversation-max-bytes", "0"),
        ("--conversation-since", "invalid"),
        ("--conversation-until", "2026-11-01T00:00:00"),
    ],
)
def test_window_rejects_invalid_options(option: str, value: str, capsys: pytest.CaptureFixture[str]) -> None:
    """不正な件数・位置・予算・日時は公開パーサーで終了2となる。"""
    assert _run(["--transcript", "unused", "--conversation-window", option, value]) == 2
    assert capsys.readouterr().err


@pytest.mark.parametrize("limit", [1, 4000, 16000])
def test_window_budget_and_long_locator(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], limit: int) -> None:
    """長い記録IDでも最小固定部/行を検証し、成功文書を指定予算以内に保つ。"""
    transcript = tmp_path / ("r" * 180 + ".jsonl")
    support.write_jsonl(transcript, [support.timestamped_entry(None, "絵文字😀" * 1000)])
    work = tmp_path / "work"
    work.mkdir()
    code = _run(
        [
            "--transcript",
            str(transcript),
            "--work-dir",
            str(work),
            "--conversation-window",
            "--conversation-max-bytes",
            str(limit),
        ]
    )
    if limit == 1:
        assert code == 2
        assert "次の操作:" in capsys.readouterr().err
    else:
        assert code == 0
        result, _ = _prepared(capsys)
        assert result["conversation_window"]["displayed_count"] == 1


@pytest.mark.parametrize("conversation_only", [False, True])
def test_large_main_rows_use_finite_window(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, conversation_only: bool
) -> None:
    """旧表示の最小行を超える多数発話でも準備を止めず、省略位置と詳細取得を保持する。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(
        transcript,
        [support.timestamped_entry("2026-11-01T00:00:00Z", f"発話{index}😀") for index in range(1000)],
    )
    work = tmp_path / "work"
    work.mkdir()
    arguments = ["--transcript", str(transcript), "--work-dir", str(work)]
    assert _run([*arguments, "--conversation-only"] if conversation_only else arguments) == 0
    result, document = _prepared(capsys)
    assert result["conversation_limit_bytes"] == 100000
    assert result["conversation_window"]["mode"] == "window"
    assert "--conversation-after" in document
    assert _run([str(transcript), "--detail", "claude:main:1000"], "session-review-evidence") == 0
    assert "発話999😀" in capsys.readouterr().out


def test_many_delegates_and_unresolved_are_bounded(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """多数担当の旧表示下限と未解決通知の増加を、全量保存と有限表示へ接続する。"""
    transcript = tmp_path / "main.jsonl"
    entries = []
    for index in range(20):
        thread = f"11111111-2222-4333-8444-{index:012}"
        entries.extend(
            [
                support.codex_tool_use_entry("2026-11-01T00:00:00Z", str(index), thread),
                support.codex_tool_result_entry("2026-11-01T00:00:01Z", str(index), thread),
            ]
        )
    support.write_jsonl(transcript, entries)
    for index in range(1010):
        support.write_subagent(
            transcript.with_suffix("") / "subagents",
            f"agent-{index:04}",
            [support.assistant_text(f"担当{index}の途中発話" + "継続中" * 30)],
        )
    work = tmp_path / "work"
    work.mkdir()
    assert _run(["--transcript", str(transcript), "--work-dir", str(work), "--conversation-only"]) == 0
    result, document = _prepared(capsys)
    assert result["conversation_window"]["unresolved_count"] >= 20
    assert result["conversation_window"]["has_more"]
    assert "bundle/unconfirmed.jsonl" in document
    assert _run([str(transcript), "--detail", "claude:main/agent-1009:1"], "session-review-evidence") == 0
    assert "担当1009の途中発話" in capsys.readouterr().out


def test_window_exact_fixed_budget_and_saved_jsonl_diagnostic(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """空集合の固定部がちょうど収まる境界と、不正な保存JSONLの物理行診断を確認する。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(transcript, [support.timestamped_entry("2026-11-01T00:00:00Z", "対象外の発話")])
    work = tmp_path / "work"
    work.mkdir()
    base = ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-window"]
    options = ["--conversation-since", "2027-01-01T00:00:00Z"]
    assert _run([*base, *options, "--conversation-max-bytes", "5000"]) == 0
    initial, _ = _prepared(capsys)
    budget = initial["conversation_utf8_bytes"]
    assert 1000 <= budget < 5000
    assert _run([*base, *options, "--conversation-after", "0", "--conversation-max-bytes", str(budget)]) == 0
    result, _ = _prepared(capsys)
    assert result["conversation_utf8_bytes"] == budget
    assert result["conversation_window"]["target_count"] == 0
    assert result["conversation_window"]["unresolved_count"] == 0
    assert _run([*base, *options, "--conversation-after", "0", "--conversation-max-bytes", str(budget - 1)]) == 2
    assert "最小固定部" in capsys.readouterr().err
    path = work / "bundle" / "conversation.jsonl"
    path.write_text(path.read_text(encoding="utf-8") + '{"unfinished":\n', encoding="utf-8")
    assert _run([*base, "--conversation-after", "0"]) == 2
    assert f"{path}:2（物理行）" in capsys.readouterr().err


def test_extraction_stdout_uses_physical_jsonl_lines(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """抽出の通知に合法なUnicode行区切りがあっても保持し、不正JSONは出所付きで診断する。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(transcript, [support.assistant_text("途中経過")])
    work = tmp_path / "work"
    work.mkdir()
    original = evidence.main

    def with_notification(argv: list[str], **_kwargs: Any) -> int:
        code = original(argv)
        print(
            json.dumps(
                {"kind": "unresolved-record", "record": "不明\u2028記録\u2029", "reason": "取得不能"}, ensure_ascii=False
            )
        )
        return code

    monkeypatch.setattr(evidence, "main", with_notification)
    arguments = ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-window"]
    assert _run(arguments) == 0
    result, _ = _prepared(capsys)
    assert result["unconfirmed_scope"][0]["record"] == "不明\u2028記録\u2029"

    def with_broken_json(argv: list[str], **_kwargs: Any) -> int:
        code = original(argv)
        print('{"unfinished":')
        return code

    monkeypatch.setattr(evidence, "main", with_broken_json)
    assert _run(arguments) == 2
    diagnostic = capsys.readouterr().err
    assert str(work / "bundle") in diagnostic and "抽出標準出力" in diagnostic and "（物理行）" in diagnostic


def test_window_scope_after_remains_separate_from_display_cursor(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """scope JSONによる再抽出と、保存bundleの表示位置による継続を別々に適用する。"""
    transcript = tmp_path / "main.jsonl"
    first = support.timestamped_entry("2026-11-01T00:00:00Z", "処理済みの発話")
    support.write_jsonl(transcript, [first])
    initial = tmp_path / "initial"
    initial.mkdir()
    assert _run(["--transcript", str(transcript), "--work-dir", str(initial), "--conversation-only"]) == 0
    _prepared(capsys)
    support.write_jsonl(transcript, [first, support.timestamped_entry("2026-11-01T00:01:00Z", "追加された発話")])
    work = tmp_path / "incremental"
    work.mkdir()
    arguments = [
        "--transcript",
        str(transcript),
        "--work-dir",
        str(work),
        "--conversation-window",
        "--after",
        str(initial / "bundle" / "scope.json"),
    ]
    assert _run(arguments) == 0
    result, document = _prepared(capsys)
    assert "処理済みの発話" not in document and "追加された発話" in document
    assert result["conversation_window"]["target_count"] == 1
    assert _run([*arguments, "--conversation-after", "1"]) == 0
    result, _ = _prepared(capsys)
    assert result["conversation_window"]["target_count"] == 0
    assert _run([*arguments, "--conversation-after", "2"]) == 2
    assert "範囲外" in capsys.readouterr().err


def test_recent_uses_latest_known_time_across_records(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """新しいメインと古い子から直近を選び、未知時刻の除外件数と通常表示を区別する。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(
        transcript,
        [
            support.timestamped_entry("2026-11-05T11:00:00Z", "新しいメイン発話"),
            support.assistant_text("メインの未知時刻"),
        ],
    )
    support.write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            support.timestamped_entry("2026-11-04T11:00:00Z", "古い子発話"),
            support.assistant_text("子の未知時刻"),
        ],
    )
    support.write_subagent(
        transcript.with_suffix("") / "subagents", "agent-unknown", [support.assistant_text("時刻が分からない担当")]
    )
    work = tmp_path / "work"
    work.mkdir()
    base = ["--transcript", str(transcript), "--work-dir", str(work), "--conversation-window"]
    assert _run([*base, "--conversation-recent", "1"]) == 0
    result, document = _prepared(capsys)
    assert "新しいメイン発話" in document and "古い子発話" not in document
    assert result["conversation_window"]["displayed_count"] == 1
    assert result["conversation_window"]["unknown_timestamp_count"] == 3
    assert result["conversation_window"]["unknown_timestamp_excluded_count"] == 3
    only_unknown = [*base, "--conversation-after", "0", "--conversation-record", "claude:main/agent-unknown"]
    assert _run([*only_unknown, "--conversation-recent", "1"]) == 0
    result, document = _prepared(capsys)
    assert result["conversation_window"]["displayed_count"] == 0
    assert result["conversation_window"]["unknown_timestamp_count"] == 1
    assert result["conversation_window"]["unknown_timestamp_excluded_count"] == 1
    assert "未知時刻: 1件" in document
    assert _run(only_unknown) == 0
    result, document = _prepared(capsys)
    assert result["conversation_window"]["displayed_count"] == 1
    assert result["conversation_window"]["unknown_timestamp_excluded_count"] == 0
    assert "時刻が分からない担当" in document and "時系列順未確定" in document


def test_time_order_and_continuation_interleave_records_without_gaps(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """担当の交互時刻・同時刻・未知時刻を安定した位置へ対応付け、保存bundleを全ページ取得する。"""
    transcript = tmp_path / "main.jsonl"
    support.write_jsonl(
        transcript,
        [
            support.timestamped_entry(f"2026-11-05T11:0{minute}:00Z", f"main-{index}" + "本文" * 1000)
            for index, minute in enumerate([0, 2, 4], 1)
        ]
        + [support.assistant_text("unknown-1" + "本文" * 1000)],
    )
    support.write_subagent(
        transcript.with_suffix("") / "subagents",
        "agent-child",
        [
            support.timestamped_entry(f"2026-11-05T11:0{minute}:00Z", f"child-{index}" + "本文" * 1000)
            for index, minute in enumerate([1, 3, 4], 1)
        ]
        + [support.assistant_text("unknown-2" + "本文" * 1000)],
    )
    work = tmp_path / "work"
    work.mkdir()
    base = [
        "--transcript",
        str(transcript),
        "--work-dir",
        str(work),
        "--conversation-window",
        "--conversation-max-bytes",
        "4500",
    ]
    arguments = base
    observed_at = None
    found: list[tuple[int, str]] = []
    for _ in range(8):
        assert _run(arguments) == 0
        result, document = _prepared(capsys)
        window = result["conversation_window"]
        if observed_at is None:
            observed_at = window["observed_at"]
            assert window["has_more"]
        assert window["observed_at"] == observed_at
        rows = re.findall(r"^- \[(\d+)\].*: ((?:main|child|unknown)-\d+)", document, re.MULTILINE)
        assert rows
        found.extend((int(position), marker) for position, marker in rows)
        if not window["has_more"]:
            break
        arguments = [*base, "--conversation-after", str(window["next_after"])]
    assert found == list(
        enumerate(
            [
                "main-1",
                "child-1",
                "main-2",
                "child-2",
                "main-3",
                "child-3",
                "unknown-1",
                "unknown-2",
            ],
            1,
        )
    )
