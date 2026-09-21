"""登録済みplugin scriptの起動を検証する。"""

import argparse
import json
import pathlib
import sys

import pytest

from agent_toolkit._atk import run_script


def test_registry_stays_inside_plugin_root() -> None:
    for relative in run_script.SCRIPT_PATHS.values():
        target = (run_script.PLUGIN_ROOT / relative).resolve()
        assert target.is_relative_to(run_script.PLUGIN_ROOT)
        assert target.is_file()


def test_dispatch_forwards_help_and_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(script_name="plan-check", script_args=["--", "--help"])
    assert run_script.dispatch(args) == 0
    assert "計画の成立に必要な情報契約" in capsys.readouterr().out


def test_dispatch_rejects_missing_registered_script(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "missing", pathlib.Path("missing.py"))
    args = argparse.Namespace(script_name="missing", script_args=[])
    with pytest.raises(ValueError, match="plugin root内に存在しません"):
        run_script.dispatch(args)


def test_dispatch_forwards_session_review_evidence_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    """再照合の全引数を抽出器へ同じ順序で渡す。"""
    observed: list[str] = []

    def capture_argv(_target: str, *, run_name: str) -> None:
        assert run_name == "__main__"
        observed.extend(sys.argv)

    monkeypatch.setattr(run_script.runpy, "run_path", capture_argv)
    script_args = [
        "--transcript",
        "/tmp/transcript.jsonl",
        "--user-events",
        "--since",
        "2026-09-21T20:00:00Z",
        "--observation-boundary",
        "2026-09-21T20:05:00Z",
        "--output-file",
        "/tmp/additional-events.jsonl",
    ]

    assert run_script.dispatch(argparse.Namespace(script_name="session-review-evidence", script_args=["--", *script_args])) == 0

    assert observed == [str(run_script.registered_script_path("session-review-evidence")), *script_args]


def test_dispatch_forwards_completion_report_stage_and_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """報告段階と振り返り状態を検査器へ同じ順序で渡す。"""
    observed: list[str] = []

    def capture_argv(_target: str, *, run_name: str) -> None:
        assert run_name == "__main__"
        observed.extend(sys.argv)

    monkeypatch.setattr(run_script.runpy, "run_path", capture_argv)
    script_args = ["/tmp/report.md", "--stage", "review-result", "--review-state", "failed"]

    assert run_script.dispatch(argparse.Namespace(script_name="completion-report-check", script_args=["--", *script_args])) == 0

    assert observed == [str(run_script.registered_script_path("completion-report-check")), *script_args]


def test_dispatch_keeps_worktree_inputs_independent(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    script = plugin_root / "probe.py"
    script.write_text(
        "import json, pathlib, sys\nprint(json.dumps({'cwd': str(pathlib.Path.cwd()), 'args': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(run_script, "PLUGIN_ROOT", plugin_root)
    monkeypatch.setitem(run_script.SCRIPT_PATHS, "probe", pathlib.Path("probe.py"))

    results: list[dict[str, object]] = []
    for name in ("worktree-a", "worktree-b"):
        worktree = tmp_path / name
        worktree.mkdir()
        monkeypatch.chdir(worktree)
        assert run_script.dispatch(argparse.Namespace(script_name="probe", script_args=["--", name])) == 0
        results.append(json.loads(capsys.readouterr().out))

    assert results == [
        {"cwd": str(tmp_path / "worktree-a"), "args": ["worktree-a"]},
        {"cwd": str(tmp_path / "worktree-b"), "args": ["worktree-b"]},
    ]
