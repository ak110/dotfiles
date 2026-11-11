"""dispatch_testの共通試験準備。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks import work_contract
from agent_toolkit._testing.hook_output_contract import validate_hook_output


def _denial_reason(captured) -> str:
    """stderrを使わず、正常拒否の理由欄を検証する。"""
    assert not captured.err
    response = json.loads(captured.out) if captured.out else {}
    specific = response.get("hookSpecificOutput", {})
    reason = specific.get("permissionDecisionReason", "")
    if reason:
        assert specific["permissionDecision"] == "deny"
    return reason


@pytest.fixture(autouse=True)
def _existing_work_contract(tmp_path, monkeypatch):
    """個別チェックの通知契約を、作業契約を作成済みの状態で検証する。"""
    path = tmp_path / "work-contract.md"
    path.write_text("目的: 個別チェックの検証\n", encoding="utf-8")
    monkeypatch.setattr(work_contract, "record_path", lambda *_args, **_kwargs: path)


def _assert_plain_pass(result, host: str) -> None:
    """資料取得後の通過をホストの公開契約で返す。"""
    assert result.returncode == 0, result.stderr
    assert not result.stderr
    output = json.loads(result.stdout) if result.stdout else {}
    assert not validate_hook_output("PreToolUse", output, host=host)
    if host == "codex":
        assert not output
    else:
        specific = output["hookSpecificOutput"]
        assert specific["permissionDecision"] == "allow"
        assert "additionalContext" not in specific


def _original_record(tmp_path: pathlib.Path, root_kind: str, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """設定したホームの原記録を用意し、個人の記録へアクセスしない。"""
    claude = tmp_path / "claude-config"
    codex = tmp_path / "codex-home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    root = claude / "projects/project" if root_kind == "claude" else codex / "sessions/2026/10/10"
    root.mkdir(parents=True)
    path = root / ("session.jsonl" if root_kind == "claude" else "rollout-session.jsonl")
    path.write_text('{"type":"assistant","message":{"content":[]}}\n', encoding="utf-8")
    return path


def _claude_assistant_line(model: str, effort: str | None = "medium") -> dict[str, object]:
    """Claude Codeの記録のassistant行を返す。

    形はClaude Code 2.1.291の`~/.claude/projects`配下の記録から写した。モデルは`message.model`、推論量は最上位の
    `effort`に置かれ、Claude Codeが合成した応答の行は`message.model`が`<synthetic>`で`effort`がnullになる。
    """
    return {
        "type": "assistant",
        "version": "2.1.291",
        "effort": effort,
        "message": {"model": model, "role": "assistant", "content": [{"type": "text", "text": "応答"}]},
    }


def _write_claude_transcript(path: pathlib.Path, models: list[str]) -> pathlib.Path:
    """モデルを順に記録したClaude Codeの記録を作成する。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(_claude_assistant_line(model)) + "\n" for model in models), encoding="utf-8")
    return path


def _claude_commit_payload(tmp_path: pathlib.Path, project: pathlib.Path) -> dict[str, object]:
    """Claude Codeの観測identityとprojectを持つ通常commit入力を返す。"""
    transcript = _write_claude_transcript(tmp_path / "transcript.jsonl", ["claude-opus-5-5"])
    return {
        "tool_name": "Bash",
        "tool_input": {"command": "git commit -m '変更'"},
        "cwd": str(project),
        "transcript_path": str(transcript),
    }


def _claude_commit_hook_payload(tmp_path: pathlib.Path, command: str, **extra: object) -> dict[str, object]:
    """推論量をhook入力の`effort.level`に持つClaude CodeのPreToolUse入力を返す。

    hook入力の`effort`はClaude Code 2.1.291本体のhook入力の定義（`{level}`）に従う。記録の推論量（medium）と
    異なる値にし、turnへ適用した推論量をhook入力から取ることを確かめる。
    """
    transcript = _write_claude_transcript(tmp_path / "project-records" / "session-1.jsonl", ["claude-opus-5-5"])
    return {
        "session_id": "session-1",
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(tmp_path),
        "transcript_path": str(transcript),
        "effort": {"level": "low"},
        **extra,
    }
