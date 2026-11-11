# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""セッション振り返りの試験（session_review_prepare_test）。"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import session_review_prepare as prepare

from agent_toolkit._common import response_language_check, session_registry
from agent_toolkit._testing import git_repository
from agent_toolkit._testing import session_evidence_support as support
from agent_toolkit._testing.session_review_prepare_support import (
    _BACKGROUND_OUTPUT_NOTICE,
    _FIXED_NOW,
    _work_dir,
    _write_claude_transcript,
)
from agent_toolkit._testing.session_review_prepare_support import _isolate_failure_ledger as _isolate_failure_ledger


@pytest.mark.parametrize("engine", ["claude", "codex", "agy"])
def test_agents_session_id_resolves_conversation(tmp_path, monkeypatch, capsys, engine):
    """登録済み委譲先の識別子だけで、上限付きの会話抜粋へ到達できる。"""
    session_id = "019a0000-1111-7222-8333-444444444444"
    text = "調査した結果。" * 4000
    if engine == "claude":
        home = tmp_path / "claude"
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
        path = home / "projects/project" / f"{session_id}.jsonl"
        entries = [support.timestamped_entry("2026-09-06T12:00:00Z", text)]
    elif engine == "agy":
        # Antigravityの記録はホームディレクトリ配下の会話IDごとのtranscriptから解決される。
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
        monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))
        path = tmp_path / "home/.gemini/antigravity-cli/brain" / session_id / ".system_generated/logs/transcript_full.jsonl"
        entries = [
            {
                "step_index": 0,
                "source": "USER_EXPLICIT",
                "type": "USER_INPUT",
                "status": "DONE",
                "content": f"<USER_REQUEST>\n{text}\n</USER_REQUEST>",
                "created_at": "2026-09-06T12:00:00Z",
            }
        ]
    else:
        home = tmp_path / "codex"
        monkeypatch.setenv("CODEX_HOME", str(home))
        path = home / "sessions/2026/09/06" / f"rollout-2026-09-06T12-00-00-{session_id}.jsonl"
        entries = [
            support.codex_item(
                "2026-09-06T12:00:00Z", {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
            )
        ]
    support.write_jsonl(path, entries)
    session_registry.publish(
        session_id,
        state=session_registry.PublicationState.RESULT_COMPLETED,
        engine=engine,
        cwd=str(tmp_path),
        model="test",
        effort="medium",
    )
    assert (
        prepare.main(
            [
                "--agents-session-id",
                session_id,
                "--work-dir",
                str(_work_dir(tmp_path)),
                "--conversation-only",
                "--conversation-window",
            ],
            now=_FIXED_NOW,
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    conversation = pathlib.Path(result["conversation_path"]).read_bytes()
    assert len(conversation) == result["conversation_utf8_bytes"] <= 16000
    assert "調査した結果" in conversation.decode("utf-8")


@pytest.mark.parametrize("session_id", ["unknown-session", "../invalid"])
def test_agents_session_id_unresolved_returns_next_action(session_id, capsys):
    assert prepare.main(["--agents-session-id", session_id, "--conversation-only", "--conversation-window"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert "次の操作:" in output.err and "atk agents show" in output.err


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
    """両未解決種別の全量を保存し、有限な文書概要から同じ対象へ到達する。"""
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
    assert event["next_action"] and "記録保持者" in event["next_action"]
    saved = (work / "bundle" / "unconfirmed.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in saved.splitlines()] == [event]
    serialized = json.dumps(event, ensure_ascii=False)
    assert len(serialized.encode("utf-8")) > 200
    for key in ("candidates_path", "stats_path"):
        document = pathlib.Path(record[key]).read_text(encoding="utf-8")
        assert "全件はbundle/unconfirmed.jsonlへ保持する" in document
        prefix = f"- {kind}: "
        preview = next(line.removeprefix(prefix) for line in document.splitlines() if line.startswith(prefix))
        assert '"record": ' in preview
        assert preview.endswith("…") and serialized.startswith(preview[:-1])
        assert len(preview.encode("utf-8")) <= 200
    assert record["candidate_total"] == 0 and not record["mandatory_candidates"]


def _git_repository(path: pathlib.Path) -> pathlib.Path:
    return git_repository.init_repository(path, files={"tracked.txt": "tracked\n"}, commit_message="initial")


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
