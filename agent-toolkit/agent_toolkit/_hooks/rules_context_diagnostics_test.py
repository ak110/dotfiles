"""SessionStartが復元操作と周辺の診断を通知することを検証する。"""

import json

from agent_toolkit._hooks import rules_context


def test_compaction_notice_preserves_managed_temp_failure(tmp_path, monkeypatch, capsys):
    """managed-tempを用意できないhook入力でも、要約の手動取得を案内する。"""
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
    assert "起動部分へ`run-script session-review-prepare -- " in output
    assert "--conversation-digest`を続けて実行し" in output
    assert "上限超過時はコマンドが示す上限で再実行" in output


def test_compaction_preserves_repository_failure(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)

    def repository_failure(*_args, **_kwargs):
        raise OSError("repository設定を読めない")

    monkeypatch.setattr(rules_context.repository_protection, "start_notice", repository_failure)
    assert (
        rules_context.main(
            json.dumps({"hook_event_name": "SessionStart", "source": "compact", "session_id": "repository-failure"})
        )
        == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "repository設定を読めない" in context and "直接編集するな" in context


def test_public_start_explains_disabled_background_tasks(monkeypatch, capsys):
    monkeypatch.setattr(rules_context.wait_schedule, "background_tasks_disabled", lambda: True)
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "run_in_background" in context and "`timeout=600000`" in context
