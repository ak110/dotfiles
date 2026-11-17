"""圧縮後の原文抽出失敗が理由を保持することを検証する。"""

import json
import subprocess

import pytest

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
    assert "ValueError: セッションのmanaged-tempを取得できない" in output
    assert "起動部分へ`run-script session-review-prepare -- " in output
    assert "--conversation-digest`を続けて実行し" in output
    print(output[output.index("会話の要約の作成に失敗した") :].splitlines()[0])


@pytest.mark.parametrize(
    ("stderr", "raised_limit"),
    [
        ("不足: bundle\n会話の要約が予算を超えた。次の操作: --conversation-max-bytesを増やして再実行する\n", True),
        ("不足: transcript_path\n", False),
    ],
)
def test_compaction_notice_passes_child_cause_and_effective_retry(tmp_path, monkeypatch, capsys, stderr, raised_limit):
    """要約の子プロセスが失敗した原因を通知へ渡し、予算の超過では上限を上げた再実行を示す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    transcript = tmp_path / "transcript.jsonl"
    transcript.touch()

    def fail(command, **_kwargs):
        raise subprocess.CalledProcessError(2, command, output="", stderr=stderr)

    monkeypatch.setattr(rules_context.process_tree, "run_with_timeout", fail)
    assert (
        rules_context.main(
            json.dumps(
                {
                    "hook_event_name": "SessionStart",
                    "source": "compact",
                    "session_id": "child-failure",
                    "transcript_path": str(transcript),
                }
            )
        )
        == 0
    )
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    failure = context[context.index("会話の要約の作成に失敗した") :].splitlines()[0]
    assert "終了コード2: " + " ".join(stderr.split()) in failure
    assert ("--conversation-digest --conversation-max-bytes 60000`" in failure) is raised_limit
    assert ("--conversation-digest`" in failure) is not raised_limit


def test_public_start_reports_owned_reclaim_and_repository_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("AGENT_TOOLKIT_OWNER_SESSION", raising=False)
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_SESSION_REVIEW_AUTO_START", "false")
    monkeypatch.setattr(rules_context.managed_temp, "resolve_session_temp", lambda *_args: tmp_path)
    transcript = tmp_path / "transcript.jsonl"
    transcript.touch()
    stale = tmp_path / "compaction-digest" / "conversation.md"
    stale.parent.mkdir()
    stale.write_text("前の圧縮の要約", encoding="utf-8")
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
    # 子プロセスを回収できなかった場合も、前の圧縮の要約を今回の復元材料として残さない。
    assert not stale.exists()
    assert "kill -KILL" not in context and "taskkill /PID" not in context
    assert "repository設定を読めない" in context and "直接編集するな" in context


def test_public_start_explains_disabled_background_tasks(monkeypatch, capsys):
    monkeypatch.setattr(rules_context.wait_schedule, "background_tasks_disabled", lambda: True)
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup"})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "run_in_background" in context and "待機区間の構成" in context
