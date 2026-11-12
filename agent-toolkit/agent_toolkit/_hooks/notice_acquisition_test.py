"""発話関連の通知と原本読取失敗が、実際の資料へ引用済みの取得操作を示すことを確かめる。"""

import json
import os
import pathlib
import re
import shlex
import subprocess
import types

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._hooks import antigravity, plugin_resources, posttooluse, user_prompt_submit


@pytest.fixture(name="documents")
def _documents(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "資料 ' [$value]; (root)"
    for relative in (
        "user-confirmation-and-report/SKILL.md",
        "user-confirmation-and-report/references/user-utterance.md",
        "delegation/references/routing.md",
    ):
        path = root / "skills" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("取得対象の本文\n", encoding="utf-8")
    monkeypatch.setattr(plugin_resources, "plugin_root", lambda: root)
    return root


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


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("prompt", ["例えば表示を直して", "例えば設定を調査して", "理解が違う！！"])
def test_content_notice_commands_resolve_only_the_required_documents(
    documents: pathlib.Path, monkeypatch: pytest.MonkeyPatch, windows: bool, prompt: str
) -> None:
    monkeypatch.setattr(posttooluse, "os", types.SimpleNamespace(name="nt" if windows else "posix"))
    bodies = posttooluse.content_notices(prompt, "notice-acquisition")
    assert len(bodies) == 1
    body = bodies[0]
    if "！！" in prompt:
        assert "agent-toolkit:user-confirmation-and-report" in body and "スキルを起動し" in body
    else:
        _assert_read(body, documents / "skills/user-confirmation-and-report/references/user-utterance.md", windows=windows)
        assert "取得せよ" in body or "取得し" in body
    routing = documents / "skills/delegation/references/routing.md"
    if "調査" in prompt:
        _assert_read(body, routing, windows=windows)
    else:
        assert str(routing) not in body


def test_confirmation_notice_asks_to_invoke_the_skill(capsys: pytest.CaptureFixture[str]) -> None:
    session_id = "notice-confirmation"
    session_state.update_state(session_id, lambda state: {**state, "user_confirmation_skill_pending": True})
    assert user_prompt_submit.main(json.dumps({"session_id": session_id, "prompt": "確認して"})) == 0
    body = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert session_state.read_state(session_id)["user_confirmation_skill_pending"] is False
    assert "`agent-toolkit:user-confirmation-and-report`スキルを起動せよ" in body
    assert "cat --" not in body and "Get-Content" not in body
    assert "agent-basics:bugfix" in body


@pytest.mark.parametrize("prompt", ["通常の依頼", "`例えば調査`は引用"])
def test_acquisition_does_not_add_notice_triggers(prompt: str) -> None:
    assert not posttooluse.content_notices(prompt, "notice-acquisition-negative")
