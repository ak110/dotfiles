"""Claude Code 2.1.301で観測したAPI要求の写しと読取不能記録の境界を検証する。"""

import collections
import json
import pathlib
from typing import Any

import pytest
import session_evidence_catalog as catalog
import session_review_evidence as evidence
import session_review_prepare as prepare

from agent_toolkit._testing.helpers import _write_transcript
from agent_toolkit._testing.session_evidence_support import read_jsonl


@pytest.mark.parametrize(
    "query",
    [
        [],
        ["--tool-calls"],
        ["--warn"],
        ["--stats"],
        ["--stats", "--summary-only"],
        ["--user-events"],
        ["--hook-notices"],
        ["--context-at", "3", "--phrase=warning"],
        ["--detail", "1"],
        ["--record-schema", "1"],
        ["--elapsed-until", "2026-10-01T00:00:03Z"],
    ],
)
def test_request_copies_do_not_create_conversation_events(tmp_path: pathlib.Path, capsys, query) -> None:
    """会話と同じmessageを持つ別種のエントリを追加しても抽出結果を増やさない。"""
    entries = [
        {
            "type": "assistant",
            "timestamp": "2026-10-01T00:00:00Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "false"}}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-01T00:00:01Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call", "content": "warning: failure", "is_error": True}],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-10-01T00:00:02Z",
            "message": {"role": "user", "content": "<task-notification><status>completed</status></task-notification>"},
        },
    ]
    transcript = _write_transcript(tmp_path, entries)
    assert evidence.main([str(transcript), *query]) == 0
    expected = read_jsonl(capsys, raw=True)
    copies = [
        {"type": kind, "message": entry["message"]} for kind in ("api-request-blob", "future-request-copy") for entry in entries
    ]
    _write_transcript(tmp_path, [*entries, *copies])
    assert evidence.main([str(transcript), *query]) == 0
    assert read_jsonl(capsys, raw=True) == expected
    assert evidence.main([str(transcript), "--detail", "4"]) == 0
    assert "false" in json.dumps(read_jsonl(capsys))
    assert evidence.main([str(transcript), "--grep", "false"]) == 0
    assert any(event.get("line") == 4 for event in read_jsonl(capsys))
    assert evidence.main([str(transcript), "--fixed-string", "false"]) == 0
    matches = read_jsonl(capsys)
    # 固定文字列はgrepと同じ生の本文の照会であり、WIが保持を求める写しの所在も返す。
    assert any(locator["line"] == 4 for event in matches for locator in event.get("locators", []))


@pytest.mark.parametrize("content", [b"\xff", b"not json", None])
def test_unreadable_main_record_is_query_error(tmp_path: pathlib.Path, capsys, content) -> None:
    """読取不能なJSONLを原因と次の操作が分かる終了2へ変換する。"""
    transcript = tmp_path / "broken.jsonl"
    if content is None:
        transcript.mkdir()
    else:
        transcript.write_bytes(content)
    assert evidence.main([str(transcript)]) == 2
    (event,) = read_jsonl(capsys, raw=True)
    assert str(transcript) in event["text"]
    assert "UTF-8" in event["next_action"]


@pytest.mark.parametrize("broken", [b"\xff", b"not-json", None])
@pytest.mark.parametrize("query", [[], ["--tool-calls"], ["--user-events"]])
@pytest.mark.parametrize("runtime", ["claude", "codex"])
def test_catalog_retains_readable_records_and_unconfirmed_scope(tmp_path: pathlib.Path, capsys, broken, query, runtime) -> None:
    """カタログの3照会がUTF-8・JSON・I/O失敗を正常な兄弟から分離する。"""
    transcript = _write_transcript(
        tmp_path,
        [
            {
                "type": "assistant",
                "timestamp": "2026-10-01T00:00:01Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "ok", "name": "Read", "input": {"file_path": "/ok"}}],
                },
            }
        ],
    )
    if runtime == "codex":
        transcript.rename(tmp_path / "rollout-ok.jsonl")
        (tmp_path / "rollout-ok.jsonl").write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "timestamp": "2026-10-01T00:00:01Z",
                    "payload": {"type": "function_call", "name": "exec_command", "call_id": "ok", "arguments": '{"cmd":"ls"}'},
                }
            ),
            encoding="utf-8",
        )
    path = tmp_path / ("rollout-broken.jsonl" if runtime == "codex" else "broken.jsonl")
    if broken is None:
        path.mkdir()
    else:
        path.write_bytes(broken)
    assert (
        evidence.main(
            [
                "--catalog-codex-history" if runtime == "codex" else "--catalog-claude-project",
                str(tmp_path),
                "--since",
                "2026-10-01T00:00:00Z",
                "--observation-boundary",
                "2026-10-01T00:00:02Z",
                *query,
            ]
        )
        == 0
    )
    events = read_jsonl(capsys, raw=True)
    expected_kind = "tool-call" if query == ["--tool-calls"] else "summary" if query else "catalog-parent"
    assert any(event["kind"] == expected_kind for event in events)
    assert any(event["kind"] == "unresolved-record" and event["record"] == str(path) for event in events)
    assert events[-1]["unresolved_record_count"] == 1


@pytest.mark.parametrize("query", [[], ["--tool-calls"], ["--user-events"]])
def test_catalog_second_pass_read_failure_is_actionable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], query: list[str]
) -> None:
    """索引作成後の再読込不能を公開照会の終了2へ変換し、途中結果を成功として返さない。"""
    transcript = _write_transcript(
        tmp_path,
        [{"type": "user", "timestamp": "2026-10-01T00:00:01Z", "message": {"role": "user", "content": "依頼"}}],
    )

    def unavailable(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(catalog, "_load_records", unavailable)
    assert (
        evidence.main(
            [
                "--catalog-claude-project",
                str(tmp_path),
                "--since",
                "2026-10-01T00:00:00Z",
                "--observation-boundary",
                "2026-10-01T00:00:02Z",
                *query,
            ]
        )
        == 2
    )
    (event,) = read_jsonl(capsys, raw=True)
    assert event["kind"] == "error" and str(transcript) in event["text"]
    assert event["next_action"]


@pytest.mark.parametrize("broken", [b"\xff", b"not-json", None])
def test_subagent_read_failure_keeps_normal_sibling(tmp_path: pathlib.Path, capsys, broken) -> None:
    """付随記録の読取失敗を未解決へ渡し、正常な記録の操作を収集する。"""
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    directory = transcript.with_suffix("") / "subagents"
    directory.mkdir(parents=True)
    good = directory / "agent-good.jsonl"
    good.write_text(
        json.dumps(
            {
                "type": "assistant",
                "isSidechain": True,
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "ok", "name": "Read", "input": {"file_path": "/ok"}}],
                },
            }
        ),
        encoding="utf-8",
    )
    bad = directory / "agent-bad.jsonl"
    if broken is None:
        bad.mkdir()
    else:
        bad.write_bytes(broken)
    assert evidence.main([str(transcript), "--tool-calls"]) == 0
    events = read_jsonl(capsys, raw=True)
    assert any(event.get("call_id") == "ok" for event in events)
    assert any(event["kind"] == "unresolved-record" and "agent-bad" in event["record"] for event in events)


def test_prepare_bundle_does_not_read_request_copies_as_conversation(tmp_path: pathlib.Path, capsys) -> None:
    """準備文書とbundleの位置が写しを追加する前と同じ実エントリを指す。"""
    entries = [
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "call", "name": "Bash", "input": {"command": "false"}}],
            },
        },
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "call", "content": "failure", "is_error": True}],
            },
        },
    ]
    transcript = _write_transcript(tmp_path, entries)
    documents = []
    for index in range(2):
        if index:
            _write_transcript(
                tmp_path, [*entries, *[{"type": "api-request-blob", "message": entry["message"]} for entry in entries]]
            )
        work = tmp_path / f"work-{index}"
        work.mkdir()
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)]) == 0
        record = json.loads(capsys.readouterr().out)
        documents.append(pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8"))
        for name in ("timeline", "warnings", "comparison-materials", "conversation", "candidate-evidence"):
            events = [json.loads(line) for line in (work / "bundle" / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()]
            positions = []
            pending = list(events)
            while pending:
                value = pending.pop()
                if isinstance(value, dict):
                    if isinstance(value.get("line"), int) and value["line"] > 0:
                        positions.append(value["line"])
                    pending.extend(value.values())
                elif isinstance(value, list):
                    pending.extend(value)
            assert all(line <= 2 for line in positions)
            types = collections.Counter(entries[line - 1]["type"] for line in positions)
            assert not types.keys() - {"user", "assistant"}
            if name == "candidate-evidence":
                assert positions
    assert documents[0] == documents[1]
