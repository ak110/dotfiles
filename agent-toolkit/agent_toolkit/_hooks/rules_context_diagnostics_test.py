"""圧縮後の原文抽出失敗が理由を保持することを検証する。"""

import json

from agent_toolkit._hooks import rules_context


def test_compaction_notice_preserves_managed_temp_failure(tmp_path, monkeypatch, capsys):
    """managed-tempを用意できないhook入力でも、原文の手動取得を案内する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")

    def unavailable(*_args, **_kwargs):
        raise OSError("temporary directory unavailable")

    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", unavailable)
    transcript = tmp_path / "transcript.jsonl"
    transcript.touch()
    assert (
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "diagnostic",
                    "transcript_path": str(transcript),
                }
            )
        )
        == 0
    )
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "ValueError: セッションのmanaged-tempを取得できない" in output
    assert "手動で`" in output
    assert "--user-events" in output
    print(output[output.index("ユーザー発話の抽出に失敗した") :].splitlines()[0])


def test_public_start_reports_owned_reclaim_and_repository_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    transcript = tmp_path / "transcript.jsonl"
    transcript.touch()
    error = rules_context.process_tree.ReclaimError(["PID 123"], [], context="原文抽出")
    monkeypatch.setattr(type(error), "retry", lambda self: ["PID 123"])

    def fail(*_args, **_kwargs):
        raise error

    def repository_failure(*_args, **_kwargs):
        raise OSError("repository設定を読めない")

    monkeypatch.setattr(rules_context.process_tree, "run_with_timeout", fail)
    monkeypatch.setattr(rules_context.repository_protection, "start_notice", repository_failure)
    assert (
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "reclaim",
                    "transcript_path": str(transcript),
                }
            )
        )
        == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "PID 123" in context and "所有対象" in context
    assert "ReclaimError: context=原文抽出" in context
    assert "kill -KILL" not in context and "taskkill /PID" not in context
    assert "repository設定を読めない" in context and "直接編集するな" in context


def test_public_start_explains_disabled_background_tasks(monkeypatch, capsys):
    monkeypatch.setattr(rules_context.wait_schedule, "background_tasks_disabled", lambda: True)
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "run_in_background" in context and "待機区間の構成" in context
