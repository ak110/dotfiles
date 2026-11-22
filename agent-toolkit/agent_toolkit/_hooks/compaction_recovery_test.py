"""SessionStartの実記録解決と、既存抽出CLIとの圧縮復元連携を検証する。"""

import argparse
import json
import pathlib
import re
import shlex
import subprocess

import pytest

from agent_toolkit._atk import run_script
from agent_toolkit._hooks import rules_context, user_prompt_submit, work_contract
from agent_toolkit._hooks.pretooluse import operation_skills
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process as run_hook


@pytest.mark.parametrize("candidates", [0, 1, 2])
def test_session_start_resolves_exact_record(
    candidates: int, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再開時の不存在入力は同一IDの実記録だけで解決し、曖昧さを通知する。"""
    config = tmp_path / "claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    found = []
    for index in range(candidates):
        path = config / "projects" / f"project-{index}" / "resume-test.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("", encoding="utf-8")
        found.append(path)
    missing = tmp_path / "missing.jsonl"
    assert (
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": "resume",
                    "session_id": "resume-test",
                    "transcript_path": str(missing),
                }
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    if candidates < 2:
        assert f"--transcript {shlex.quote(str(found[0] if found else missing))}" in output
    else:
        assert "--claude-session-id" in output
        assert str(missing) not in output
        assert all(str(path) not in output for path in found)


def _prepare_from_notice(context: str, destination: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> pathlib.Path:
    command = re.search(r"`(run-script session-review-prepare -- [^`]+)`", context)
    assert command is not None
    argv = shlex.split(command[1])
    destination.mkdir()
    assert (
        run_script.dispatch(argparse.Namespace(script_name=argv[1], script_args=[*argv[3:], "--work-dir", str(destination)]))
        == 0
    )
    return pathlib.Path(json.loads(capsys.readouterr().out)["conversation_path"])


@pytest.mark.parametrize("delegated", [False, True])
def test_compact_extracts_original_user_events(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], delegated: bool
) -> None:
    """複数の圧縮をまたぐ要求の原文を要約へ保持し、生成入力を除く。"""
    path = tmp_path / "directory with spaces" / "transcript.jsonl"
    path.parent.mkdir()
    entries = [
        {"type": "user", "sessionId": "compact-test", "message": {"role": "user", "content": "最初の要求を全部保持する"}},
        {"type": "system", "subtype": "compact_boundary"},
        {"type": "user", "message": {"role": "user", "content": '<atk-auto source="hook" kind="notice">通知</atk-auto>'}},
        {"type": "user", "message": {"role": "user", "content": "範囲を狭めず例も確認する"}},
    ]
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    child = path.with_suffix("") / "subagents" / "agent-broken.jsonl"
    child.parent.mkdir(parents=True)
    child.write_text("{broken child record", encoding="utf-8")
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    else:
        monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    rules_context.main(
        json.dumps(
            {"hook_event_name": "SessionStart", "source": "compact", "session_id": "compact-test", "transcript_path": str(path)}
        )
    )
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert output.count("session-review-prepare") == 1 and "conversation_path" in output
    assert not (tmp_path / "compaction-digest").exists()
    body = _prepare_from_notice(output, tmp_path / "manual", capsys).read_text(encoding="utf-8")
    assert "- ユーザー: 最初の要求を全部保持する\n- ユーザー: 範囲を狭めず例も確認する\n" in body
    assert "atk-auto" not in body


@pytest.mark.parametrize(
    "instructions,retained",
    [
        (["説明を省かず最後まで実装して", "説明は簡潔に訂正する。最後まで実装して"], "説明は簡潔に、実装を完了する"),
        (["計画だけを返して", "計画だけという指示を取り下げる。検証まで進めて"], "検証まで実装する"),
    ],
)
def test_compact_original_read_to_same_session_resume(tmp_path, monkeypatch, capsys, instructions, retained):
    """圧縮後の通知が示す要約の所在と本文から、契約への統合と再開までを接続する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    monkeypatch.setattr(work_contract, "record_path", lambda _session: tmp_path / "work-contract.md")
    monkeypatch.setattr(operation_skills, "notices", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(user_prompt_submit.termination_evidence, "observe_user", lambda *_args: None)
    contract = tmp_path / "work-contract.md"
    contract.write_text("目的: 作業を完了する\n", encoding="utf-8")
    session = "original-resume"
    for prompt in instructions:
        notified = run_hook("user_prompt_submit", {"session_id": session, "prompt": prompt})
        assert notified.returncode == 0
        context = json.loads(notified.stdout)["hookSpecificOutput"]["additionalContext"]
        assert work_contract.short_notice(contract) in context
        assert "目的: 作業を完了する" not in context
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(
                {"type": "user", "sessionId": session, "message": {"role": "user", "content": prompt}}, ensure_ascii=False
            )
            for prompt in instructions
        )
        + "\n",
        encoding="utf-8",
    )
    compact = run_hook(
        "rules_context",
        {"session_id": session, "hook_event_name": "SessionStart", "source": "compact", "transcript_path": str(transcript)},
    )
    assert compact.returncode == 0
    context = json.loads(compact.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "目的: 作業を完了する" in context
    body = _prepare_from_notice(context, tmp_path / "manual", capsys).read_text(encoding="utf-8")
    assert all(f"- ユーザー: {prompt}\n" in body for prompt in instructions)
    # 自然文の採否はエージェントの責務であり、この試験では判定済みの契約を消費側へ渡す。
    contract.write_text(f"目的: 作業を完了する\n現在の指示: {retained}\n", encoding="utf-8")
    restart = run_hook("user_prompt_submit", {"session_id": session, "prompt": "続きを進めて"})
    assert restart.returncode == 0 and retained not in restart.stdout
    assert work_contract.short_notice(contract) in json.loads(restart.stdout)["hookSpecificOutput"]["additionalContext"]


def test_compact_does_not_start_process_or_reuse_previous_digest(tmp_path, monkeypatch, capsys):
    path = tmp_path / "transcript.jsonl"
    path.write_text("", encoding="utf-8")
    saved = tmp_path / "compaction-digest" / "conversation.md"
    saved.parent.mkdir()
    saved.write_text("前の要約", encoding="utf-8")

    def unexpected(*_args, **_kwargs):
        pytest.fail("圧縮後の通知がプロセスを起動した")

    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    payload = {
        "hook_event_name": "SessionStart",
        "source": "compact",
        "session_id": "compact-test",
        "transcript_path": str(path),
    }
    assert rules_context.main(json.dumps(payload)) == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "--conversation-digest" in output and str(saved) not in output
    assert saved.read_text(encoding="utf-8") == "前の要約"


@pytest.mark.parametrize("source", ["startup", "resume", "clear"])
def test_noncompact_session_start_does_not_extract(
    source: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """圧縮以外の開始では、原文抽出の起動と保存を追加しない。"""
    path = tmp_path / "transcript.jsonl"
    path.write_text("{}\n", encoding="utf-8")

    def unexpected(*_args, **_kwargs):
        pytest.fail("圧縮以外の開始で抽出を起動した")

    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    monkeypatch.setattr(subprocess, "Popen", unexpected)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    assert (
        rules_context.main(
            json.dumps(
                {"hook_event_name": "SessionStart", "source": source, "session_id": "noncompact", "transcript_path": str(path)}
            )
        )
        == 0
    )
    assert "--conversation-only" not in capsys.readouterr().out
    assert not (tmp_path / "compaction-digest").exists()


def test_resolved_resume_notice_path_is_accepted_by_prepare(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """再開通知が解決した実パスだけを、公開prepareへ渡して会話を取得する。"""
    config = tmp_path / "claude"
    path = config / "projects/old-directory/resume-test.jsonl"
    path.parent.mkdir(parents=True)
    entry = {
        "type": "user",
        "sessionId": "resume-test",
        "cwd": str(tmp_path),
        "message": {"role": "user", "content": "この会話の原文を保持する"},
    }
    path.write_text(json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *args, **kwargs: tmp_path)
    rules_context.main(
        json.dumps(
            {
                "hook_event_name": "SessionStart",
                "source": "resume",
                "session_id": "resume-test",
                "transcript_path": str(tmp_path / "missing.jsonl"),
            }
        )
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    command = next(
        match
        for match in re.findall(r"`([^`\n]*run-script session-review-evidence[^`\n]*)`", context)
        if "--transcript" in shlex.split(match)
    )
    arguments = shlex.split(command)
    notified = arguments[arguments.index("--transcript") + 1]
    destination = tmp_path / "conversation"
    destination.mkdir()
    assert (
        run_script.dispatch(
            argparse.Namespace(
                script_name="session-review-prepare",
                script_args=["--transcript", notified, "--work-dir", str(destination), "--conversation-only"],
            )
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    conversation = pathlib.Path(prepared["conversation_path"]).read_text(encoding="utf-8")
    assert prepared["conversation_utf8_bytes"] == len(conversation.encode("utf-8"))
    assert prepared["conversation_limit_bytes"] == 100_000
    assert "この会話の原文を保持する" in conversation


# test-copy-contract: 原本パス不明のcompact入力から会話IDを照会CLIへ渡し、別会話の原文を取得しない回復契約。
def test_compact_without_resolved_path_has_executable_id_lookup(tmp_path, monkeypatch, capsys):
    """パスを取得できない場合も、同じ圧縮後通知で実IDを渡す照会を案内する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args, **_kwargs: tmp_path)
    rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "unresolved"}))
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "run-script session-review-evidence -- --claude-session-id unresolved --user-events`" in output
    assert "作業契約と取得したユーザー発話の原文を読め" in output
    assert "conversation_path" not in output
