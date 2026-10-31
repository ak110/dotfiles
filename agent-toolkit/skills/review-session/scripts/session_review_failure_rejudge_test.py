"""失敗署名台帳が抽出と同じhook分類を再適用する契約を検証する。"""

import datetime
import json
import pathlib

import pytest
import session_evidence_candidates as candidates
import session_review_prepare as prepare

from agent_toolkit._common import state_paths
from agent_toolkit._testing.helpers import _write_transcript


@pytest.mark.parametrize(
    "event",
    [
        {
            "tool": "call",
            "text": 'PreToolUse:Bash hook error: [hook]: <atk-auto source="pretooluse" kind="block">理由</atk-auto>',
        },
        {
            "tool": "call",
            "text": "PreToolUse:Bash hook error: [hook]: 理由",
            "hook_notices": [{"text": "理由", "hook_name": "PreToolUse:Bash"}],
        },
    ],
)
def test_prepare_removes_hook_classified_failure_records(tmp_path: pathlib.Path, capsys, event) -> None:
    """旧分類で保存した失敗を、反復件数と書き戻す台帳の双方から除く。"""
    now = datetime.datetime(2026, 10, 18, tzinfo=datetime.UTC)
    ledger = state_paths.state_dir() / "session-review" / "failure-signatures.jsonl"
    ledger.parent.mkdir(parents=True)
    record = {
        "failure_signature": "old",
        "failure_summary": "旧分類",
        "session_id": "previous",
        "observed_at": now.isoformat(),
        "candidate_kind": "tool-failure",
        "failure_event": event,
    }
    ledger.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=now) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["failure_ledger_skipped"] == 0
    assert not ledger.read_text(encoding="utf-8")


def test_failure_extraction_and_ledger_rejudging_agree(tmp_path: pathlib.Path, capsys) -> None:
    """同じ入力集合で、抽出と再判定が失敗として残す記録位置を比較する。"""
    notice = {
        "kind": "hook-notice",
        "record": "claude:source",
        "line": 3,
        "hook": "pretooluse",
        "tag": "block",
        "hook_name": "PreToolUse:Bash",
        "text": "理由",
    }
    events = [
        {
            "kind": "failed-tool",
            "record": "claude:source",
            "line": 1,
            "tool": "normal",
            "text": "通常の失敗",
        },
        {
            "kind": "failed-tool",
            "record": "claude:source",
            "line": 2,
            "tool": "converted",
            "text": 'PreToolUse:Bash hook error: [hook]: <atk-auto source="pretooluse" kind="block">理由</atk-auto>',
        },
        {
            "kind": "failed-tool",
            "record": "claude:source",
            "line": 3,
            "tool": "represented",
            "text": "PreToolUse:Bash hook error: [hook]: 理由",
        },
        {
            "kind": "failed-tool",
            "record": "claude:source",
            "line": 4,
            "tool": "negative",
            "tool_name": "Bash",
            "operation": json.dumps({"command": "atk wi grep -F -- none"}),
            "exit_code": 1,
            "text": "該当0件: none",
        },
    ]
    extracted = candidates._candidate_events(events, [], [notice])  # pylint: disable=protected-access
    expected = {
        row["failure_event"]["line"] for row in extracted if row.get("candidate_kind") in candidates.FAILURE_CANDIDATE_KINDS
    }
    assert expected == {1}
    now = datetime.datetime(2026, 10, 18, tzinfo=datetime.UTC)
    ledger = state_paths.state_dir() / "session-review" / "failure-signatures.jsonl"
    ledger.parent.mkdir(parents=True)
    records = []
    for event in events:
        saved: dict[str, object] = {field: event[field] for field in candidates.FAILURE_EVENT_FIELDS if field in event}
        saved["hook_notices"] = [notice] if event["line"] == 3 else []
        records.append(
            {
                "failure_signature": f"old-{event['line']}",
                "failure_summary": "旧分類",
                "session_id": "previous",
                "observed_at": now.isoformat(),
                "candidate_kind": "tool-failure",
                "failure_event": saved,
            }
        )
    ledger.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records), encoding="utf-8")
    transcript = _write_transcript(tmp_path, [{"type": "user", "message": {"role": "user", "content": "依頼"}}])
    work = tmp_path / "work"
    work.mkdir()
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=now) == 0
    assert json.loads(capsys.readouterr().out)["failure_ledger_skipped"] == 0
    counted = {json.loads(line)["failure_event"]["line"] for line in ledger.read_text(encoding="utf-8").splitlines()}
    assert counted == expected
