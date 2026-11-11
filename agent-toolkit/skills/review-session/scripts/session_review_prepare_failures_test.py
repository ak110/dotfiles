# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""セッション振り返りの試験（session_review_prepare_failures_test）。"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import subprocess
import sys

import pytest
import session_evidence_candidates as evidence_candidates
import session_evidence_execution as execution
import session_review_prepare as prepare

from agent_toolkit._testing import session_evidence_support as support
from agent_toolkit._testing.failure_records import bash_failure_entries, failure_entries
from agent_toolkit._testing.session_review_prepare_support import _FIXED_NOW, _work_dir
from agent_toolkit._testing.session_review_prepare_support import _isolate_failure_ledger as _isolate_failure_ledger


def test_prepare_reuses_syntax_within_one_run_without_carryover(tmp_path, capsys, monkeypatch) -> None:
    """別位置の証拠を保って同じ準備の中で構文を共有し、次の準備では解析し直す。"""
    parsed: list[str] = []
    original = execution._parse_shell_simple_commands  # pylint: disable=protected-access

    def counted(command, **kwargs):
        parsed.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(execution, "_parse_shell_simple_commands", counted)
    command = "cat /repo/role.subagent.md; sed -n -e1p /cache/atk-output-example/output.txt"
    for number in range(2):
        transcript = tmp_path / f"session-{number}.jsonl"
        support.write_jsonl(
            transcript,
            [
                support.timestamped_entry("2026-09-06T12:00:00Z", "依頼"),
                support.claude_call("2026-09-06T12:00:01Z", "a", "Bash", {"command": command}),
                support.claude_call("2026-09-06T12:00:02Z", "b", "Bash", {"command": command}),
                support.claude_call("2026-09-06T12:00:03Z", "read", "Read", {"file_path": "/repo/read-only.md"}),
            ],
        )
        work = tmp_path / f"prepared-{number}"
        work.mkdir()
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
        capsys.readouterr()
        candidates = [json.loads(line) for line in (work / "bundle/candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        processing = next(row for row in candidates if row.get("candidate_kind") == "adhoc-processing")
        assert [row["line"] for row in processing["calls"]] == [2, 3]
        assert processing["count"] == 2
        assert parsed.count(command) == number + 1
        assert "/repo/read-only.md" not in parsed


def test_prepare_rejudges_env_representative_events(tmp_path, capsys) -> None:
    """誤統合された旧署名を残さず、代表イベントの既知・未知env入力を再分類する。"""
    ledger = tmp_path / "state/session-review/failure-signatures.jsonl"
    ledger.parent.mkdir(parents=True)
    commands = ["env -u NAME git status", "env -u NAME pytest", "env -S git status", "env -S pytest"]
    records = [
        {
            "candidate_kind": "tool-failure",
            "session_id": f"past-{number}",
            "observed_at": "2026-09-05T00:00:00+00:00",
            "failure_signature": json.dumps(["tool-failure", "-u", "NAME", 2, "同じ診断"]),
            "failure_summary": "旧署名",
            "failure_event": {
                "tool": "Bash",
                "exit_code": 2,
                "text": "同じ診断",
                "operation": json.dumps({"command": command}),
            },
        }
        for number, command in enumerate(commands)
    ]
    ledger.write_text("".join(json.dumps(row) + "\n" for row in records), encoding="utf-8")
    transcript = tmp_path / "current.jsonl"
    support.write_jsonl(transcript, [support.timestamped_entry("2026-09-06T12:00:00Z", "依頼")])
    work = _work_dir(tmp_path)
    for _ in range(2):
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["failure_ledger_skipped"] == 0
        saved = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert len(saved) == 4
        assert len({row["failure_signature"] for row in saved}) == 4
        assert {row["session_id"] for row in saved} == {row["session_id"] for row in records}
        assert {row["observed_at"] for row in saved} == {"2026-09-05T00:00:00+00:00"}


@pytest.mark.parametrize("failure_kind", ["command-failure", "tool-failure"])
def test_failure_variable_values_share_signature(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], failure_kind: str
) -> None:
    """別セッションの新規失敗でパス・UUID・記録IDだけを変え、反復をセッション単位で数える。"""
    signatures = set()
    ledger = tmp_path / "state" / "session-review" / "failure-signatures.jsonl"
    for index in range(3):
        diagnostic = (
            f"対象 /records/session-{index}/input.jsonl がない。"
            f"uuid=11111111-2222-4333-8444-{index:012d}、記録=claude:session-{index}:7"
        )
        entries: list[dict] = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
        entries.extend(failure_entries(failure_kind, diagnostic, "failed"))
        transcript = tmp_path / f"session-{index}.jsonl"
        transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
        work = tmp_path / f"prepared-{index}"
        work.mkdir()
        for _ in range(2):
            assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
            result = json.loads(capsys.readouterr().out)
            bundle = [
                json.loads(line) for line in (work / "bundle" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()
            ]
            failures = [row for row in bundle if row.get("candidate_kind") == failure_kind]
            assert len(failures) == 1
            signatures.add(failures[0]["failure_signature"])
            assert len(signatures) == 1
            saved = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
            assert len(saved) == index + 1
            assert {row["session_id"] for row in saved} == {f"session-{number}" for number in range(index + 1)}
            assert {row["failure_signature"] for row in saved} == signatures
            assert {row["candidate_kind"] for row in saved} == {failure_kind}
            current = next(row for row in saved if row["session_id"] == f"session-{index}")
            assert current["failure_event"] == failures[0]["failure_event"]
            if index == 0:
                assert result["candidate_total"] == 0
                assert result["excluded_counts"]["single-session-failure"] == 1
            else:
                assert result["candidate_counts"] == {failure_kind: 1}
                document = pathlib.Path(result["candidates_path"]).read_text(encoding="utf-8")
                assert f"{index + 1}セッション" in document


def test_prepare_recomputes_legacy_failure_ledger(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """旧形式の署名で集約された4セッションの記録を、保存済みの代表診断の理由で1・2・1件の署名へ分け直し、再準備でも記録を重複させず観測時刻を保つ。"""
    ledger = tmp_path / "state" / "session-review" / "failure-signatures.jsonl"
    ledger.parent.mkdir(parents=True)
    diagnostics = [
        '{"error":"併用拒否"}',
        '{"error":"記録ID不明","record":"claude:first"}',
        '{"error":"記録ID不明","record":"claude:second"}',
        "記録ファイル不在: /records/missing.jsonl",
    ]
    records = [
        {
            "failure_signature": json.dumps([kind, "atk", "session-review-evidence", 2, "{<arg>:<arg>}"], ensure_ascii=False),
            "session_id": f"{kind}-{index}",
            "observed_at": "2026-09-05T00:00:00+00:00",
            "failure_summary": f"atk run-script session-review-evidence（終了コード2）: {diagnostic}",
        }
        for kind in ("command-failure", "tool-failure")
        for index, diagnostic in enumerate(diagnostics)
    ]
    ledger.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")
    transcript = tmp_path / "no-failure.jsonl"
    transcript.write_text(json.dumps({"type": "user", "message": {"role": "user", "content": "依頼"}}) + "\n", encoding="utf-8")
    work = _work_dir(tmp_path)
    for _ in range(2):
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["failure_ledger_skipped"] == 0
        saved = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        counts: dict[str, int] = {}
        for item in saved:
            counts[item["failure_signature"]] = counts.get(item["failure_signature"], 0) + 1
        assert sorted(counts.values()) == [1, 1, 1, 1, 2, 2]
        assert {item["session_id"] for item in saved} == {item["session_id"] for item in records}
        assert {item["observed_at"] for item in saved} == {"2026-09-05T00:00:00+00:00"}


def test_failure_ledger_rejudges_records_with_current_exclusions(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """台帳の記録へ抽出と同じ除外判定を読込時に適用し、現在の判定が除く記録を反復に数えず台帳からも除く。

    除外規則を後から広げても、判定の結論だけを保持する台帳では旧規則で記録した失敗が30日の期限まで反復に数えられる。
    旧規則で記録された状態を、判定に使う欄を持つ記録として台帳へ置いて再現する。
    """
    no_match = "Exit code 1\n該当0件: 条件に一致するWIは無い"
    excluded_session = tmp_path / "excluded-session.jsonl"
    entries: list[dict] = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
    entries.extend(bash_failure_entries("atk wi grep 台帳", no_match, "single"))
    entries.extend(bash_failure_entries("cd /repo; atk wi grep 台帳", no_match, "cd-prefixed"))
    entries.extend(bash_failure_entries("atk agents wait", "Exit code 3\n待機中: 1件", "wait"))
    entries.extend(bash_failure_entries("rg needle docs 2>/dev/null", "Exit code 1", "read-predicate"))
    excluded_session.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    excluded_dir = tmp_path / "excluded-work"
    excluded_dir.mkdir()
    assert prepare.main(["--transcript", str(excluded_session), "--work-dir", str(excluded_dir)], now=_FIXED_NOW) == 0
    assert json.loads(capsys.readouterr().out)["candidate_total"] == 0
    timeline = [
        json.loads(line) for line in (excluded_dir / "bundle" / "timeline.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    failures = [event for event in timeline if event.get("kind") == "failed-tool"]
    assert len(failures) == 4
    ledger = tmp_path / "state" / "session-review" / "failure-signatures.jsonl"
    excluded_records = []
    for event in failures:
        failure_event = {field: event[field] for field in evidence_candidates.FAILURE_EVENT_FIELDS if field in event}
        signature, summary = evidence_candidates.failure_signature("tool-failure", failure_event)
        excluded_records.append(
            {
                "failure_signature": signature,
                "session_id": "excluded-session",
                "observed_at": "2026-09-05T00:00:00+00:00",
                "failure_summary": summary,
                "candidate_kind": "tool-failure",
                "failure_event": failure_event,
            }
        )
    negative = excluded_records[-1]
    excluded_records.extend({**negative, "session_id": f"negative-{index}"} for index in range(17))
    ledger.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in excluded_records), encoding="utf-8")

    chained = "atk wi grep 台帳; atk wi grep 失敗署名"
    for index, session in enumerate(("chained-a", "chained-b")):
        transcript = tmp_path / f"{session}.jsonl"
        entries = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
        entries.extend(bash_failure_entries(chained, no_match, "chained"))
        transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
        work = tmp_path / f"{session}-work"
        work.mkdir()
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
        result = json.loads(capsys.readouterr().out)
        bundle = [json.loads(line) for line in (work / "bundle" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        chained_failures = [row for row in bundle if row.get("candidate_kind") == "tool-failure"]
        assert len(chained_failures) == 1
        # 除かれる単独の`atk wi grep`の記録は、連結した失敗と同じ署名を持つ。
        assert chained_failures[0]["failure_signature"] == excluded_records[0]["failure_signature"]
        if index == 0:
            assert result["candidate_total"] == 0
            assert result["excluded_counts"]["single-session-failure"] == 1
        else:
            assert result["candidate_counts"] == {"tool-failure": 1}
            assert "2セッション" in pathlib.Path(result["candidates_path"]).read_text(encoding="utf-8")
        saved = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        assert {item["session_id"] for item in saved} == {f"chained-{name}" for name in "ab"[: index + 1]}
        assert all(item["failure_event"] and item["candidate_kind"] == "tool-failure" for item in saved)


def _write_failed_codex_transcript(tmp_path: pathlib.Path, session_id: str) -> pathlib.Path:
    """同じ原因のCommandExecution失敗を持つ最上位セッション記録を作成する。"""
    path = tmp_path / f"{session_id}.jsonl"
    entries = [
        {
            "timestamp": "2026-09-06T12:00:00Z",
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
        },
        {
            "timestamp": "2026-09-06T12:00:01Z",
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CommandExecution",
                    "status": "failed",
                    "command": ["/bin/bash", "-lc", "timeout 900s uv run --frozen python a.py"],
                    "exit_code": 127,
                    "stderr": "python: command not found",
                },
            },
        },
    ]
    path.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return path


def test_prepare_promotes_failure_only_after_another_session(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """単発は件数表へ置き、別セッションの反復だけ候補へ上げ、同じセッションの再実行は重ねない。"""
    first = _write_failed_codex_transcript(tmp_path, "session-a")
    second = _write_failed_codex_transcript(tmp_path, "session-b")
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()

    assert prepare.main(["--transcript", str(first), "--work-dir", str(first_dir)], now=_FIXED_NOW) == 0
    first_result = json.loads(capsys.readouterr().out)
    assert first_result["candidate_total"] == 0
    assert first_result["excluded_counts"]["single-session-failure"] == 1
    assert "単発の失敗" in (first_dir / "candidates.md").read_text(encoding="utf-8")

    assert prepare.main(["--transcript", str(second), "--work-dir", str(second_dir)], now=_FIXED_NOW) == 0
    second_result = json.loads(capsys.readouterr().out)
    assert second_result["candidate_counts"] == {"command-failure": 1}
    assert "2セッション" in (second_dir / "candidates.md").read_text(encoding="utf-8")

    assert prepare.main(["--transcript", str(second), "--work-dir", str(second_dir)], now=_FIXED_NOW) == 0
    assert json.loads(capsys.readouterr().out)["candidate_total"] == 1
    ledger = (tmp_path / "state" / "session-review" / "failure-signatures.jsonl").read_text(encoding="utf-8")
    assert len(ledger.splitlines()) == 2


def test_prepare_counts_wait_continuations_without_repeated_failure_signature(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """待機継続は別セッションで反復しても署名へ集計せず、実際の待機失敗だけを候補にする。"""
    for session in ("wait-session-a", "wait-session-b"):
        entries = [
            {
                "timestamp": "2026-09-06T12:00:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "依頼"}],
                },
            },
        ]
        for command, code in [
            (["atk", "agents", "wait"], 3),
            (["bash", "-lc", "/repo/agent-toolkit/bin/atk agents wait"], 3),
            (["atk", "agents", "wait"], 2),
            (["rg", "missing", "docs"], 1),
        ]:
            entries.append(
                {
                    "timestamp": "2026-09-06T12:00:01Z",
                    "type": "event_msg",
                    "payload": {
                        "type": "item_completed",
                        "item": {
                            "type": "CommandExecution",
                            "status": "failed",
                            "command": command,
                            "exit_code": code,
                            "stderr": "",
                        },
                    },
                }
            )
        transcript = tmp_path / f"{session}.jsonl"
        transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
        work = tmp_path / session
        work.mkdir()
        assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["excluded_counts"]["normal-nonterminal-result"] == 2
        assert result["excluded_counts"]["normal-negative-result"] == 1
        document = pathlib.Path(result["candidates_path"]).read_text(encoding="utf-8")
        assert "normal-nonterminal-result 2件" in document
        bundle = [json.loads(line) for line in (work / "bundle" / "candidates.jsonl").read_text(encoding="utf-8").splitlines()]
        assert bundle[-1]["excluded"]["normal-nonterminal-result"] == 2
        assert [item["locators"] for item in bundle if item["kind"] == "candidate"] == [
            [{"record": f"codex:{session}", "line": 4}]
        ]
    assert result["candidate_counts"] == {"command-failure": 1}
    ledger = (tmp_path / "state" / "session-review" / "failure-signatures.jsonl").read_text(encoding="utf-8")
    assert len(ledger.splitlines()) == 2


def test_prepare_prunes_old_and_skips_invalid_failure_records(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """30日より古い観測と破損行は反復に使わず、無視した行数を公開結果へ示す。"""
    ledger = tmp_path / "state" / "session-review" / "failure-signatures.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text(
        json.dumps(
            {
                "failure_signature": "old",
                "session_id": "old-session",
                "observed_at": "2026-07-01T00:00:00+00:00",
                "failure_summary": "old",
            }
        )
        + "\n{"
        + "broken\n",
        encoding="utf-8",
    )
    transcript = _write_failed_codex_transcript(tmp_path, "new-session")
    work_dir = _work_dir(tmp_path)

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)], now=_FIXED_NOW) == 0

    result = json.loads(capsys.readouterr().out)
    assert result["failure_ledger_skipped"] == 1
    assert result["candidate_total"] == 0
    saved = ledger.read_text(encoding="utf-8")
    assert "old-session" not in saved
    assert "new-session" in saved


def test_parallel_prepare_keeps_both_sessions_in_failure_ledger(tmp_path: pathlib.Path) -> None:
    """別々の最上位セッションの準備を並行実行しても、共有記録へ両方の署名を残す。"""
    state_home = tmp_path / "state-home"
    environment = os.environ.copy()
    environment.update(
        {
            "XDG_STATE_HOME": str(state_home),
            "LOCALAPPDATA": str(state_home),
            "APPDATA": str(state_home),
            "USERPROFILE": str(tmp_path),
            "HOME": str(tmp_path),
        }
    )
    with contextlib.ExitStack() as opened:
        processes: list[subprocess.Popen[str]] = []
        for session_id in ("parallel-a", "parallel-b"):
            transcript = _write_failed_codex_transcript(tmp_path, session_id)
            work_dir = tmp_path / session_id
            work_dir.mkdir()
            processes.append(
                opened.enter_context(
                    subprocess.Popen(
                        [
                            sys.executable,
                            str(pathlib.Path(prepare.__file__)),
                            "--transcript",
                            str(transcript),
                            "--work-dir",
                            str(work_dir),
                        ],
                        env=environment,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                )
            )
        for process in processes:
            stdout, stderr = process.communicate(timeout=30)
            assert process.returncode == 0, stderr
            assert json.loads(stdout)["failure_ledger_skipped"] == 0

    ledger = state_home / "agent-toolkit" / "session-review" / "failure-signatures.jsonl"
    records = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert {item["session_id"] for item in records} == {"parallel-a", "parallel-b"}
    assert all(item["candidate_kind"] == "command-failure" and item["failure_event"] for item in records)
