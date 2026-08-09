"""セッション振り返りの起動済み状態記録CLIを検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import _fork_runner
import _session_review_evidence
import pytest
import session_review_state

_SCRIPT = pathlib.Path(__file__).resolve().parent / "session_review_state.py"
_SESSION_REVIEW_SKILL = _SCRIPT.parents[1] / "skills" / "session-review" / "SKILL.md"


def _run(session_id: str, state_dir: pathlib.Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TMPDIR"] = str(state_dir)
    env["TEMP"] = str(state_dir)
    env["TMP"] = str(state_dir)
    return _fork_runner.run_script(_SCRIPT, argv=(session_id,), env=env)


def _read_state(state_dir: pathlib.Path, session_id: str) -> dict:
    path = state_dir / f"claude-agent-toolkit-{session_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_records_session_review_idempotently(tmp_path: pathlib.Path) -> None:
    first = _run("review-session", tmp_path)
    second = _run("review-session", tmp_path)

    assert first.returncode == 0
    assert second.returncode == 0
    assert first.stdout.strip() == _session_review_evidence.SESSION_REVIEW_STARTED_MARKER
    assert second.stdout.strip() == _session_review_evidence.SESSION_REVIEW_STARTED_MARKER
    assert _read_state(tmp_path, "review-session")["session_review_invoked"] == {"agent-toolkit:session-review": True}


def test_codex_resolves_executable_from_loaded_skill_path(tmp_path: pathlib.Path) -> None:
    """Codexが読み込んだSKILL.mdから実在する記録CLIを解決して実行できる。"""
    plugin_root = _SESSION_REVIEW_SKILL.resolve().parents[2]
    script = plugin_root / "scripts" / "session_review_state.py"

    assert script == _SCRIPT.resolve()
    result = _fork_runner.run_script(
        script,
        argv=("codex-loaded-skill",),
        env={**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )

    assert result.returncode == 0
    assert _read_state(tmp_path, "codex-loaded-skill")["session_review_invoked"] == {"agent-toolkit:session-review": True}


def test_rejects_empty_session_id_without_marker(tmp_path: pathlib.Path) -> None:
    result = _run("", tmp_path)

    assert result.returncode != 0
    assert result.stdout == ""
    assert _session_review_evidence.SESSION_REVIEW_STARTED_MARKER not in result.stderr


def test_write_failure_does_not_emit_marker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_update(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(session_review_state, "update_state", fail_update)

    assert session_review_state.main(["write-failure"]) == 1
    assert capsys.readouterr().out == ""
