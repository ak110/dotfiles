"""発話関連の通知と原本読取失敗が、実際の資料へ引用済みの取得操作を示すことを確かめる。"""

import json
import os
import pathlib
import re
import shlex
import subprocess
import types

import pytest

from agent_toolkit._common.read_triggers import ReadTarget
from agent_toolkit._hooks import antigravity, document_reads, posttooluse, user_prompt_submit
from agent_toolkit._hooks.pretooluse import read_delivery


def _assert_read(body: str, path: pathlib.Path, *, windows: bool) -> None:
    if windows:
        commands = re.findall(r"Get-Content -LiteralPath '((?:''|[^'])*)' -Encoding utf8", body)
        assert str(path) in [value.replace("''", "'") for value in commands]
    else:
        commands = re.findall(r"`(cat -- [^`]+)`", body)
        arguments = [shlex.split(command) for command in commands]
        assert ["cat", "--", str(path)] in arguments
        # 受信側へ示す引数が一つの実ファイルを指し、メタ文字が別の操作へ分かれないことを確かめる。
        if os.name == "posix":
            result = subprocess.run(["cat", "--", str(path)], capture_output=True, check=False, timeout=5)
            assert result.returncode == 0
            assert result.stdout == path.read_bytes()


@pytest.mark.parametrize("windows", [False, True])
def test_unreadable_transcript_separates_recovery_from_hook_retry(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], windows: bool
) -> None:
    path = tmp_path / "原本 ' [$value]; (record).jsonl"
    monkeypatch.setattr(antigravity, "os", types.SimpleNamespace(name="nt" if windows else "posix"))
    assert (
        antigravity.main(
            json.dumps({"conversationId": "notice-transcript", "transcriptPath": str(path)}), event="PreInvocation"
        )
        == 0
    )
    output = capsys.readouterr().err
    path.write_text("{}\n", encoding="utf-8")
    _assert_read(output, path, windows=windows)
    fact, action = output.split("次の操作:", 1)
    assert "hookが再読する" in fact
    assert "PreInvocation" not in action and "Stop" not in action
    assert "確認せよ" in action and "報告せよ" in action


@pytest.mark.parametrize("prompt", ["例えば表示を直して", "例えば設定を調査して", "理解が違う！！"])
def test_content_notice_references_only_the_required_documents(prompt: str) -> None:
    bodies = posttooluse.content_notices(prompt, "notice-acquisition")
    assert len(bodies) == 1
    body = bodies[0]
    if "！！" in prompt:
        assert "`agent-toolkit:user-confirmation-and-report`を起動し" in body
    else:
        assert "`references/user-utterance.md`" in body
    routing = "`references/routing.md`"
    if "調査" in prompt:
        assert routing in body
    else:
        assert routing not in body
    assert "cat --" not in body and "Get-Content" not in body


def test_first_prompt_does_not_warn_before_preparation(capsys: pytest.CaptureFixture[str]) -> None:
    session_id = "notice-confirmation"
    assert user_prompt_submit.main(json.dumps({"session_id": session_id, "prompt": "確認して"})) == 0
    captured = capsys.readouterr()
    assert not captured.out and not captured.err


@pytest.mark.parametrize("prompt", ["通常の依頼", "`例えば調査`は引用"])
def test_acquisition_does_not_add_notice_triggers(prompt: str) -> None:
    assert not posttooluse.content_notices(prompt, "notice-acquisition-negative")


@pytest.mark.parametrize("subject", ["main", "subagent", "codex"])
@pytest.mark.parametrize("loadable", [False, True])
def test_skill_guidance_keeps_unloadable_copy_readable(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, subject: str, loadable: bool
) -> None:
    path = tmp_path / ".claude/skills/local/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("# 固有本文\n", encoding="utf-8")
    monkeypatch.setattr(document_reads, "skill_loadable", lambda *_args, **_kwargs: loadable)
    body = read_delivery.acquisition_step(ReadTarget(path), subject)
    if loadable:
        assert body == "`local`を起動せよ"
        for forbidden in ("Skill(skill=", "cat --", "Get-Content", "Read", "それ以外", "使えない場合"):
            assert forbidden not in body
    else:
        assert str(path) in body and "起動せよ" not in body
