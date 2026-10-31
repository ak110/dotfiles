"""振り返りの入力（会話の流れ、`candidates.md`、セッション統計）を書く`atk run-script session-review-prepare`を検証する。

公開されたコマンドから利用シナリオを実行する。
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import pytest
import session_evidence_candidates as evidence_candidates  # pylint: disable=import-error
import session_review_evidence as evidence  # pylint: disable=import-error
import session_review_prepare as prepare  # noqa: E402  # pylint: disable=wrong-import-position,import-error

from agent_toolkit._atk.wi import layout
from agent_toolkit._common import response_language_check, state_paths
from agent_toolkit._testing import delegated_threads, git_repository
from agent_toolkit._testing import session_evidence_support as support
from agent_toolkit._testing.failure_records import bash_failure_entries, failure_entries

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)
_LONG_INTERVENTION = "そうじゃなくて、対象は全部です。" + "理由の説明。" * 400 + "最後まで読んで。"
_BACKGROUND_OUTPUT_NOTICE = (
    '<atk-auto source="response-language" kind="block">'
    "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取った。完了通知を受けてから読むこと。</agent-toolkit-auto-inserted>"
)


@pytest.fixture(autouse=True)
def _isolate_failure_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """失敗署名の発生記録の保存先をテストごとの状態ディレクトリへ限定する。"""
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")  # pylint: disable=protected-access


def _work_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    return work_dir


def _bundle_rows(record: dict[str, Any], filename: str) -> list[dict[str, Any]]:
    """準備結果が案内する保存証拠を読む。"""
    path = pathlib.Path(record["work_dir"]) / "bundle" / filename
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _write_claude_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """初期要求、英語の状況説明と`pretooluse`の警告通知、失敗したコマンド、2000文字を超えるユーザー介入を持つtranscriptを書き込む。"""
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "I will run the linter now."},
                    {"type": "tool_use", "name": "Bash", "id": "toolu_fail", "input": {"command": "make lint"}},
                ],
            },
        },
        {
            "type": "attachment",
            "timestamp": "2026-09-06T12:00:11Z",
            "attachment": {
                "type": "hook_additional_context",
                "hookName": "PreToolUse:Bash",
                "toolUseID": "toolu_fail",
                "content": [_BACKGROUND_OUTPUT_NOTICE],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:20Z",
            "message": {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "toolu_fail", "is_error": True, "content": "Error: 検査に失敗した"}
                ],
            },
        },
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": _LONG_INTERVENTION}},
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    return transcript


def _hook_attachment(line_time: str, tool_use_id: str, notice: str) -> dict[str, object]:
    return {
        "type": "attachment",
        "timestamp": line_time,
        "attachment": {
            "type": "hook_additional_context",
            "hookName": "PreToolUse:Bash",
            "toolUseID": tool_use_id,
            "content": [notice],
        },
    }


def test_response_language_notices_are_excluded_from_candidates(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """応答言語hookの警告を候補から除いて件数だけを数え、同じ発生源・区分の他の警告と別の発生源の同じ本文は候補に残す。

    応答言語hookは遮断後に対処する型で、振り返りのたびに同じ見送り判定になる。
    除外しないと`candidates.md`へ毎回載り、発生源や区分を見ずに除くと是正を要する他の警告まで候補から消える。
    """
    opening = '<atk-auto source="pretooluse" kind="warn">'
    first_warning = f"{opening}{response_language_check.WARNING_BODY}判定対象の冒頭: 「I will run」</atk-auto>"
    repeated_strong_warning = (
        '<atk-auto source="response-language" kind="block">'
        f"{response_language_check.BLOCK_BODY}{response_language_check.WARNING_BODY}"
        "\nこの通知は同一セッションで3件目である。</atk-auto>"
    )
    other_source = f'<atk-auto source="posttooluse" kind="warn">{response_language_check.WARNING_BODY}</atk-auto>'
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {
                "role": "assistant",
                "content": [{"type": "tool_use", "name": "Bash", "id": "toolu_a", "input": {"command": "ls"}}],
            },
        },
        _hook_attachment("2026-09-06T12:00:11Z", "toolu_a", first_warning),
        _hook_attachment("2026-09-06T12:00:12Z", "toolu_a", repeated_strong_warning),
        _hook_attachment("2026-09-06T12:00:13Z", "toolu_a", _BACKGROUND_OUTPUT_NOTICE),
        _hook_attachment("2026-09-06T12:00:14Z", "toolu_a", other_source),
    ]
    transcript = tmp_path / "22222222-3333-4444-5555-666666666666.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    work_dir = _work_dir(tmp_path)

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    assert record["candidate_counts"] == {"hook-notice": 2}
    assert record["excluded_counts"]["response-language-notice"] == 2
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert "response-language-notice 2件" in candidates
    assert "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取った" in candidates
    assert "r1:6" in candidates and "claude:22222222-3333-4444-5555-666666666666=メイン" in candidates
    assert "累計2回以上" not in candidates


def test_prepare_comparison_materials(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """正常な直接本文・参照ファイル・明示読取と命令文字列の観測限界を、通常の準備から得る。"""
    entries = [{"type": "user", "message": {"role": "user", "content": "依頼"}}]
    body = "担当する作業の説明。" * 300
    calls = [
        (
            "mcp__plugin_agent-toolkit_agents_server__start",
            {"cwd": "/repo", "mode": "delegate", "model_type": "execute", "prompt": body},
        ),
        (
            "mcp__plugin_agent-toolkit_agents_server__start",
            {
                "cwd": "/repo",
                "subagent_md_path": "refine-prompt",
                "extra_params": {"対象プロンプト": "/inputs/task.md", "シナリオ": "なし"},
            },
        ),
        ("Read", {"file_path": "/rules/shared.md"}),
        ("Skill", {"skill": "agent-basics:search"}),
        ("Bash", {"command": "cat /rules/other.md"}),
    ]
    for index, (tool, inputs) in enumerate(calls):
        entries.extend(
            [
                {
                    "type": "assistant",
                    "timestamp": "2026-09-06T12:00:00Z",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": tool,
                                "id": f"call-{index}",
                                "input": inputs,
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
                                "tool_use_id": f"call-{index}",
                                "content": "記録した結果本文",
                            }
                        ],
                    },
                },
            ]
        )
    transcript = tmp_path / "comparison.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")
    work = _work_dir(tmp_path)
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
    record = json.loads(capsys.readouterr().out)
    materials = [
        json.loads(line) for line in (work / "bundle" / "comparison-materials.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert materials[0]["observed_body_characters"] == len(body)
    assert materials[0]["input_form"] == "direct-body"
    assert materials[1]["input_form"] == "file-reference" and materials[1]["observed_body_characters"] is None
    assert materials[2]["target"] == {"file_path": "/rules/shared.md"}
    assert materials[2]["recorded_result_characters"] == len("記録した結果本文")
    assert materials[3]["target"] == {"skill": "agent-basics:search"}
    assert materials[4]["category"] == "opaque-command" and not materials[4]["target"]
    comparison = pathlib.Path(record["comparison_materials_path"]).read_text(encoding="utf-8")
    assert "claude:comparison:2" in comparison and "claude:comparison:3" in comparison
    assert "/inputs/task.md" in comparison and "/rules/shared.md" in comparison
    assert record["candidate_total"] == 0


@pytest.mark.parametrize("has_identifier", [False, True])
def test_prepare_unresolved_scope(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], *, has_identifier: bool) -> None:
    """抽出から準備まで両未解決種別を通し、文書と1行JSONへ同じ対象を届ける。"""
    result = {"session_id": "22222222-3333-4444-8555-666666666666"} if has_identifier else {"status": "completed"}
    entries = [
        {"type": "user", "message": {"role": "user", "content": "依頼"}},
        {
            "type": "assistant",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "mcp__plugin_agent-toolkit_agents_server__start",
                        "id": "start",
                        "input": {"mode": "delegate", "prompt": "作業を行う"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "toolUseResult": result,
            "mcpMeta": {"structuredContent": result},
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "start",
                        "content": json.dumps(result),
                    }
                ]
            },
        },
    ]
    if not has_identifier:
        entries = [
            {
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "name": "mcp__agents_server__start",
                    "call_id": "start",
                    "arguments": {"engine": "codex"},
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "custom_tool_call_output", "call_id": "start", "output": {"status": "done"}},
            },
        ]
    transcript = tmp_path / "unresolved.jsonl"
    transcript.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    work = _work_dir(tmp_path)
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
    output = capsys.readouterr().out
    assert len(output.splitlines()) == 1
    record = json.loads(output)
    kind = "unresolved-record" if has_identifier else "unresolved-delegation"
    assert len(record["unconfirmed_scope"]) == 1
    event = record["unconfirmed_scope"][0]
    assert event["kind"] == kind and event["line"] == 3 and event["record"]
    for key in ("candidates_path", "stats_path"):
        document = pathlib.Path(record[key]).read_text(encoding="utf-8")
        assert json.dumps(event, ensure_ascii=False) in document
    assert record["candidate_total"] == 0 and not record["mandatory_candidates"]


def test_prepare_all_participant_costs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """全担当の原始費用と計数定義を示し、Codexのcacheを総入力へ重ねて足さない。"""
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(threads.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    claude = threads.home / ".claude" / "projects" / "repo" / f"{delegated_threads.CLAUDE_THREAD_ID}.jsonl"
    for path, values in ((threads.transcript, (10, 7, 2, 4)), (claude, (20, 9, 3, 6))):
        usage = dict(
            zip(
                ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"), values, strict=True
            )
        )
        with path.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "id": "usage",
                            "usage": usage,
                            "content": [{"type": "text", "text": "完了"}],
                        },
                    }
                )
                + "\n"
            )
    codex = next(threads.codex_home.rglob("*.jsonl"))
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 40,
        "cache_write_input_tokens": 5,
        "output_tokens": 30,
        "reasoning_output_tokens": 10,
        "total_tokens": 130,
    }
    with codex.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "response_item",
                    "payload": {"type": "token_count", "info": {"total_token_usage": usage, "last_token_usage": usage}},
                }
            )
            + "\n"
        )
    work = _work_dir(tmp_path)
    assert prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
    record = json.loads(capsys.readouterr().out)
    stats = [json.loads(line) for line in (work / "bundle" / "stats.jsonl").read_text(encoding="utf-8").splitlines()]
    total = next(event for event in stats if event["kind"] == "stats-total")
    assert total["tokens"] == {
        "input_tokens": 90,
        "output_tokens": 46,
        "cache_creation_input_tokens": 10,
        "cache_read_input_tokens": 50,
    }
    document = pathlib.Path(record["stats_path"]).read_text(encoding="utf-8")
    assert (
        delegated_threads.MAIN_SESSION_ID in document
        and delegated_threads.CLAUDE_THREAD_ID in document
        and delegated_threads.CODEX_THREAD_ID in document
    )
    assert '"cached_input_tokens": 40' in document and '"input_tokens": 100' in document
    assert '"input_tokens": 10' in document and '"input_tokens": 20' in document


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


@pytest.mark.parametrize("delivery", ["text", "send", "codex"])
def test_delegate_improvements_reach_prepare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], delivery: str
) -> None:
    """正常返却の省略区間も、元の全文から抽出した行と記録位置で準備へ含める。"""
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(threads.home / ".claude"))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    improvement = "気付いた改善点: 正常な委譲返却に含まれる新しい機会"
    body = "説明。" * 1000 + "\n" + improvement + "\n" + "補足。" * 1000
    delegated_threads.append_return(threads, body, delivery)
    work = _work_dir(tmp_path)
    assert prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(work)], now=_FIXED_NOW) == 0
    record = json.loads(capsys.readouterr().out)
    assert record["improvement_lines"] == [improvement]
    assert improvement in pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")


def _git_repository(path: pathlib.Path) -> pathlib.Path:
    return git_repository.init_repository(path, files={"tracked.txt": "tracked\n"}, commit_message="initial")


def test_prepare_writes_conversation_candidates_and_stats_without_queue_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """1回の実行で文書を作業ディレクトリへ書き、所在と件数を1行JSONで返し、キューを変更しない。

    振り返りはメインが同じセッション内で分析する。
    `atk run-script session-review-prepare`がAWIを投入すると、キューへ未分析の項目が残る。
    `atk`を起動できない環境でも成功することで、キュー操作を呼ばないことを確かめる。
    """
    monkeypatch.setenv("PATH", str(tmp_path / "no-atk"))
    work_dir = _work_dir(tmp_path)
    transcript = _write_claude_transcript(tmp_path)

    exit_code = prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)], now=_FIXED_NOW)

    assert exit_code == 0
    stdout = capsys.readouterr().out
    assert len(stdout.splitlines()) == 1
    record = json.loads(stdout)
    assert record["candidate_counts"] == {"hook-notice": 1, "user-intervention": 1}
    assert record["candidate_total"] == 2
    assert record["excluded_counts"]["single-session-failure"] == 1
    assert record["failure_ledger_skipped"] == 0
    assert record["utterance_counts"] == {"user": 2, "assistant": 1}
    assert record["elapsed_seconds"] == 60
    assert record["prepared_at"] == "2026-09-06T12:34:56Z"
    assert record["reference_document"] is None
    for key in ("conversation_path", "candidates_path", "stats_path"):
        assert pathlib.Path(record[key]).parent == work_dir
        assert pathlib.Path(record[key]).is_file()

    conversation = pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
    assert "初期要求" in conversation
    assert "I will run the linter now." in conversation
    assert "agent-toolkit-auto-inserted" not in conversation
    assert "Bash×1" in conversation and "失敗1件" in conversation
    assert "make lint" not in conversation and "Error: 検査に失敗した" not in conversation
    assert "hook_additional_context" not in conversation
    assert _LONG_INTERVENTION in conversation
    assert f"claude:{transcript.stem}:5" in conversation
    assert f"atk run-script session-review-evidence -- {transcript.resolve()} --detail <記録位置>" in conversation

    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert "- 候補: 2件（hook-notice 1件、user-intervention 1件）" in candidates
    assert "r1:3" in candidates and "claude:11111111-2222-3333-4444-555555555555=メイン" in candidates
    assert "単発の失敗1件" in candidates
    index = _bundle_rows(record, "review-index.jsonl")
    assert len(index) == 3 and sum(item["mandatory"] for item in index) == 1
    details = [json.loads((work_dir / "bundle" / item["evidence_path"]).read_text(encoding="utf-8")) for item in index]
    assert any("make lint" in json.dumps(item, ensure_ascii=False) for item in details)
    # 是正の全文と判定の前後関係は保存証拠から取得し、初回表示へ展開しない。
    assert _LONG_INTERVENTION not in candidates
    assert any(_LONG_INTERVENTION in json.dumps(item, ensure_ascii=False) for item in details)
    assert "直前と直後のユーザー発話" not in candidates

    stats = pathlib.Path(record["stats_path"]).read_text(encoding="utf-8")
    assert "- 経過秒: 60秒（セッションの最初の記録から準備時点まで）" in stats
    assert pathlib.Path(record["comparison_materials_path"]).is_file()


def test_prepare_lists_offered_answer_as_mandatory_confirmation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """選択肢のlabelをそのまま選び自由記述の無い確認回答を、確認の候補として載せる。

    介入として数えると是正を含まない回答まで介入として分析する。除外すると不要だった確認が件数だけになり、
    確認の要否を振り返る入力から消える。確認の候補は評価が必須の候補として1行JSONにも示し、深さと再発防止策の要否は重大度判断へ渡す。
    """
    monkeypatch.setenv("PATH", str(tmp_path / "no-atk"))
    work_dir = _work_dir(tmp_path)
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "AskUserQuestion",
                        "id": "toolu_question",
                        "input": {
                            "questions": [
                                {"question": "方針", "options": [{"label": "既存機構へ統合"}, {"label": "新機構を追加"}]}
                            ]
                        },
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:20Z",
            "toolUseResult": {"answers": {"方針": "既存機構へ統合"}, "annotations": {}},
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_question", "content": "回答"}],
            },
        },
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    assert "user-intervention" not in record["candidate_counts"]
    assert record["candidate_counts"]["confirmation-request"] == 1
    assert "question-answer" not in record["excluded_counts"]
    assert record["mandatory_candidates"] == ["c0001"]
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert "- c0001 confirmation-request（発生1件、評価が必須）: 方針" in candidates
    assert "評価が必須: 1件" in candidates
    candidate = next(item for item in _bundle_rows(record, "candidates.jsonl") if item.get("kind") == "candidate")
    assert [option["label"] for option in candidate["assistant_context"][0]["options"]] == ["既存機構へ統合", "新機構を追加"]
    assert candidate["user_response"] == [{"answers": ["既存機構へ統合"]}]
    assert _bundle_rows(record, "review-index.jsonl")[0]["mandatory"] is True


def _write_wi(root: pathlib.Path, state: str, name: str, frontmatter: dict[str, str], body: str) -> None:
    """private-notesへWIを1件書く。frontmatterの形は`atk wi add`が保存する形に合わせる。"""
    directory = root / state
    directory.mkdir(parents=True, exist_ok=True)
    head = "".join(f"{key}: {value}\n" for key, value in frontmatter.items())
    (directory / name).write_text(f"---\n{head}---\n{body}", encoding="utf-8")


def _uwi_body(question: str, answer: str) -> str:
    return (
        f"\n## 質問\n\n{question}\n\n## 判断材料\n\n- 材料\n\n## 回答\n\n"
        f"<!-- ユーザーはこの行以降に回答を追記する -->\n{answer}\n"
    )


def _bash_entry(timestamp: str, call_id: str, command: str) -> dict[str, object]:
    return {
        "type": "assistant",
        "timestamp": timestamp,
        "message": {
            "role": "assistant",
            "content": [{"type": "tool_use", "name": "Bash", "id": call_id, "input": {"command": command}}],
        },
    }


@pytest.mark.parametrize(
    ("dated", "past_state"),
    [(False, "adopted"), (True, "adopted"), (True, "future-state")],
    ids=["legacy-layout", "dated-layout", "future-state"],
)
def test_prepare_marks_wi_candidates_and_similar_past_records(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, dated: bool, past_state: str
) -> None:
    """投入したUWIを確認の候補、WIの記入欄の是正を是正の候補として載せ、ユーザーの是正へ過去の同種記録を添える。

    報告用UWI、選択肢と一致する回答、読んだだけのWI、引用の中のコマンド名は候補にしない。
    過去の同種記録はユーザーの発話を保持する節だけと比べ、記入元のWI自身と短い文の一致を含めない。
    """
    session_id = "11111111-2222-3333-4444-555555555555"
    root = tmp_path / "private-notes"
    correction = "エージェントが判断すべきことをユーザーに委ねないで、判断できないことだけを聞いてほしい"
    uwi = {"target_repo": "github.com/example/repo", "type": "uwi", "question_type": "choice", "choices": "進める,止める"}
    _write_wi(
        root, "adopted", "20261006-100000-001.md", {**uwi, "submitter_session": session_id}, _uwi_body("確認A？", "進める")
    )
    _write_wi(
        root, "adopted", "20261006-100000-002.md", {**uwi, "submitter_session": session_id}, _uwi_body("確認B？", correction)
    )
    _write_wi(
        root,
        "inbox",
        "20261006-100000-003.md",
        {**uwi, "submitter_session": session_id},
        _uwi_body("2026年10月6日のrepoの作業結果と振り返りについて、この対応で問題ありませんか？", "問題がある"),
    )
    _write_wi(root, "adopted", "20261006-100000-004.md", uwi, _uwi_body("他のセッションの確認？", "読んだだけの是正の記入"))
    _write_wi(root, "processing", "20261006-100000-005.md", uwi, _uwi_body("処理した確認？", "処理した是正の記入"))
    awi = {"target_repo": "github.com/example/repo", "type": "awi"}
    _write_wi(
        root, "processing", "20261006-100000-006.md", awi, "\n# 処理したAWI\n\n本文\n\n## ユーザーコメント\n\nコメントの是正\n"
    )
    _write_wi(root, "inbox", "20261006-100000-007.md", awi, "\n# 引用だけのAWI\n\n本文\n\n## ユーザーコメント\n\n引用の是正\n")
    _write_wi(
        root,
        "adopted",
        "20261001-163353-001.md",
        awi,
        f"\n# 過去の同種の是正への対策\n\n## ユーザー指摘の逐語引用\n\n```text\n{correction}。\n```\n",
    )
    _write_wi(root, "adopted", "20261001-163353-002.md", awi, f"\n# 本文だけに同じ文を持つ項目\n\n{correction}\n")
    if dated:
        destination = root / "2026" / "10"
        destination.mkdir(parents=True)
        for path in sorted(root.glob("*/*.md")):
            text = path.read_text(encoding="utf-8")
            text = text.replace("---\n", f"---\nstate: {path.parent.name}\n", 1)
            (destination / path.name).write_text(text, encoding="utf-8")
            path.unlink()
    if past_state == "future-state":
        original_entry_state_of = layout.entry_state_of
        monkeypatch.setattr(
            layout,
            "entry_state_of",
            lambda path, *args, **kwargs: (
                past_state if path.name == "20261001-163353-001.md" else original_entry_state_of(path, *args, **kwargs)
            ),
        )
    entries = [
        {"type": "user", "timestamp": "2026-10-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        _bash_entry("2026-10-06T12:00:10Z", "toolu_show", "atk wi show --skip-pull 20261006-100000-004.md"),
        _bash_entry("2026-10-06T12:00:20Z", "toolu_adopt", "atk wi adopt 20261006-100000-005.md --target-repo=/repo"),
        _bash_entry("2026-10-06T12:00:30Z", "toolu_start", "atk wi start-processing 20261006-100000-006.md"),
        _bash_entry("2026-10-06T12:00:40Z", "toolu_echo", "echo 'atk wi adopt 20261006-100000-007.md'"),
        {"type": "user", "timestamp": "2026-10-06T12:01:00Z", "message": {"role": "user", "content": correction}},
        {
            "type": "user",
            "timestamp": "2026-10-06T12:02:00Z",
            "message": {"role": "user", "content": "上限をリセットした。続けて"},
        },
    ]
    transcript = tmp_path / f"{session_id}.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    saved = _bundle_rows(record, "candidates.jsonl")
    confirmations = [item for item in saved if item.get("candidate_kind") == "confirmation-request"]
    responses = [item for item in saved if item.get("candidate_kind") == "wi-user-response"]
    assert [item["wi_file"] for item in confirmations] == [
        "20261006-100000-001.md",
        "20261006-100000-002.md",
    ]
    assert [item["wi_file"] for item in responses] == [
        "20261006-100000-002.md",
        "20261006-100000-005.md",
        "20261006-100000-006.md",
    ]
    assert record["candidate_counts"] == {"confirmation-request": 2, "user-intervention": 2, "wi-user-response": 3}
    assert sorted(record["mandatory_candidates"]) == sorted(
        line.split()[1] for line in candidates.splitlines() if "評価が必須）" in line
    )
    assert len(record["mandatory_candidates"]) == 7
    by_text = {line.split()[1]: line for line in candidates.splitlines() if line.startswith("- c") and "評価が必須" in line}
    intervention_id = next(
        candidate_id for candidate_id, line in by_text.items() if "user-intervention" in line and "委ねないで" in line
    )
    response_id = next(
        candidate_id for candidate_id, line in by_text.items() if "wi-user-response" in line and "委ねないで" in line
    )
    reset_id = next(candidate_id for candidate_id, line in by_text.items() if "上限をリセットした" in line)
    # ユーザーの発話を保持する節（逐語引用、回答）に同じ文を持つWIだけを添え、本文だけに同じ文を持つ項目は含めない。
    # 回答欄の是正では、記入元のUWI自身を過去の同種記録から除く。
    assert record["similar_records"][intervention_id] == ["20261001-163353-001.md", "20261006-100000-002.md"]
    assert record["similar_records"][response_id] == ["20261001-163353-001.md"]
    assert record["similar_records"][reset_id] == []
    saved_similar = {item["candidate_id"]: item["similar_records"] for item in _bundle_rows(record, "review-index.jsonl")}
    assert saved_similar[reset_id] == []
    assert saved_similar[response_id] == [["20261001-163353-001.md", past_state, "過去の同種の是正への対策"]]


@pytest.mark.parametrize(
    "command",
    [
        "for f in 20261006-100000-006.md; do atk wi adopt $f; done",
        'for f in 20261006-100000-006.md; do atk wi adopt "${f}"; done',
        "{ atk wi adopt 20261006-100000-006.md; } > o",
        "if true; then atk wi adopt 20261006-100000-006.md; fi",
    ],
    ids=["for-loop", "for-loop-braced", "group", "if"],
)
def test_prepare_counts_wi_processed_inside_compound_commands(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """複合コマンドとグループの中で処理したWIも処理済みとして扱い、ユーザーの記入を評価の候補へ載せる。

    数え漏れると、処理したWIへのユーザーの是正が振り返りの評価から抜ける。
    """
    root = tmp_path / "private-notes"
    awi = {"target_repo": "github.com/example/repo", "type": "awi"}
    _write_wi(
        root, "processing", "20261006-100000-006.md", awi, "\n# 処理したAWI\n\n本文\n\n## ユーザーコメント\n\nコメントの是正\n"
    )
    entries = [
        {"type": "user", "timestamp": "2026-10-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        _bash_entry("2026-10-06T12:00:10Z", "toolu_adopt", command),
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    record = json.loads(capsys.readouterr().out)
    responses = [item for item in _bundle_rows(record, "candidates.jsonl") if item.get("candidate_kind") == "wi-user-response"]
    assert [item["wi_file"] for item in responses] == ["20261006-100000-006.md"]


@pytest.mark.parametrize(
    ("first_text", "expected_candidates", "expected_initial_request"),
    [
        (
            "<command-name>/goal</command-name>\n"
            '<command-args><atk-auto source="process-loop" kind="goal">'
            "`agent-toolkit:process-wi`を完遂してください。</atk-auto></command-args>",
            {"user-intervention": 2},
            None,
        ),
        ("この変更を実装して", {"user-intervention": 2}, 1),
    ],
    ids=["process-loop-start", "human-request"],
)
def test_prepare_keeps_first_human_intervention_after_process_loop_start(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    first_text: str,
    expected_candidates: dict[str, int],
    expected_initial_request: int | None,
) -> None:
    """process-loopの起動後に続く人間の発話は候補へ残し、人間の依頼で始まる記録では初期依頼を除く。"""
    monkeypatch.setenv("PATH", str(tmp_path / "no-atk"))
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": first_text}},
        {"type": "user", "timestamp": "2026-09-06T12:00:30Z", "message": {"role": "user", "content": "対象を絞らないで"}},
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": "それも直して"}},
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-666666666666.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    exit_code = prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0
    record = json.loads(capsys.readouterr().out)
    assert record["candidate_counts"] == expected_candidates
    assert record["excluded_counts"].get("initial-request") == expected_initial_request


def test_prepare_keeps_improvement_lines_in_omitted_middle(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    """1000字を超える発話の省略区間にある`気付いた改善点:`の行を、会話の流れへ全て残す。

    振り返りは会話の流れからこの行を全件抽出するため、省略区間で行が消えると、作業中に伝えた改善の機会を振り返りが分析できない。
    行を持たない長い発話は従来どおり先頭と末尾だけを載せ、中間の本文を残さない。
    """
    first_note = "気付いた改善点: atk agents waitの出力をjqで加工するスクリプトを3回作った（ツールの出力形式）"
    second_note = "  気付いた改善点: 全件そろうまで戻らない回収ループで先に届いた結果を待たせた（待ち方の手順）"
    filler = "作業の経過を説明する。" * 60
    report = "\n".join(
        ["作業完了報告。" + "冒頭の説明。" * 90, filler, first_note, filler, second_note, filler, "末尾の結び。" * 90]
    )
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:10Z",
            "message": {"role": "assistant", "content": [{"type": "text", "text": report}]},
        },
        {"type": "user", "timestamp": "2026-09-06T12:01:00Z", "message": {"role": "user", "content": _LONG_INTERVENTION}},
    ]
    transcript = tmp_path / "33333333-4444-5555-6666-777777777777.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    result = json.loads(capsys.readouterr().out)
    conversation = pathlib.Path(result["conversation_path"]).read_text(encoding="utf-8")
    assert report not in conversation
    assert result["improvement_lines"] == [first_note, second_note.strip()]
    assert filler not in conversation
    assert _LONG_INTERVENTION in conversation


def test_prepare_reads_codex_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexのthread IDからも同じ文書を書き、全文を照会するコマンドをthread IDの形で示す。"""
    codex_home = tmp_path / "codex-home"
    rollout_dir = codex_home / "sessions" / "2026" / "09" / "06"
    rollout_dir.mkdir(parents=True)
    thread_id = "019900aa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    entries = [
        {
            "timestamp": "2026-09-06T12:00:00Z",
            "type": "response_item",
            "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
        },
        {
            "timestamp": "2026-09-06T12:00:01Z",
            "type": "response_item",
            "payload": {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "call-ls",
                "arguments": json.dumps({"cmd": "ls docs"}),
            },
        },
        {
            "timestamp": "2026-09-06T12:00:02Z",
            "type": "response_item",
            "payload": {"type": "function_call_output", "call_id": "call-ls", "output": "成功した出力の本文"},
        },
        {
            "timestamp": "2026-09-06T12:00:03Z",
            "type": "response_item",
            "payload": {
                "type": "custom_tool_call",
                "name": "apply_patch",
                "call_id": "call-patch",
                "input": "*** Begin Patch\n*** Update File: docs/a.md\n+書き込む本文\n*** End Patch",
            },
        },
        {
            "timestamp": "2026-09-06T12:00:04Z",
            "type": "event_msg",
            "payload": {
                "type": "item_completed",
                "item": {
                    "type": "CommandExecution",
                    "status": "failed",
                    "command": ["rg", "x"],
                    "exit_code": 2,
                    "stderr": "rg: 失敗",
                },
            },
        },
    ]
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8"
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    exit_code = prepare.main(["--codex-thread-id", thread_id, "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    assert record["candidate_total"] == 0
    assert record["utterance_counts"] == {"user": 1, "assistant": 0}
    conversation = pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
    assert f"--codex-thread-id {thread_id} --detail <記録位置>" in conversation
    assert "exec_command×1" in conversation and "CommandExecution×1" in conversation
    assert "apply_patch×1" in conversation
    assert "rg: 失敗" not in conversation and "成功した出力の本文" not in conversation
    assert "失敗1件" in conversation
    assert "候補として残す問題は無かった。" in pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")


def test_prepare_lists_adhoc_processing_per_record_without_failure_selection(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """その場のコードによる加工は記録ごとの件数と記録位置・代表入力の一覧で載り、失敗署名の選別を受けない。

    失敗した加工の呼び出しも、単発の失敗として件数表へ送られる失敗の候補とは別に加工の候補へ残る。
    """
    saved_output = "/home/u/.cache/agent-toolkit/managed-temp/atk-output-abc/output.txt"
    entries = [
        {"type": "user", "timestamp": "2026-09-06T12:00:00Z", "message": {"role": "user", "content": "初期要求"}},
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:01Z",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "id": "toolu_sed", "input": {"command": f"sed -n 2p {saved_output}"}}
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:02Z",
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_sed", "content": "2行目"}]},
        },
        {
            "type": "assistant",
            "timestamp": "2026-09-06T12:00:03Z",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "id": "toolu_py",
                        "input": {"command": "python3 -c 'import sys; sys.exit(3)'"},
                    }
                ],
            },
        },
        {
            "type": "user",
            "timestamp": "2026-09-06T12:00:04Z",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "toolu_py", "is_error": True, "content": "Exit code 3"}],
            },
        },
    ]
    transcript = tmp_path / "11111111-2222-3333-4444-555555555555.jsonl"
    transcript.write_text("".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries), encoding="utf-8")

    exit_code = prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    assert record["candidate_counts"] == {"adhoc-processing": 1}
    assert record["excluded_counts"]["single-session-failure"] == 1
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    record_id = "claude:11111111-2222-3333-4444-555555555555"
    assert "- c0001 adhoc-processing（発生2件）:" in candidates
    candidate = next(item for item in _bundle_rows(record, "candidates.jsonl") if item.get("candidate_id") == "c0001")
    assert [(call["record"], call["line"], call["text"]) for call in candidate["calls"]] == [
        (record_id, 2, f"sed -n 2p {saved_output}"),
        (record_id, 4, "python3 -c 'import sys; sys.exit(3)'"),
    ]


def test_prepare_writes_breakdown_of_rate_limiting_threads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """律速区間に排他区間を持つagent threadごとに、内部の工程の内訳の節を排他区間の長い順に`stats.md`へ書く。

    内訳が無いと、振り返りは委譲先の返却本文の説明を内訳の代わりにして律速区間を見送る。
    節の記録位置は`--detail`へそのまま渡す値であり、委譲先の記録の行を指す必要がある。
    """
    threads = delegated_threads.write_delegated_threads(tmp_path)
    monkeypatch.setenv("HOME", str(threads.home))
    monkeypatch.setenv("CODEX_HOME", str(threads.codex_home))
    claude_id = delegated_threads.CLAUDE_THREAD_ID
    codex_id = delegated_threads.CODEX_THREAD_ID

    exit_code = prepare.main(["--transcript", str(threads.transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    stats = pathlib.Path(json.loads(capsys.readouterr().out)["stats_path"]).read_text(encoding="utf-8")
    claude_section = f"""
## 律速threadの内訳: {claude_id}

- 排他区間: 175.0秒
- turnの経過秒: 175秒
- ツール別: Bash 2件40.0秒、Read 1件1.0秒
- 60秒以上の間隔:
  - 80.0秒: claude:{claude_id}:3〜claude:{claude_id}:4
- 反復:
  - Bash 2回（claude:{claude_id}:2、claude:{claude_id}:4）: pytest
- 遅い呼び出しの上位:
  - Bash 30.0秒 claude:{claude_id}:2: pytest
  - Bash 10.0秒 claude:{claude_id}:4: pytest
  - Read 1.0秒 claude:{claude_id}:6: /repo/a.md
"""
    codex_section = f"""
## 律速threadの内訳: {codex_id}

- 排他区間: 59.0秒
- turnの経過秒: 59秒
- ツール別: exec_command 1件20.0秒
- 60秒以上の間隔: なし
- 反復: なし
- 遅い呼び出しの上位:
  - exec_command 20.0秒 codex:{codex_id}:3: make build
"""
    assert claude_section in stats
    assert codex_section in stats
    assert stats.index(claude_section) < stats.index(codex_section)


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


@pytest.mark.parametrize("linked_worktree", [False, True])
def test_prepare_resolves_reference_document_from_main_worktree_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    *,
    linked_worktree: bool,
) -> None:
    """通常checkoutとlinked worktreeは同じ参照文書へ解決し、JSONの`reference_document`で返す。"""
    main_worktree = _git_repository(tmp_path / "dotfiles")
    target_repo = main_worktree
    if linked_worktree:
        target_repo = tmp_path / "process-loop"
        subprocess.run(
            ["git", "-C", str(main_worktree), "worktree", "add", "--detach", str(target_repo)],
            check=True,
            capture_output=True,
        )
    home = tmp_path / "home"
    document = home / ".claude" / "docs" / "session-review-dotfiles.md"
    document.parent.mkdir(parents=True)
    document.write_text("# 観点\n", encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda _cls: home))

    assert (
        prepare.main(
            [
                "--transcript",
                str(_write_claude_transcript(tmp_path)),
                "--work-dir",
                str(_work_dir(tmp_path)),
                "--target-repo",
                str(target_repo),
            ],
            now=_FIXED_NOW,
        )
        == 0
    )

    record = json.loads(capsys.readouterr().out)
    assert record["reference_document"] == str(document)


def test_prepare_reports_missing_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """記録、作業ディレクトリまたは`atk run-script session-review-evidence`の同一性が成立しない場合を扱う。

    不足項目を返して標準出力を空に保つ。
    """
    transcript = _write_claude_transcript(tmp_path)
    work_dir = _work_dir(tmp_path)

    assert prepare.main(["--transcript", str(tmp_path / "missing.jsonl"), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == "不足: transcript_path"
    assert "`--transcript`" in captured.err.splitlines()[1].removeprefix("次の操作: ")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(tmp_path / "absent")]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == "不足: work_dir"
    assert "`--work-dir`" in captured.err.splitlines()[-1].removeprefix("次の操作: ")

    monkeypatch.setattr(prepare, "__file__", str(tmp_path / "detached" / "session_review_prepare.py"))
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == "不足: evidence_script"
    assert captured.err.splitlines()[1].startswith("次の操作: `atk run-script session-review-prepare --")


def _delegate_adhoc_session(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """メインがCodexの委譲先（役割文書exec、mode task）を起動し、委譲先がその場のコードを実行したセッションを書く。

    記録の形は`agent_toolkit._testing.session_evidence_support`のCodex rollout（2026年10月4日の記録）を写す。
    """
    parent = support.tool_call_session(tmp_path, monkeypatch, "rg needle")
    entries = [json.loads(line) for line in parent.read_text(encoding="utf-8").splitlines()]
    entries[5]["message"]["content"][0]["input"]["mode"] = "task"
    support.write_jsonl(parent, entries)
    codex = next((tmp_path / "codex").rglob("*.jsonl"))
    entries = [json.loads(line) for line in codex.read_text(encoding="utf-8").splitlines()]
    entries[1:1] = [
        support.codex_item(
            "2026-10-06T00:00:05Z",
            {
                "type": "message",
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "次の文書の手順を実行せよ（出所: /plugin/share/exec.subagent.md）。\n作業"}
                ],
            },
        ),
        support.codex_item(
            "2026-10-06T00:00:06Z",
            {
                "type": "function_call",
                "name": "exec_command",
                "call_id": "c-py",
                "arguments": '{"cmd": "python3 -c \'print(2)\'"}',
            },
        ),
        support.codex_item("2026-10-06T00:00:06.500Z", {"type": "function_call_output", "call_id": "c-py", "output": "2"}),
    ]
    support.write_jsonl(codex, entries)
    return parent


def test_prepare_adds_record_provenance_to_candidate_locations(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """委譲先の記録にある候補の記録位置へ、役割文書・起動mode・起動元とその役割を添える。

    要約の文言（コマンドやパス）から実行主体を推させないための契約であり、期待値は同じbundleの
    `record-provenance`の項目（`role_document`、`mode`、`source_record`）から導く。
    """
    parent = _delegate_adhoc_session(tmp_path, monkeypatch)

    exit_code = prepare.main(["--transcript", str(parent), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    delegate = f"codex:{support.TOOL_CALL_THREAD}"
    provenance = next(line for line in candidates.splitlines() if f"由来: {delegate}=" in line)
    assert provenance.startswith(f"- r1: 由来: {delegate}=役割文書exec・mode task・起動元claude:parent-session:")
    assert provenance.endswith("（メイン）")


def test_prepare_marks_candidate_locations_without_provenance(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """由来を持たない証拠bundle（`record-provenance`を出力する前の版で作成した記録など）では、取得できなかったことを示す。"""
    parent = _delegate_adhoc_session(tmp_path, monkeypatch)
    original = prepare._read_jsonl  # pylint: disable=protected-access

    def read_without_provenance(path: pathlib.Path) -> list[dict[str, Any]]:
        return [event for event in original(path) if event.get("kind") != "record-provenance"]

    monkeypatch.setattr(prepare, "_read_jsonl", read_without_provenance)

    exit_code = prepare.main(["--transcript", str(parent), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert f"由来: codex:{support.TOOL_CALL_THREAD}=由来を取得できなかった" in candidates


@pytest.mark.parametrize("existing_ledger", [False, True])
def test_prepare_conversation_only_preserves_failure_ledger(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    *,
    existing_ledger: bool,
) -> None:
    """引き継ぎ用の会話生成は、候補文書・統計と共有の失敗署名を更新せず終わる。"""
    transcript = _write_claude_transcript(tmp_path)
    work = _work_dir(tmp_path)
    ledger = tmp_path / "state" / "session-review" / "failure-signatures.jsonl"
    original = "既存の失敗署名の記録\n"
    if existing_ledger:
        ledger.parent.mkdir(parents=True)
        ledger.write_text(original, encoding="utf-8")

    assert (
        prepare.main(
            [
                "--transcript",
                str(transcript),
                "--work-dir",
                str(work),
                "--conversation-only",
            ],
            now=_FIXED_NOW,
        )
        == 0
    )

    record = json.loads(capsys.readouterr().out)
    conversation = pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
    assert "初期要求" in conversation and "Bash×1" in conversation
    assert record["conversation_utf8_bytes"] == len(conversation.encode("utf-8"))
    assert record["conversation_limit_bytes"] == 100_000
    assert not (work / "candidates.md").exists()
    assert not (work / "stats.md").exists()
    assert not (work / "comparison-materials.md").exists()
    assert ledger.exists() is existing_ledger
    if existing_ledger:
        assert ledger.read_text(encoding="utf-8") == original


def _file_body(name: str) -> str:
    """末尾の行で結果の本文の有無を確かめられる本文を返す。"""
    return "\n".join(f"{name}-line-{index}" for index in range(20))


def test_prepare_omits_tool_result_bodies_in_claude_conversation(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ツール結果と長い入力は本文を載せず、種類・量・失敗と記録位置を示す。

    会話の流れは作業の流れを振り返る入力であり、結果の本文は記録位置から照会する。
    ファイル表示、パイプを含む加工、失敗、ホストの長文保存の通知のいずれの結果も本文を載せない。
    """
    persisted = "<persisted-output>\nOutput too large (73.7KB). Full output saved to: /tmp/saved.txt\n" + _file_body("saved")
    calls: list[tuple[str, str, dict[str, Any], str, bool]] = [
        ("read", "Read", {"file_path": "/repo/read.md", "offset": 3}, _file_body("read"), False),
        ("pipe", "Bash", {"command": "rg -n needle /repo | head -20"}, _file_body("pipe"), False),
        ("failed", "Bash", {"command": "make lint"}, _file_body("failed"), True),
        ("persisted", "Read", {"file_path": "/repo/persisted.md"}, persisted, False),
    ]
    entries: list[dict[str, Any]] = [support.timestamped_entry("2026-10-19T00:00:00Z", "依頼")]
    for index, (call_id, name, tool_input, content, is_error) in enumerate(calls, start=1):
        entries.append(support.claude_call(f"2026-10-19T00:0{index}:00Z", call_id, name, tool_input))
        result = support.claude_result(f"2026-10-19T00:0{index}:30Z", call_id, content)
        result["message"]["content"][0]["is_error"] = is_error
        entries.append(result)
    transcript = tmp_path / "77777777-1111-4222-8333-444444444444.jsonl"
    support.write_jsonl(transcript, entries)

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    conversation = pathlib.Path(json.loads(capsys.readouterr().out)["conversation_path"]).read_text(encoding="utf-8")
    assert "依頼" in conversation
    assert "呼出4件" in conversation
    assert "Read×2" in conversation and "Bash×2" in conversation
    assert "失敗1件" in conversation
    for name in ("read", "pipe", "failed", "saved"):
        assert f"{name}-line-" not in conversation
    assert "Output too large" not in conversation
    locator = f"claude:{transcript.stem}:3"
    assert locator in conversation
    assert evidence.main([str(transcript), "--detail", locator]) == 0
    assert "read-line-19" in capsys.readouterr().out


def test_prepare_summarizes_codex_tool_activity(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CodexのCommandExecutionと`exec_command`も種類・量・非0終了へ縮約する。

    CommandExecutionの`item_completed`の形と、`exec_command`の結果の先頭の`Chunk ID:`から`Output:`までの行の形は、
    2026年10月19日のCodex CLI 0.163.0の記録を写す。
    """
    thread_id = "019a0000-1111-7222-8333-444444444444"
    rollout_dir = tmp_path / "codex-home" / "sessions" / "2026" / "10" / "19"

    def command_execution(line_time: str, item_id: str, command: str, output: str, exit_code: int) -> dict[str, Any]:
        item = {
            "type": "CommandExecution",
            "id": item_id,
            "command": ["/bin/bash", "-lc", command],
            "cwd": "file:///repo",
            "status": "completed" if exit_code == 0 else "failed",
            "aggregated_output": output,
            "exit_code": exit_code,
        }
        return {"timestamp": line_time, "type": "event_msg", "payload": {"type": "item_completed", "item": item}}

    def exec_command(line_time: str, call_id: str, command: str, output: str, exit_code: int) -> list[dict[str, Any]]:
        header = f"Chunk ID: abc\nWall time: 0.0 seconds\nProcess exited with code {exit_code}\nOutput:\n"
        return [
            support.codex_item(
                line_time,
                {
                    "type": "function_call",
                    "name": "exec_command",
                    "call_id": call_id,
                    "arguments": json.dumps({"cmd": command}),
                },
            ),
            support.codex_item(line_time, {"type": "function_call_output", "call_id": call_id, "output": header + output}),
        ]

    support.write_jsonl(
        rollout_dir / f"rollout-2026-10-19T00-00-00-{thread_id}.jsonl",
        [
            support.codex_item(
                "2026-10-19T00:00:00Z",
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
            ),
            command_execution("2026-10-19T00:00:01Z", "exec-sed", "sed -n '1,40p' /repo/sed.md", _file_body("sed"), 0),
            command_execution("2026-10-19T00:00:02Z", "exec-failed", "make check", _file_body("checked"), 1),
            *exec_command("2026-10-19T00:00:03Z", "call-ok", "rg -n needle /repo", _file_body("found"), 0),
            *exec_command("2026-10-19T00:00:04Z", "call-ng", "make lint", _file_body("linted"), 2),
        ],
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))

    assert prepare.main(["--codex-thread-id", thread_id, "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW) == 0

    conversation = pathlib.Path(json.loads(capsys.readouterr().out)["conversation_path"]).read_text(encoding="utf-8")
    assert "呼出4件" in conversation
    assert "CommandExecution×2" in conversation and "exec_command×2" in conversation
    assert "失敗2件" in conversation
    for name in ("sed", "checked", "found", "linted"):
        assert f"{name}-line-" not in conversation
    assert "Chunk ID: abc" not in conversation


def test_escalation_candidate_requires_evaluation() -> None:
    """続行不能の返却は担当の作業遂行不能を示すため、後続で回復しても評価必須の候補にする。"""
    assert prepare.is_mandatory({"candidate_kind": "escalation"})
    assert not prepare.is_mandatory({"candidate_kind": "delegate-return"})
