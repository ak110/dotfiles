# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""セッション振り返りの試験（session_review_prepare_conversation_test）。"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest
import session_review_evidence as evidence
import session_review_prepare as prepare

from agent_toolkit._testing import delegated_threads
from agent_toolkit._testing import session_evidence_support as support
from agent_toolkit._testing.session_review_prepare_support import (
    _FIXED_NOW,
    _LONG_INTERVENTION,
    _bundle_rows,
    _work_dir,
    _write_claude_transcript,
)
from agent_toolkit._testing.session_review_prepare_support import _isolate_failure_ledger as _isolate_failure_ledger


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
    calls = [line for line in conversation.splitlines() if line.startswith("- 呼出 ")]
    assert len(calls) == 1
    assert calls[0].startswith(f"- 呼出 Bash claude:{transcript.stem}:2（失敗1件）:")
    assert '"command": "make lint"' in calls[0]
    assert "Error: 検査に失敗した" not in conversation
    assert len(conversation.encode("utf-8")) <= 100_000
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
    calls = [line for line in conversation.splitlines() if line.startswith("- 呼出 ")]
    assert len(calls) == 3
    for row, tool, line, body in zip(
        calls,
        ("exec_command", "apply_patch", "CommandExecution"),
        (2, 4, 5),
        ("ls docs", "docs/a.md", "rg x"),
        strict=True,
    ):
        assert row.startswith(f"- 呼出 {tool} ") and f":{line}" in row and body in row
    assert "rg: 失敗" not in conversation and "成功した出力の本文" not in conversation
    assert "失敗1件" in conversation
    assert "候補として残す問題は無かった。" in pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")


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
    assert "初期要求" in conversation
    calls = [line for line in conversation.splitlines() if line.startswith("- 呼出 ")]
    assert len(calls) == 1 and "make lint" in calls[0]
    assert f"claude:{transcript.stem}:2（失敗1件）:" in calls[0]
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
    """全呼出の入力・失敗・記録位置を個別行へ載せ、ツール結果本文を除く。

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
    call_rows = [line for line in conversation.splitlines() if line.startswith("- 呼出 ")]
    assert len(call_rows) == 4
    for row, (_, tool, tool_input, _, _), line in zip(call_rows, calls, (2, 4, 6, 8), strict=True):
        assert row.startswith(f"- 呼出 {tool} claude:{transcript.stem}:{line}")
        assert json.dumps(tool_input, ensure_ascii=False) in row
    assert "失敗1件" in conversation
    for name in ("read", "pipe", "failed", "saved"):
        assert f"{name}-line-" not in conversation
    assert "Output too large" not in conversation
    locator = f"claude:{transcript.stem}:3"
    assert f"claude:{transcript.stem}:2:" in conversation
    assert len(conversation.encode("utf-8")) <= 100_000
    assert evidence.main([str(transcript), "--detail", locator]) == 0
    assert "read-line-19" in capsys.readouterr().out


def test_prepare_summarizes_codex_tool_activity(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """CodexのCommandExecutionと`exec_command`も入力順・位置・非0終了を個別行へ示す。

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
    calls = [line for line in conversation.splitlines() if line.startswith("- 呼出 ")]
    assert len(calls) == 4
    for row, tool, line, command in zip(
        calls,
        ("CommandExecution", "CommandExecution", "exec_command", "exec_command"),
        (2, 3, 4, 6),
        ("/repo/sed.md", "make check", "rg -n needle /repo", "make lint"),
        strict=True,
    ):
        assert row.startswith(f"- 呼出 {tool} ") and f":{line}" in row and command in row
    assert "失敗1件" in calls[1] and "失敗1件" in calls[3]
    assert "失敗" not in calls[0] and "失敗" not in calls[2]
    assert len(conversation.encode("utf-8")) <= 100_000
    for name in ("sed", "checked", "found", "linted"):
        assert f"{name}-line-" not in conversation
    assert "Chunk ID: abc" not in conversation
