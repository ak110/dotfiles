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
