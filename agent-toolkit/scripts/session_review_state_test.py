"""セッション振り返りの起動済み状態記録CLIを検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import shlex
import shutil
import subprocess

import _fork_runner
import _session_review_evidence
import pytest
import session_review_state

_SCRIPT = pathlib.Path(__file__).resolve().parent / "session_review_state.py"
_PLUGIN_ROOT = _SCRIPT.parent.parent
_SESSION_REVIEW_SKILL = _PLUGIN_ROOT / "skills" / "session-review" / "SKILL.md"
_CODEX_SKILL_SUFFIX = ("skills", "session-review", "SKILL.md")


def _resolve_codex_recording_script(skill_path: pathlib.Path) -> pathlib.Path | None:
    """スキル本文と同じ固定末尾成分の除去と実在確認で記録CLIを解決する。"""
    resolved = skill_path.resolve()
    suffix_length = len(_CODEX_SKILL_SUFFIX)
    if resolved.parts[-suffix_length:] != _CODEX_SKILL_SUFFIX:
        return None
    plugin_root = pathlib.Path(*resolved.parts[:-suffix_length])
    script = plugin_root / "scripts" / "session_review_state.py"
    return script if script.is_file() else None


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
    skill_text = _SESSION_REVIEW_SKILL.read_text(encoding="utf-8")
    script = _resolve_codex_recording_script(_SESSION_REVIEW_SKILL)

    assert "`skills`、`session-review`、`SKILL.md`と完全一致" in skill_text
    assert "固定末尾成分を一組として絶対パスから除き" in skill_text
    assert "対象スクリプトが実在する通常ファイル" in skill_text
    assert script is not None
    assert script == _SCRIPT.resolve()
    result = _fork_runner.run_script(
        script,
        argv=("codex-loaded-skill",),
        env={**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)},
    )

    assert result.returncode == 0
    assert _read_state(tmp_path, "codex-loaded-skill")["session_review_invoked"] == {"agent-toolkit:session-review": True}


def test_codex_rejects_unexpected_skill_suffix_and_missing_script(tmp_path: pathlib.Path) -> None:
    """固定末尾成分の不一致と、導出先スクリプトの不在をいずれも棄却する。"""
    unexpected = tmp_path / "skills" / "other-skill" / "SKILL.md"
    missing_script = tmp_path / "skills" / "session-review" / "SKILL.md"
    missing_script.parent.mkdir(parents=True)
    missing_script.write_text("# test\n", encoding="utf-8")

    assert _resolve_codex_recording_script(unexpected) is None
    assert _resolve_codex_recording_script(missing_script) is None


def test_codex_command_preserves_space_containing_script_path_as_one_argument(tmp_path: pathlib.Path) -> None:
    """文書の引用済みコマンドを解析し、空白入り導入先の記録CLIを実行できる。"""
    plugin_root = tmp_path / "plugin root with spaces"
    scripts_dir = plugin_root / "scripts"
    skill_path = plugin_root / "skills" / "session-review" / "SKILL.md"
    scripts_dir.mkdir(parents=True)
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text(_SESSION_REVIEW_SKILL.read_text(encoding="utf-8"), encoding="utf-8")
    for source in (
        _SCRIPT,
        _SCRIPT.parent / "_session_review_evidence.py",
        _SCRIPT.parent / "_session_state.py",
        _SCRIPT.parent / "_file_lock.py",
    ):
        shutil.copy2(source, scripts_dir / source.name)

    script = _resolve_codex_recording_script(skill_path)
    assert script is not None
    command_template = 'uv run --no-project --script "<plugin root>/scripts/session_review_state.py" <session_id>'
    assert command_template in skill_path.read_text(encoding="utf-8")
    command = command_template.replace("<plugin root>", plugin_root.as_posix()).replace("<session_id>", "space-root")
    argv = shlex.split(command)
    assert pathlib.Path(argv[4]) == script

    env = {**os.environ, "TMPDIR": str(tmp_path), "TEMP": str(tmp_path), "TMP": str(tmp_path)}
    result = subprocess.run(argv, capture_output=True, text=True, check=False, env=env)

    assert result.returncode == 0
    assert _read_state(tmp_path, "space-root")["session_review_invoked"] == {"agent-toolkit:session-review": True}


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
