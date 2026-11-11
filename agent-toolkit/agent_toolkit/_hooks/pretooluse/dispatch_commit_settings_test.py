# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
# pylint: disable=protected-access
"""dispatch_commit_settingsの契約テスト。"""

import json
import pathlib

import pytest

from agent_toolkit._hooks.pretooluse import dispatch as pretooluse
from agent_toolkit._testing.dispatch_support import (
    _claude_commit_hook_payload,
    _claude_commit_payload,
    _denial_reason,
    _write_claude_transcript,
)
from agent_toolkit._testing.dispatch_support import _existing_work_contract as _existing_work_contract

_LOW_TRAILER = "Co-Authored-By: Claude Opus 5.5 / Low <noreply@anthropic.com>"


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(
            "git commit -q -F - <<'EOF'\nfix: 変更\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>\nEOF",
            id="heredoc-without-effort",
        ),
        pytest.param(
            "git commit -q -F - <<'EOF'\nfix: 変更\n\nCo-Authored-By: Claude Opus 5.5 / 10 <noreply@anthropic.com>\nEOF",
            id="heredoc-wrong-effort",
        ),
        pytest.param(
            "git commit -m 'fix: 変更' -m 'Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>'", id="message-option"
        ),
    ],
)
def test_claude_commit_blocks_trailer_without_turn_identity(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path, command: str
) -> None:
    """推論量を欠くか誤った帰属行は、`-F -`のheredocでも遮断し、必要な帰属行を示す。

    `-F -`を読まないと、エージェントのcommitの大半を確かめないまま通し、誤った帰属行がpushされる。
    """
    assert pretooluse.main(json.dumps(_claude_commit_hook_payload(tmp_path, command))) == 0
    assert _LOW_TRAILER in _denial_reason(capsys.readouterr())


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(f"git commit -q -F - <<'EOF'\nfix: 変更\n\n本文\n\n{_LOW_TRAILER}\nEOF", id="heredoc"),
        pytest.param("git commit -F message.txt", id="existing-file"),
        pytest.param(f"git commit -m 'fix: 変更' -m '{_LOW_TRAILER}'", id="message-option"),
        pytest.param("git commit --amend --no-edit", id="amend-no-edit"),
        pytest.param("git commit --fixup HEAD", id="fixup"),
        pytest.param("git commit -F missing.txt", id="missing-file"),
    ],
)
def test_claude_commit_accepts_matching_or_unjudged_messages(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path, command: str
) -> None:
    """正しい帰属行を持つ`-F -`・`-F <既存ファイル>`・`-m`と、判定しない形は遮断しない。"""
    (tmp_path / "message.txt").write_text(f"fix: 変更\n\n{_LOW_TRAILER}\n", encoding="utf-8")

    assert pretooluse.main(json.dumps(_claude_commit_hook_payload(tmp_path, command))) == 0
    assert _denial_reason(capsys.readouterr()) == ""


def test_claude_commit_uses_last_observed_model_after_switching_back(
    capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path
) -> None:
    """turnの途中でモデルを切り替えて戻した記録では、最後に観測したモデルで期待値を求める。

    初出順に重複を除いた一覧の末尾を使うと、戻す前のモデルを期待値にして正しい帰属行を遮断する。
    """
    command = f"git commit -m 'fix: 変更' -m '{_LOW_TRAILER}'"
    payload = _claude_commit_hook_payload(tmp_path, command)
    _write_claude_transcript(
        pathlib.Path(str(payload["transcript_path"])), ["claude-opus-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]
    )

    assert pretooluse.main(json.dumps(payload)) == 0
    assert _denial_reason(capsys.readouterr()) == ""


def test_subagent_commit_uses_the_subagent_record_model(capsys: pytest.CaptureFixture[str], tmp_path: pathlib.Path) -> None:
    """`agent_id`を持つ入力はsubagentの記録のモデルで期待値を求め、記録を解決できなければ遮断しない。

    `transcript_path`はsubagentのhookでもメインの記録を指すため、メインのモデルで期待値を求めると誤って遮断する。
    subagentの記録の配置はClaude Code 2.1.291の`<session_id>/subagents/agent-<agentId>.jsonl`から写した。
    """
    command = "git commit -m 'fix: 変更' -m 'Co-Authored-By: Claude Haiku 4.5 / Low <noreply@anthropic.com>'"
    payload = _claude_commit_hook_payload(tmp_path, command, agent_id="a67c55c5819f1e406")
    transcript = pathlib.Path(str(payload["transcript_path"]))
    _write_claude_transcript(
        transcript.parent / "session-1" / "subagents" / "agent-a67c55c5819f1e406.jsonl", ["claude-haiku-4-5-20251001"]
    )
    assert pretooluse.main(json.dumps(payload)) == 0
    assert _denial_reason(capsys.readouterr()) == ""

    unresolved = _claude_commit_hook_payload(tmp_path, "git commit -m 'fix: 変更'", agent_id="missing-agent")
    assert pretooluse.main(json.dumps(unresolved)) == 0
    assert _denial_reason(capsys.readouterr()) == ""


def test_commit_without_observed_identity_remains_unblocked(capsys: pytest.CaptureFixture[str]) -> None:
    """観測identityが無い入力の帰属を推測せず、repository対象の解決と分ける。"""
    payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m '変更'"}}

    assert pretooluse.main(json.dumps(payload)) == 0
    reason = _denial_reason(capsys.readouterr())
    assert "Co-Authored-By" not in reason
    assert "コミット先のリポジトリを解決できない" in reason


def test_claude_commit_respects_explicit_empty_attribution_setting(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """最上位の観測可能な`attribution.commit`が空文字なら、通常commitを誤遮断しない。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"attribution": {"commit": ""}}),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    payload = _claude_commit_payload(tmp_path, project)

    assert pretooluse.main(json.dumps(payload)) == 0
    assert _denial_reason(capsys.readouterr()) == ""


def test_project_attribution_setting_overrides_user_disable(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """project設定が帰属を再指定した場合は、低優先度のuser空文字を無効化として扱わない。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "settings.json").write_text(
        json.dumps({"attribution": {"commit": ""}}),
        encoding="utf-8",
    )
    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"attribution": {"commit": "Co-Authored-By: {model} / {effort}"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    payload = _claude_commit_payload(tmp_path, project)

    assert pretooluse.main(json.dumps(payload)) == 0
    assert "Co-Authored-By: Claude Opus 5.5 / Medium <noreply@anthropic.com>" in _denial_reason(capsys.readouterr())
