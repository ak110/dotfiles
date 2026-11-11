"""commandsのlogsの契約を検証する。"""

from __future__ import annotations

# テストでは共有状態とバックエンドの内部境界も直接検証する。
# pylint: disable=protected-access
import json
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._agents_server import commands, shared_layout
from agent_toolkit._atk import session_record_format
from agent_toolkit._common import state_paths
from agent_toolkit._common.next_action import NEXT_ACTION_PREFIX
from agent_toolkit._testing.agents_server_commands_support import _LONG_REPLY, _write_jsonl, _write_mixed_claude_record


def test_agents_logs_reads_claude_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """一覧の識別子から既存のClaude Code記録を時系列で読める。"""
    project = tmp_path / "projects" / "sample"
    project.mkdir(parents=True)
    (project / "session-1.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:00Z",
                "message": {"content": [{"type": "text", "text": "調査して"}]},
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1"])

    assert "[2026-09-24T00:00:00Z] user: 調査して" in capsys.readouterr().out


def test_agents_logs_markdown_keeps_turns_and_tool_result_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """公開CLIがClaudeのメタデータ、会話、思考、ツール結果を同じ形式で出力する。"""
    path = tmp_path / "projects" / "sample" / "session-1.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:00Z",
                "sessionId": "session-1",
                "cwd": "/work/sample",
                "gitBranch": "develop",
                "slug": "調査記録",
                "message": {"content": [{"type": "text", "text": "調査して"}]},
            },
            {
                "type": "assistant",
                "timestamp": "2026-09-24T00:00:01Z",
                "message": {
                    "content": [
                        {"type": "text", "text": "調べます"},
                        {"type": "thinking", "thinking": "確認中"},
                        {"type": "tool_use", "id": "toolu_a", "name": "Bash", "input": {"command": "echo hi"}},
                    ]
                },
            },
            {
                "type": "user",
                "timestamp": "2026-09-24T00:00:02Z",
                "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_a", "content": [{"text": "hi"}]}]},
            },
        ],
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1", "--format", "markdown"])
    output = capsys.readouterr().out
    assert "# Session: 調査記録" in output
    assert "| セッションID | session-1 |" in output
    assert "| ブランチ | develop |" in output
    assert "| 期間 | 2026-09-24 00:00:00 UTC 〜 2026-09-24 00:00:02 UTC |" in output
    assert "## Human\n\n調査して" in output
    assert "## Assistant\n\n調べます" in output
    assert "<summary>Tool: Bash" in output
    assert "**Result:**\n\n```\nhi\n```" in output
    assert "<summary>Thinking</summary>" not in output

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1", "--format", "markdown", "--include-thinking", "--no-tool-details"])
    concise = capsys.readouterr().out
    assert "<summary>Thinking</summary>" in concise
    assert "> Tool: Bash" in concise
    assert "**Result:**" not in concise


def test_agents_logs_markdown_renders_codex_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexの公開記録もClaudeと同じ見出し・表・ツール詳細で出力する。"""
    thread_id = "55555555-5555-4555-8555-555555555555"
    path = tmp_path / "sessions" / "2026" / "09" / "24" / f"rollout-2026-09-24T00-00-00-{thread_id}.jsonl"
    _write_jsonl(
        path,
        [
            {
                "type": "session_meta",
                "timestamp": "2026-09-24T00:00:00Z",
                "payload": {"cwd": "/work/codex", "timestamp": "2026-09-24T00:00:00Z", "git": {"branch": "develop"}},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:01Z",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "調査して"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:02Z",
                "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "調べます"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:03Z",
                "payload": {
                    "type": "function_call",
                    "name": "shell",
                    "call_id": "call-1",
                    "arguments": '{"command": "echo hi"}',
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-24T00:00:04Z",
                "payload": {"type": "function_call_output", "call_id": "call-1", "output": "hi"},
            },
        ],
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", thread_id, "--format", "markdown"])
    output = capsys.readouterr().out
    assert "| プロジェクト | /work/codex |" in output
    assert "| ブランチ | develop |" in output
    assert "## Human\n\n調査して" in output
    assert "## Assistant\n\n調べます" in output
    assert "<summary>Tool: shell</summary>" in output
    assert "**Result:**\n\n```\nhi\n```" in output


def test_agents_logs_reads_subagent_and_appends_it_to_parent_markdown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """サブエージェントのIDからも親の記録からも同じ保存記録へ到達する。"""
    parent = tmp_path / "projects" / "sample" / "parent.jsonl"
    _write_jsonl(parent, [{"type": "user", "timestamp": "2026-09-24T00:00:00Z", "message": {"content": "親の依頼"}}])
    child_id = "agent-a7157f79d55caab81"
    child = parent.with_suffix("") / "subagents" / f"{child_id}.jsonl"
    _write_jsonl(child, [{"type": "user", "timestamp": "2026-09-24T00:00:01Z", "message": {"content": "子の依頼"}}])
    child.with_name(f"{child_id}.meta.json").write_text(
        json.dumps({"description": "記録調査", "agentType": "Explore"}), encoding="utf-8"
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", child_id])
    assert "子の依頼" in capsys.readouterr().out

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "parent", "--format", "markdown", "--include-subagents"])
    output = capsys.readouterr().out
    assert "## Subagent: 記録調査" in output
    assert "Type: Explore" in output
    assert "### Human\n\n子の依頼" in output


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["one", "--all"],
        ["one", "--project-dir", "/work"],
        ["one", "--latest", "1"],
        ["one", "--format", "markdown", "--follow"],
        ["--all", "--format", "markdown", "--follow"],
        ["one", "--include-thinking"],
    ],
)
def test_agents_logs_rejects_incompatible_scopes(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    """対象の排他、件数、追尾とmarkdown専用オプションの受理条件を確かめ、受理される指定を次の操作で示す。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "logs", *argv])
    error = capsys.readouterr().err
    assert "error:" in error
    assert f"\n{NEXT_ACTION_PREFIX}" in error


def test_agents_logs_bulk_export_filters_projects_and_preserves_existing_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ClaudeとCodexを全件変換し、同じ日時から作るファイル名の衝突と既存ファイルを安全に扱う。"""
    project = tmp_path / "work" / "sample"
    encoded = str(project).replace("/", "-").replace(".", "-")
    claude_root = tmp_path / "claude" / "projects" / encoded
    for name in ("claude-a", "claude-b"):
        _write_jsonl(
            claude_root / f"{name}.jsonl",
            [{"type": "user", "timestamp": "2026-09-24T00:00:00Z", "cwd": str(project), "message": {"content": name}}],
        )
    for day, cwd in (("25", str(project)), ("26", "/other")):
        thread_id = f"55555555-5555-4555-8555-5555555555{day}"
        path = tmp_path / "codex" / "sessions" / "2026" / "09" / day / f"rollout-2026-09-{day}T00-00-00-{thread_id}.jsonl"
        _write_jsonl(
            path,
            [
                {"type": "session_meta", "timestamp": f"2026-09-{day}T00:00:00Z", "payload": {"cwd": cwd}},
                {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"text": cwd}]}},
            ],
        )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
    output_dir = tmp_path / "exports"

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "--all", "--format", "markdown", "--output-dir", str(output_dir)])
    assert len(list(output_dir.glob("*.md"))) == 4
    assert (output_dir / "20260924_000000.md").is_file()
    assert len(list(output_dir.glob("20260924_000000_*.md"))) == 1
    first_contents = {path.name: path.read_text(encoding="utf-8") for path in output_dir.glob("*.md")}
    capsys.readouterr()

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "--project-dir", str(project), "--latest", "1", "--format", "markdown"])
    selected = capsys.readouterr().out
    assert "/work" in selected
    assert "2026-09-25" in selected
    assert "2026-09-26" not in selected
    assert "claude-a" not in selected

    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "logs", "--all", "--format", "markdown", "--output-dir", str(output_dir)])
    assert "--output-dir" in capsys.readouterr().err.split(NEXT_ACTION_PREFIX, 1)[1]
    assert {path.name: path.read_text(encoding="utf-8") for path in output_dir.glob("*.md")} == first_contents


def test_agents_logs_reports_missing_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """存在しない記録は識別子を添えて報告し、一覧で識別子を確かめる操作を示す。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    for argv in (["missing"], ["missing", "--format", "markdown"]):
        with pytest.raises(SystemExit, match="2"):
            atk.main(["agents", "logs", *argv])

        error = capsys.readouterr().err
        assert "missing" in error
        assert "atk agents list --include-terminated" in error.split(NEXT_ACTION_PREFIX, 1)[1]


def test_agents_logs_shows_first_of_ambiguous_codex_records(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexの記録が複数一致する識別子でも、従来どおり先頭の記録を表示する。"""
    thread_id = "55555555-5555-4555-8555-555555555555"
    for day, text in (("01", "先頭の記録"), ("02", "後続の記録")):
        rollout = tmp_path / "sessions" / "2026" / "09" / day / f"rollout-2026-09-{day}T00-00-00-{thread_id}.jsonl"
        rollout.parent.mkdir(parents=True)
        rollout.write_text(
            json.dumps(
                {
                    "type": "response_item",
                    "timestamp": f"2026-09-{day}T00:00:00Z",
                    "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path / "state")

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", thread_id])

    output = capsys.readouterr().out
    assert "先頭の記録" in output
    assert "後続の記録" not in output


def test_agents_logs_reads_and_follows_antigravity_events(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Antigravityの保存済み出力と、その後に追記された行を順に表示する。"""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr(state_paths, "state_dir", lambda: tmp_path)
    path = shared_layout.session_log_path("root-1", "agy-1", tmp_path)
    path.parent.mkdir(parents=True)
    records = [
        {"event": "init", "conversation_id": "agy-1"},
        {
            "event": "step_update",
            "step_update": {
                "conversation_id": "agy-1",
                "step_index": 1,
                "step_type": "agent_response",
                "state": "DONE",
                "text_delta": "保存済みの応答",
            },
        },
    ]
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    sleeps = 0

    def append_then_stop(_seconds: float) -> None:
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(
                    json.dumps(
                        {
                            "event": "step_update",
                            "step_update": {
                                "conversation_id": "agy-1",
                                "step_index": 2,
                                "step_type": "agent_response",
                                "state": "DONE",
                                "text_delta": "調査中",
                            },
                        }
                    )
                    + "\n"
                )
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr(commands.time, "sleep", append_then_stop)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "agy-1", "--follow"])

    captured = capsys.readouterr()
    assert captured.out == "[-] assistant: 保存済みの応答\n\n[-] assistant: 調査中\n\n"
    assert not captured.err
    assert sleeps == 2


@pytest.mark.parametrize("argv", [["session-1"], ["--all"]], ids=["single", "bulk"])
def test_logs_text_default_follows_conversation_with_blank_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    """`--verbose`を付けないtext表示は振り返りの通常表示と同じ基準で会話の流れを要約し、項目の間に空行を1行置く。

    ツール呼び出し・成功したツール結果・思考を除き、失敗したツール結果は残し、長い本文を同じ上限で切り詰める。
    """
    _write_mixed_claude_record(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", *argv])

    output = capsys.readouterr().out
    clipped = "長" * session_record_format.SUMMARY_TEXT_LIMIT + session_record_format.OMISSION_MARK
    items = [
        "[2026-09-24T00:00:00Z] user: 調査して",
        "[2026-09-24T00:00:03Z] tool_result: 失敗した出力",
        f"[2026-09-24T00:00:04Z] assistant: {clipped}",
    ]
    assert output.endswith("\n\n".join(items) + "\n\n")
    for omitted in ("思考の本文", "tool_call", "成功した出力", _LONG_REPLY):
        assert omitted not in output


def test_logs_verbose_keeps_current_events_with_blank_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--verbose`は全イベントを全文で表示し、付けない場合と同じく項目の間に空行を1行置く。"""
    _write_mixed_claude_record(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1", "--verbose"])

    items = [
        "[2026-09-24T00:00:00Z] user: 調査して",
        "[2026-09-24T00:00:01Z] thinking: 思考の本文",
        "[2026-09-24T00:00:01Z] tool_call: Bash",
        "[2026-09-24T00:00:02Z] tool_result: 成功した出力",
        "[2026-09-24T00:00:03Z] tool_result: 失敗した出力",
        f"[2026-09-24T00:00:04Z] assistant: {_LONG_REPLY}",
    ]
    assert capsys.readouterr().out == "\n\n".join(items) + "\n\n"


def test_logs_follow_uses_conversation_summary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--follow`も`--verbose`を付けない場合は要約で表示し、項目の間に空行を1行置く。"""
    _write_mixed_claude_record(tmp_path)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))

    def stop(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(commands.time, "sleep", stop)

    with pytest.raises(SystemExit, match="0"):
        atk.main(["agents", "logs", "session-1", "--follow"])

    output = capsys.readouterr().out
    assert output.startswith("[2026-09-24T00:00:00Z] user: 調査して\n\n[2026-09-24T00:00:03Z] tool_result: 失敗した出力\n\n")
    assert "思考の本文" not in output and "成功した出力" not in output


def test_logs_verbose_rejects_markdown(capsys: pytest.CaptureFixture[str]) -> None:
    """内容の制御オプションと同じく、`--verbose`とmarkdownの併用は次の操作付きの引数エラーにする。"""
    with pytest.raises(SystemExit, match="2"):
        atk.main(["agents", "logs", "session-1", "--format", "markdown", "--verbose"])
    error = capsys.readouterr().err
    assert "--verboseはtext形式でだけ使える" in error
