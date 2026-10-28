"""無進捗記録による起動の見送りと、記録の保持・比較・削除の契約を検証する。"""

import argparse
import json
import pathlib
import subprocess
import sys
from typing import Any

import pytest

from agent_toolkit._atk import config, review_audit
from agent_toolkit._atk.wi import alerts as wi_alerts
from agent_toolkit._atk.wi import listing
from agent_toolkit._atk.wi import process_loop as loop
from agent_toolkit._atk.wi import process_loop_launch_gate as gate
from agent_toolkit._atk.wi import process_loop_session as session
from agent_toolkit._atk.wi import process_loop_update as update_module
from agent_toolkit._atk.wi import process_loop_watch as watch
from agent_toolkit._atk.wi.process_loop_test import _manifest
from agent_toolkit._testing import git_repository as git

_REPO = "example.test/team/one"


def _setup(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> tuple[pathlib.Path, config.ConfiguredRepository]:
    worktree = git.init_repository(tmp_path / "one", origin=f"https://{_REPO}.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_PROCESS_LOOP_REPOSITORIES", json.dumps([str(worktree)]))
    notes = tmp_path / "notes"
    monkeypatch.setenv("AGENT_TOOLKIT_PRIVATE_NOTES", str(notes))
    directory = notes / "2026" / "10"
    directory.mkdir(parents=True)
    (directory / "task.md").write_text(f"---\nstate: inbox\ntype: awi\ntarget_repo: {_REPO}\n---\n\n# 要求\n", encoding="utf-8")
    return notes, config.resolve_configured_repositories()[0]


def _record(notes: pathlib.Path, repository: config.ConfiguredRepository, alerts: frozenset[int] | None) -> gate.LaunchInput:
    current = gate.repository_input(listing.configured_repo_snapshot(notes, (repository,))[0], alerts)
    gate.record_no_progress(_REPO, current, current)
    return current


def test_unobserved_alerts_keep_record(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """未観測時はWIの抑止を保持し、未markのアラートは過去の無進捗記録があっても再試行する。"""
    notes, repository = _setup(tmp_path, monkeypatch)
    current = _record(notes, repository, frozenset({5}))
    assert gate.decide(_REPO, current.with_alerts(None)).skipped
    stored = json.loads(next(gate.record_directory().glob("*.json")).read_text(encoding="utf-8"))
    assert "alerts" not in stored
    # 旧版が保存した番号だけの記録でも、未markアラートを抑止しない。
    stored["alerts"] = [5]
    next(gate.record_directory().glob("*.json")).write_text(json.dumps(stored), encoding="utf-8")
    assert gate.decide(_REPO, current.with_alerts(frozenset({5}))).launch
    # 判定済みで未判定から外れた番号は新しい入力に当たらない。
    assert gate.decide(_REPO, current.with_alerts(frozenset())).skipped
    assert gate.decide(_REPO, current.with_alerts(frozenset({5, 6}))).launch


def test_record_survives_restart_and_is_cleared(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録は別プロセスの起動後も読まれ、見送らずに起動した時点と起動入力が空になった時点で削除される。"""
    notes, repository = _setup(tmp_path, monkeypatch)
    current = _record(notes, repository, None)
    # 自己再起動と同じく、状態ディレクトリを共有する新しいプロセスが記録を読んで見送りを判定する。
    code = (
        "from agent_toolkit._atk.wi import listing, process_loop_launch_gate as gate\n"
        "from agent_toolkit._atk import config\n"
        "import pathlib, sys\n"
        "snapshot = listing.configured_repo_snapshot(pathlib.Path(sys.argv[1]), config.resolve_configured_repositories())[0]\n"
        "print(gate.decide(sys.argv[2], gate.repository_input(snapshot, None)).skipped)\n"
    )
    restarted = subprocess.run(
        [sys.executable, "-c", code, str(notes), _REPO], capture_output=True, text=True, check=False, timeout=120
    )
    assert restarted.returncode == 0, restarted.stderr
    assert restarted.stdout.strip() == "True"

    def execute(_args: argparse.Namespace, _cwd: pathlib.Path, prompt: str, env: dict[str, str], **kwargs: Any) -> bool:
        data = json.loads(_manifest(prompt).read_text())
        for path in notes.glob("2026/10/task.md"):
            path.unlink()
        env["AGENT_TOOLKIT_PROCESS_LOOP_SESSION_ID"] = "session-one"
        kwargs["on_session_started"]("session-one")
        pathlib.Path(data["result_file"]).write_text(json.dumps({"repositories": {_REPO: {"status": "completed"}}}))
        return True

    monkeypatch.setattr(session, "run_process_session", execute)
    monkeypatch.setattr(session, "resolve_orchestrator_specs", lambda: [("claude", "model", "medium")])
    monkeypatch.setattr(session, "select_available_orchestrator", lambda *_a, **_k: ("claude", "model", "medium"))
    results = loop.run_coordinator(
        argparse.Namespace(resume=None, worktree=None),
        listing.configured_repo_snapshot(notes, (repository,)),
        launch_inputs={_REPO: current},
    )
    assert results[_REPO]["status"] == "completed"
    assert not list(gate.record_directory().glob("*.json"))

    # 候補・診断が無く、アラートの取得に成功して0件になった反復で記録を削除する。
    gate.record_no_progress(_REPO, current, current)
    monkeypatch.setattr(wi_alerts, "check_and_submit_alerts", lambda *_a, **_k: wi_alerts.AlertCheckResult(0, ()))
    monkeypatch.setattr(review_audit, "dependabot_pending", lambda _repository: {"status": "available", "alerts": []})
    monkeypatch.setattr(watch, "pull_private_notes", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(update_module, "resolve_dotfiles_root", lambda: None)

    def stop(*_args: Any, **_kwargs: Any) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(watch, "wait_for_changes", stop)
    args = argparse.Namespace(
        target_repo=None,
        resume=None,
        auto_resume=False,
        worktree=None,
        no_update=True,
        no_alerts=False,
        alert_interval=0,
        alert_forge="github",
        internal_dotfiles_updated=False,
    )
    loop.cmd_process_loop(args, notes)
    assert not list(gate.record_directory().glob("*.json"))
