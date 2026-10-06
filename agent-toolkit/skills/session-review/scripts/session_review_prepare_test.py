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

import pytest
import session_review_prepare as prepare  # noqa: E402  # pylint: disable=wrong-import-position,import-error

from agent_toolkit._hooks import response_language_check

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)
_LONG_INTERVENTION = "そうじゃなくて、対象は全部です。" + "理由の説明。" * 400 + "最後まで読んで。"
_BACKGROUND_OUTPUT_NOTICE = (
    '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">'
    "未完了のバックグラウンドタスクが書き込む出力ファイルを読み取った。完了通知を受けてから読むこと。</agent-toolkit-auto-inserted>"
)


@pytest.fixture(autouse=True)
def _isolate_failure_ledger(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """失敗署名の発生記録をテストごとの状態ディレクトリへ閉じる。"""
    monkeypatch.setattr(prepare._atk_config, "state_dir", lambda: tmp_path / "state")  # pylint: disable=protected-access


def _work_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    return work_dir


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
        '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">'
        f"{response_language_check.BLOCK_BODY}{response_language_check.WARNING_BODY}"
        "\nこの通知は同一セッションで3件目である。</agent-toolkit-auto-inserted>"
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
    assert "  - 記録位置: claude:22222222-3333-4444-5555-666666666666:6" in candidates
    assert "累計2回以上" not in candidates


def _git_repository(path: pathlib.Path) -> pathlib.Path:
    path.mkdir()
    subprocess.run(["git", "-C", str(path), "init"], check=True, capture_output=True)
    (path / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(path), "add", "tracked.txt"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"],
        check=True,
        capture_output=True,
    )
    return path


def test_prepare_writes_conversation_candidates_and_stats_without_queue_changes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """1回の実行で3つの文書を作業ディレクトリへ書き、所在と件数を1行JSONで返し、キューを変更しない。

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
    # ツール呼び出しは発話と区別できるリスト行で、失敗したツール結果は呼び出しの直後に診断の1行で載せる。
    assert "- ツール呼び出し（main:2）: Bash make lint\n  - 失敗（main:4）: Error: 検査に失敗した\n" in conversation
    assert "hook_additional_context" not in conversation
    # 1000字を超える介入は先頭と末尾だけを載せ、全文を照会する記録位置を示す。
    assert _LONG_INTERVENTION not in conversation
    assert _LONG_INTERVENTION[:500] in conversation
    assert _LONG_INTERVENTION[-500:] in conversation
    assert "全文は記録位置main:5" in conversation
    assert f"atk run-script session-review-evidence -- {transcript.resolve()} --detail <記録位置>" in conversation

    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert "- 候補: 2件（hook-notice 1件、user-intervention 1件）" in candidates
    assert "  - 記録位置: claude:11111111-2222-3333-4444-555555555555:3" in candidates
    assert "## 単発の失敗（件数のみ）" in candidates
    assert "make lint" in candidates
    # hook通知の是非は通知が判定した応答を読まないと判断できないため、直前のアシスタント発話を添える。
    assert "  - 直前のアシスタント発話: I will run the linter now." in candidates
    # ユーザーの是正は要約すると趣旨が変わるため全文を載せる。
    assert _LONG_INTERVENTION in candidates
    assert "直前と直後のユーザー発話" not in candidates

    stats = pathlib.Path(record["stats_path"]).read_text(encoding="utf-8")
    assert "- 経過秒: 60秒（セッションの最初の記録から準備時点まで）" in stats
    assert not list(work_dir.glob("*material*"))


def test_prepare_excludes_answer_that_selects_offered_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """選択肢のlabelをそのまま選び自由記述の無い確認回答を、ユーザー介入候補ではなく確認回答として数える。

    候補に残すと、振り返りを行う主体が是正を含まない回答まで介入として分析し、実際の介入件数を過大に扱う。
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
    assert record["excluded_counts"]["question-answer"] == 1
    assert "既存機構へ統合" not in pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")


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

    振り返りは会話の流れからこの行を全件拾うため、省略区間で行が消えると、作業中に伝えた改善の機会を振り返りが分析できない。
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

    conversation = pathlib.Path(json.loads(capsys.readouterr().out)["conversation_path"]).read_text(encoding="utf-8")
    assert report not in conversation
    omitted_marker = "全文は記録位置main:2）…\n"
    assert f"{omitted_marker}{first_note}\n{second_note.strip()}\n" in conversation
    assert filler not in conversation
    # 行を持たない長い発話は、省略の標識の直後に末尾の本文が続く。
    assert f"全文は記録位置main:3）…\n{_LONG_INTERVENTION[-500:]}" in conversation


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
    assert "- ツール呼び出し（main:2）: exec_command ls docs" in conversation
    assert "- ツール呼び出し（main:4）: apply_patch docs/a.md" in conversation
    assert "  - 失敗（main:5）: rg: 失敗" in conversation
    assert "成功した出力の本文" not in conversation and "書き込む本文" not in conversation
    assert "候補として残す問題は無かった。" in pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")


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
    assert "`--work-dir`" in captured.err.splitlines()[1].removeprefix("次の操作: ")

    monkeypatch.setattr(prepare, "__file__", str(tmp_path / "detached" / "session_review_prepare.py"))
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.splitlines()[0] == "不足: evidence_script"
    assert captured.err.splitlines()[1].startswith("次の操作: `atk run-script session-review-prepare --")
