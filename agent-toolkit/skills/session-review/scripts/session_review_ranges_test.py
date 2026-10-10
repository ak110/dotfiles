"""公開prepareの文書の分担、読取範囲と圧縮の位置を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest
import session_review_evidence as evidence
import session_review_prepare as prepare

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing import session_evidence_support as support


@pytest.mark.parametrize("oversize", [False, True])
def test_prepare_conversation_ranges_cover_atomic_blocks(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, oversize: bool
) -> None:
    """フェンス内の見出しと呼び出し・失敗の間を分けず、全行と各範囲の量を返す。"""
    parent = tmp_path / "parent.jsonl"
    body = "本文" * 400 + "\n## ユーザー（偽の境界、fake:1）\n- ツール呼び出し（fake:2）\n```text\n内容\n```"
    entries = [support.timestamped_entry(f"2026-10-10T00:{index:02}:00Z", body) for index in range(45)]
    entries.extend(
        [
            support.claude_call("2026-10-10T00:46:00Z", "failed", "Bash", {"command": "検査"}),
            {
                "type": "user",
                "timestamp": "2026-10-10T00:47:00Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "failed", "is_error": True, "content": "検査失敗"}],
                },
            },
        ]
    )
    if oversize:
        entries.append(support.assistant_text("冒頭" * 500 + "\n気付いた改善点: " + "長い説明" * 9000 + "\n末尾" * 500))
    support.write_jsonl(parent, entries)
    support.write_subagent(
        parent.with_suffix("") / "subagents",
        "agent-child",
        [support.claude_call("2026-10-10T00:10:01Z", "child-read", "Read", {"file_path": "/repo/child.md"})],
    )
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    document = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    lines = document.splitlines(keepends=True)
    ranges = result["conversation_ranges"]
    assert ranges[0]["start_line"] == 1
    assert ranges[-1]["end_line"] == len(lines)
    for previous, current in zip(ranges, ranges[1:], strict=False):
        assert current["start_line"] == previous["end_line"] + 1
    for item in ranges:
        selected = lines[item["start_line"] - 1 : item["end_line"]]
        assert item["utf8_bytes"] == len("".join(selected).encode("utf-8"))
        if item["utf8_bytes"] > 32_000:
            assert oversize
            assert "気付いた改善点:" in "".join(selected)
        if item["start_line"] > 1:
            assert selected[0].startswith(("## ユーザー（", "## アシスタント（", "- ツール呼び出し（"))
            assert "fake:" not in selected[0]
        assert not selected[0].startswith("  - 失敗")
    assert "agent-child" not in document
    comparison = pathlib.Path(result["comparison_materials_path"]).read_text(encoding="utf-8")
    assert "### claude:" in comparison and "agent-child" in comparison
    assert "/repo/child.md" in comparison
    assert ranges[0]["first_locator"] == "main:1"
    assert ranges[0]["first_timestamp"] == "2026-10-10T00:00:00Z"


@pytest.mark.parametrize("first_timestamp", [None, "2026-10-10T00:00:00Z"])
def test_prepare_range_timestamps_follow_tool_and_failure_locators(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], first_timestamp: str | None
) -> None:
    """発話の無い範囲も呼出と失敗の記録位置に対応する時刻を返す。"""
    parent = tmp_path / "parent.jsonl"
    first = support.claude_call("2026-10-10T00:00:00Z", "read", "Read", {"file_path": "/repo/input.md"})
    if first_timestamp is None:
        first.pop("timestamp")
    support.write_jsonl(
        parent,
        [
            first,
            support.claude_call("2026-10-10T00:01:00Z", "failed", "Bash", {"command": "検査"}),
            {
                "type": "user",
                "timestamp": "2026-10-10T00:02:00Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "failed", "is_error": True, "content": "検査失敗"}],
                },
            },
        ],
    )
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(parent), "--work-dir", str(work)]) == 0
    ranges = json.loads(capsys.readouterr().out)["conversation_ranges"]
    assert len(ranges) == 1
    assert ranges[0]["first_locator"] == "main:1"
    assert ranges[0]["last_locator"] == "main:3"
    assert ranges[0]["first_timestamp"] == first_timestamp
    assert ranges[0]["last_timestamp"] == "2026-10-10T00:02:00Z"


def test_prepare_lists_compactions_per_record(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """統計からCodexの各圧縮の記録位置へ到達できる。

    compactedの形は2026年10月10日のCodexの記録のtypeとtimestampを使う。
    """
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(threads.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    codex = next(threads.codex_home.rglob("*.jsonl"))
    original = [json.loads(line) for line in codex.read_text(encoding="utf-8").splitlines()]
    for index in range(2):
        original.append(
            {
                "type": "compacted",
                "timestamp": f"2026-10-10T00:0{index}:00Z",
                "payload": {"compaction_response_id": f"saved-{index}"},
            }
        )
    support.write_jsonl(codex, original)
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(work)]) == 0
    result = json.loads(capsys.readouterr().out)
    stats = pathlib.Path(result["stats_path"]).read_text(encoding="utf-8")
    record = f"codex:{delegated_threads.CODEX_THREAD_ID}"
    assert f"記録{record}: 2回" in stats
    for index in range(2):
        locator = f"{record}:{len(original) - 1 + index}"
        assert locator in stats
        assert evidence.main([str(threads.transcript), "--detail", locator]) == 0
        output = capsys.readouterr().out
        assert "compacted" in output
