"""振り返りの入力（会話の流れ、問題候補の一覧、セッション統計）を書く準備スクリプトを、公開入口から利用シナリオで検証する。"""

from __future__ import annotations

import datetime
import json
import pathlib
import subprocess

import pytest
import session_review_prepare as prepare  # noqa: E402  # pylint: disable=wrong-import-position,import-error

_FIXED_NOW = datetime.datetime(2026, 9, 6, 12, 34, 56, tzinfo=datetime.UTC)
_LONG_INTERVENTION = "そうじゃなくて、対象は全部です。" + "理由の説明。" * 400 + "最後まで読んで。"
_LANGUAGE_NOTICE = (
    '<agent-toolkit-auto-inserted source="agent-toolkit/pretooluse" kind="warn">'
    "直前のアシスタント応答の地の文が英語主体と判定された。次の応答は日本語で書くこと。</agent-toolkit-auto-inserted>"
)


def _work_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    return work_dir


def _write_claude_transcript(tmp_path: pathlib.Path) -> pathlib.Path:
    """初期要求、英語の状況説明と言語判定の通知、失敗したコマンド、2000文字を超えるユーザー介入を持つtranscriptを書き込む。"""
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
                "content": [_LANGUAGE_NOTICE],
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

    振り返りはメインが同じセッション内で分析するため、準備スクリプトがAWIを投入するとキューへ未分析の項目が残る。
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
    assert record["candidate_counts"] == {"hook-notice": 1, "tool-failure": 1, "user-intervention": 1}
    assert record["candidate_total"] == 3
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
    assert "Error: 検査に失敗した" not in conversation
    # 1000字を超える介入は先頭と末尾だけを載せ、全文を照会する記録位置を示す。
    assert _LONG_INTERVENTION not in conversation
    assert _LONG_INTERVENTION[:500] in conversation
    assert _LONG_INTERVENTION[-500:] in conversation
    assert "全文は記録位置main:5" in conversation
    assert f"atk run-script session-review-evidence -- {transcript.resolve()} --detail <記録位置>" in conversation

    candidates = pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")
    assert "- 候補: 3件（hook-notice 1件、tool-failure 1件、user-intervention 1件）" in candidates
    assert "  - 記録位置: main:3" in candidates
    assert "  - 対象: Bash make lint" in candidates
    # hook通知の是非は通知が判定した応答を読まないと判断できないため、直前のアシスタント発話を添える。
    assert "  - 直前のアシスタント発話: I will run the linter now." in candidates
    # 利用者の是正は要約すると趣旨が変わるため全文を載せる。
    assert _LONG_INTERVENTION in candidates
    assert "直前と直後のユーザー発話" not in candidates

    stats = pathlib.Path(record["stats_path"]).read_text(encoding="utf-8")
    assert "- 経過秒: 60秒（セッションの最初の記録から準備時点まで）" in stats
    assert not list(work_dir.glob("*material*"))


def test_prepare_reads_codex_thread(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexのthread IDからも同じ文書を書き、全文を照会するコマンドをthread IDの形で示す。"""
    codex_home = tmp_path / "codex-home"
    rollout_dir = codex_home / "sessions" / "2026" / "09" / "06"
    rollout_dir.mkdir(parents=True)
    thread_id = "019900aa-bbbb-7ccc-8ddd-eeeeeeeeeeee"
    (rollout_dir / f"rollout-test-{thread_id}.jsonl").write_text(
        json.dumps(
            {
                "timestamp": "2026-09-06T12:00:00Z",
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "依頼"}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CODEX_HOME", str(codex_home))

    exit_code = prepare.main(["--codex-thread-id", thread_id, "--work-dir", str(_work_dir(tmp_path))], now=_FIXED_NOW)

    assert exit_code == 0, capsys.readouterr().err
    record = json.loads(capsys.readouterr().out)
    assert record["candidate_total"] == 0
    assert record["utterance_counts"] == {"user": 1, "assistant": 0}
    conversation = pathlib.Path(record["conversation_path"]).read_text(encoding="utf-8")
    assert f"--codex-thread-id {thread_id} --detail <記録位置>" in conversation
    assert "抽出器が問題候補を返さなかった。" in pathlib.Path(record["candidates_path"]).read_text(encoding="utf-8")


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
    """記録、作業ディレクトリ又は抽出器の同一性が成立しない場合は、不足項目を返して標準出力を空に保つ。"""
    transcript = _write_claude_transcript(tmp_path)
    work_dir = _work_dir(tmp_path)

    assert prepare.main(["--transcript", str(tmp_path / "missing.jsonl"), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "不足: transcript_path\n")

    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(tmp_path / "absent")]) == 2
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "不足: work_dir\n")

    monkeypatch.setattr(prepare, "__file__", str(tmp_path / "detached" / "session_review_prepare.py"))
    assert prepare.main(["--transcript", str(transcript), "--work-dir", str(work_dir)]) == 2
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "不足: evidence_script\n")
