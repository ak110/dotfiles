# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_commit_identityの契約テスト。"""

import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._testing.dispatch_support import _denial_reason, _write_claude_transcript
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract


def test_codex_commit_uses_observed_identity_from_hook_payload(capsys: pytest.CaptureFixture[str]) -> None:
    """Codexの通常commitはhook payloadの観測model・effortに一致するtrailerを要求する。"""
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m '変更'"},
        "turn_id": "codex-turn",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "medium",
    }

    assert pretooluse.main(json.dumps(payload)) == 0
    assert "Co-Authored-By: GPT-6.1 Sol / Medium <noreply@openai.com>" in _denial_reason(capsys.readouterr())


@pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude", "codex"])
@pytest.mark.parametrize(
    "command, code",
    [
        ("git status", 0),
        ("printf '%s' 'git commit -m example'", 0),
        ("git commit --amend --no-edit", 0),
        ("git commit --fixup HEAD", 0),
        ("git commit --squash=HEAD", 0),
        ("git commit -F missing.txt", 0),
    ],
)
def test_bash_without_commit_message_does_not_acquire_attribution_inputs(
    command: str, code: int, extra_payload: dict[str, str], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """対象本文が無ければ、帰属固有のidentity・記録・設定の取得へ進まない。

    書式不正の本文は、全違反を1回の遮断へ並べるため帰属の検査も受ける。
    """

    def unexpected(*_args: object, **_kwargs: object) -> None:
        pytest.fail("帰属の対象が無いBashで依存情報を取得した")

    monkeypatch.setattr(pretooluse, "_hook_observed_identity", unexpected)
    monkeypatch.setattr(pretooluse, "_claude_commit_attribution_disabled", unexpected)
    payload = {"tool_name": "Bash", "tool_input": {"command": command}, "isSidechain": True, **extra_payload}
    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert ("空行" in _denial_reason(captured)) == (code == 2)


@pytest.mark.parametrize("extra_payload", [{}, {"turn_id": "codex-turn"}], ids=["claude", "codex"])
@pytest.mark.parametrize("option", ["-F", "--file", "--file="])
def test_commit_message_file_is_read_once_for_format_and_attribution(
    tmp_path: pathlib.Path,
    option: str,
    extra_payload: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """同じファイルの本文を、書式確認と観測identityの帰属確認で共用する。"""
    message = tmp_path / "message.txt"
    author = (
        "GPT-6.1 Sol / Medium <noreply@openai.com>" if extra_payload else "Claude Opus 5.5 / Medium <noreply@anthropic.com>"
    )
    message.write_text(f"fix: 変更\n\nCo-Authored-By: {author}\n", encoding="utf-8")
    original = pathlib.Path.read_text
    reads = 0

    def read(path: pathlib.Path, *args: Any, **kwargs: Any) -> str:
        nonlocal reads
        if path == message:
            reads += 1
        return original(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", read)
    separator = "" if option.endswith("=") else " "
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"git commit {option}{separator}message.txt"},
        "cwd": str(tmp_path),
        "model": "gpt-6.1-sol" if extra_payload else "claude-opus-5-5",
        "effort": {"level": "medium"},
        "isSidechain": True,
        **extra_payload,
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    capsys.readouterr()
    assert reads == 1


@pytest.mark.parametrize("reader", ["cat --", "sed -n '1,200p'", "sed -n '1,200p' --", "awk '{print}'"])
def test_commit_rules_read_delivers_observed_trailer_and_allows_same_turn_commit(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    reader: str,
) -> None:
    """実ファイルの読取で得た帰属行は同turnの通常commitへ使え、turn変化では現在値と比べる。"""
    rules = tmp_path / "different root" / "skills/commit/references/message.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("帰属規範\n", encoding="utf-8")
    payload = {
        "tool_name": "Bash",
        "turn_id": "codex-turn",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "medium",
        "cwd": str(tmp_path),
        "tool_input": {"command": f"{reader} '{rules}'"},
    }
    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    notice = json.loads(captured.out)["hookSpecificOutput"]["additionalContext"]
    trailer = "Co-Authored-By: GPT-6.1 Sol / Medium <noreply@openai.com>"
    assert trailer in notice and "読取時点" in notice
    assert not captured.err
    payload["tool_input"] = {"command": f"git commit -m 'fix: 変更' -m '{trailer}'"}
    assert pretooluse.main(json.dumps(payload)) == 0
    assert not _denial_reason(capsys.readouterr())
    payload["reasoning_effort"] = "high"
    assert pretooluse.main(json.dumps(payload)) == 0
    assert "GPT-6.1 Sol / High" in _denial_reason(capsys.readouterr())
    for value in ("誤った行", f"{trailer}\n{trailer}"):
        payload["tool_input"] = {"command": f"git commit -m 'fix: 変更' -m '{value}'"}
        assert pretooluse.main(json.dumps(payload)) == 0
        assert "GPT-6.1 Sol / High" in _denial_reason(capsys.readouterr())


@pytest.mark.parametrize("case", ["literal", "search", "other", "missing", "unknown", "claude", "read-tool"])
def test_commit_rules_read_notice_is_limited_to_observed_codex_reads(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    case: str,
) -> None:
    """引用・検索・別資料・未観測・別ホストを帰属配送の読取にしない。"""
    rules = tmp_path / "skills/commit/references/message.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("帰属規範\n", encoding="utf-8")
    other = rules.with_name("other.md")
    other.write_text("別資料\n", encoding="utf-8")
    commands = {
        "literal": f"printf '%s' '{rules}'",
        "search": f"rg -F -- '帰属' '{rules}'",
        "other": f"cat '{other}'",
        "missing": f"cat '{tmp_path}/missing/skills/commit/references/message.md'",
    }
    payload = {
        "tool_name": "Bash",
        "turn_id": "codex-turn",
        "model": "gpt-6.1-sol",
        "reasoning_effort": "medium",
        "cwd": str(tmp_path),
        "tool_input": {"command": commands.get(case, f"cat '{rules}'")},
    }
    if case == "unknown":
        payload.pop("model")
        payload.pop("reasoning_effort")
    if case == "claude":
        payload.pop("turn_id")
    if case == "read-tool":
        payload["tool_name"] = "Read"
        payload["tool_input"] = {"file_path": str(rules)}
    assert pretooluse.main(json.dumps(payload)) == 0
    assert "読取時点" not in capsys.readouterr().out


@pytest.mark.parametrize("host", ["claude-unknown", "claude-disabled", "codex-unknown", "codex-observed"])
@pytest.mark.parametrize("source", ["message", "heredoc", "file"])
def test_commit_format_blocks_independently_of_attribution(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str], host: str, source: str
) -> None:
    (tmp_path / "message.txt").write_text("件名\n本文\n", encoding="utf-8")
    commands = {
        "message": "git commit -m '件名\n本文'",
        "heredoc": "git commit -F - <<'EOF'\n件名\n本文\nEOF",
        "file": "git commit -F message.txt",
    }
    payload = {
        "session_id": "format-case",
        "cwd": str(tmp_path),
        "tool_name": "Bash",
        "tool_input": {"command": commands[source]},
    }
    if host.startswith("codex"):
        payload["turn_id"] = "codex-turn"
    if host == "codex-observed":
        payload.update(model="gpt-6.1-sol", reasoning_effort="medium")
    if host == "claude-disabled":
        (tmp_path / ".claude").mkdir()
        (tmp_path / ".claude/settings.json").write_text('{"attribution":{"commit":""}}', encoding="utf-8")
    assert pretooluse.main(json.dumps(payload)) == 0
    error = _denial_reason(capsys.readouterr())
    assert "空行" in error and "再実行" in error


def test_claude_commit_uses_observed_identity_from_transcript(
    capsys: pytest.CaptureFixture[str],
    tmp_path: pathlib.Path,
) -> None:
    """Claude Codeの通常commitはtranscript中の最後の観測identityへ接続する。"""
    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl", ["claude-opus-5-5"])
    trailer = "Co-Authored-By: Claude Opus 5.5 / Medium <noreply@anthropic.com>"
    payload = {
        "tool_name": "Bash",
        "tool_input": {"command": f"git commit -m '変更' -m '{trailer}'"},
        "transcript_path": str(transcript),
    }

    assert pretooluse.main(json.dumps(payload)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
