"""run-skillの実行記録の互換読込と、読めない記録があっても起動を止めない契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import time
import typing

import pytest

from agent_toolkit._atk import config, outcome, run_skill, run_skill_state
from agent_toolkit._common import session_activity, session_review
from agent_toolkit._testing import git_repository, session_evidence_support
from agent_toolkit._testing.run_skill_support import _engine_env, _Env, _records, _run

__all__ = ["_engine_env"]

_OLD = time.time() - run_skill.LOG_RETENTION_SECONDS - 3600


def _write_record(
    env: _Env, run_id: str, connection: dict[str, typing.Any] | None, *, status: str = "interrupted"
) -> pathlib.Path:
    """30日より前のログを参照する実行記録を、保存された形のJSONで置く。"""
    root = env.state / "agent-toolkit" / "run-skill"
    (root / "runs").mkdir(parents=True, exist_ok=True)
    log_path = root / f"20261109-000000-agent-toolkit_review-session-{run_id[:8]}.log"
    log_path.write_text("old\n", encoding="utf-8")
    os.utime(log_path, (_OLD, _OLD))
    payload = {
        "input": {
            "source": "run-skill",
            "run_id": run_id,
            "skill": "agent-toolkit:review-session",
            "arguments": "batch",
            "mode": "batch",
            "free_text": None,
            "accepted_scopes": ["agent-toolkit:review-session"],
            "target_repo": None,
            "batch_manifest": None,
        },
        "work_dir": str(env.repo),
        "log_path": str(log_path),
        "engine": "claude",
        "model": "sonnet",
        "effort": "high",
        "session_id": None,
        "connection": connection,
        "remaining": 60.0,
        "deadline": None,
        "executor": None,
        "status": status,
    }
    (root / "runs" / f"{run_id}.json").write_text(json.dumps(payload), encoding="utf-8")
    return log_path


def test_legacy_isolation_env_file_record_starts_new_run(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """`isolation_env_file`を保存した旧形式の記録を読み替え、新しい実行の起動とその記録のログ保護を続ける。"""
    unset = "0" * 32
    path_valued = "1" * 32
    unset_log = _write_record(engine_env, unset, {"kind": "subscription", "api_env_file": None, "isolation_env_file": None})
    path_log = _write_record(
        engine_env, path_valued, {"kind": "subscription", "api_env_file": None, "isolation_env_file": "/secrets/credits.env"}
    )

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert outcome.WARNING_PREFIX not in err
    assert len(_records(engine_env, "session")) == 1
    assert unset_log.exists() and path_log.exists()
    unset_connection = run_skill_state.load(unset).connection
    path_connection = run_skill_state.load(path_valued).connection
    assert unset_connection is not None and unset_connection.isolation_env_files == ()
    assert path_connection is not None and path_connection.isolation_env_files == ("/secrets/credits.env",)


def test_unreadable_record_does_not_block_new_run(engine_env: _Env, capsys: pytest.CaptureFixture[str]) -> None:
    """読めない記録は診断して残し、参照先を特定できないログを回収せずに新しい実行を起動する。"""
    root = engine_env.state / "agent-toolkit" / "run-skill"
    (root / "runs").mkdir(parents=True)
    broken = root / "runs" / f"{'3' * 32}.json"
    broken.write_text("{", encoding="utf-8")
    log_path = root / "20261109-000000-my-skill-unknown.log"
    log_path.write_text("old\n", encoding="utf-8")
    os.utime(log_path, (_OLD, _OLD))

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert len(_records(engine_env, "session")) == 1
    assert str(broken) in err and "次の操作:" in err
    assert broken.exists() and log_path.exists()


def _prepare_batch_run(engine_env: _Env, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch):
    """発生時と同じく待ち対象を用意し、報告の同期先を隔離したGitにする。"""
    session_id = "11111111-1111-4111-8111-111111111111"
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    transcript = codex_home / "sessions/2026/11/20" / f"rollout-2026-11-20T00-54-42-{session_id}.jsonl"
    session_evidence_support.write_jsonl(
        transcript,
        [
            {"type": "session_meta", "payload": {"id": session_id}},
            {"type": "event_msg", "payload": {"type": "user_message", "message": "試験を確認して"}},
        ],
    )
    assert session_review.record(
        {
            "hook_event_name": "SessionStart",
            "session_id": session_id,
            "cwd": str(engine_env.repo),
            "transcript_path": str(transcript),
        },
        host="codex",
        target_repo="example.com/team/repo",
    )
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    remote = git_repository.init_bare_repository(tmp_path / "reports.git")
    git_repository.commit_all(notes, "initial")
    git_repository.run_git(notes, "remote", "add", "origin", str(remote))
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD")
    monkeypatch.setenv("FAKE_REPORT_REMOTE", "1")

    return session_id, notes, remote


@pytest.mark.cpu_limit(1.0, reason="実prepare・偽ホストの子プロセス・実Gitの報告同期を通してbatchの完了を検収する。")
def test_batch_finishes_codex_target_with_completed_legacy_run(engine_env, tmp_path, monkeypatch, capsys) -> None:
    """旧形式の完了記録が残っていても、Codexの待ち対象を準備し、同期済み報告と処理済み範囲を残す。"""
    # 準備前の合成会話に実ホスト上のプロセスはない。
    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    _write_record(
        engine_env,
        "2" * 32,
        {"kind": "subscription", "api_env_file": None, "isolation_env_file": "/old/credits.env"},
        status="completed",
    )
    session_id, notes, remote = _prepare_batch_run(engine_env, tmp_path, monkeypatch)
    code, _out, err = _run(["agent-toolkit:review-session", "--args", "batch"], capsys)

    assert code == 0, err
    (child,) = _records(engine_env, "session")
    run_id = child["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    manifest = session_review.read(session_review.directory() / "runs" / f"{run_id}.json")
    assert [(item["host"], item["session_id"]) for item in manifest["prepared"]] == [("codex", session_id)]
    assert manifest["analysis_succeeded"] and manifest["status"] == "completed"
    assert not session_review.pending()
    branch = git_repository.git_output(notes, "branch", "--show-current")
    (report_path,) = git_repository.git_output(remote, "ls-tree", "-r", "--name-only", branch).splitlines()
    report = git_repository.git_output(remote, "show", f"{branch}:{report_path}")
    assert f"run_skill_run_id: {run_id}" in report and "run_skill_report: true" in report
    assert session_id in report


@pytest.mark.cpu_limit(2.5, reason="実prepareとローカルGitの失敗報告保存を通し、通常のログ保護まで確かめる。")
@pytest.mark.parametrize(
    ("case", "exit_code", "diagnostic"),
    [
        ("preflight", 2, "synthetic record sweep failed"),
        ("model-config", 2, "synthetic model settings failed"),
        ("execution-setup", 1, "synthetic process identity failed"),
        ("child", 7, "[stderr] session stderr line"),
    ],
)
def test_batch_preserves_failure_log(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: str,
    exit_code: int,
    diagnostic: str,
) -> None:
    """起動前と子実行のいずれの失敗でも、同じbatchからログと診断を取得できる。"""
    session_id, notes, _remote = _prepare_batch_run(engine_env, tmp_path, monkeypatch)
    if case == "preflight":

        def fail_sweep(*_args: object, **_kwargs: object) -> None:
            raise ValueError(diagnostic)

        monkeypatch.setattr(run_skill, "_remove_expired_logs", fail_sweep)
    elif case == "model-config":

        def fail_settings(*_args: object, **_kwargs: object) -> None:
            raise run_skill.ActionableError(diagnostic, next_action="合成モデル設定を確認する")

        monkeypatch.setattr(config, "parse_unresolved_model_candidates", fail_settings)
    elif case == "execution-setup":

        def fail_launch(*_args: object, **_kwargs: object) -> None:
            raise run_skill.ActionableError(diagnostic, next_action="合成プロセスの状態を確認する")

        monkeypatch.setattr(run_skill, "_run_locked", fail_launch)
    else:
        monkeypatch.setenv("FAKE_REPORT_MODE", "missing")
        monkeypatch.setenv("FAKE_SESSION_EXIT", str(exit_code))

    code, _out, err = _run(["agent-toolkit:review-session", "--args", "batch"], capsys)

    assert code == exit_code, err
    (manifest_path,) = (session_review.directory() / "runs").glob("*.json")
    manifest = session_review.read(manifest_path)
    assert [item["session_id"] for item in manifest["prepared"]] == [session_id]
    assert manifest["status"] == "failed"
    assert manifest["exit_code"] == (None if case == "execution-setup" else exit_code)
    log_path = pathlib.Path(manifest["log_path"])
    assert log_path in session_review.protected_logs()
    assert diagnostic in log_path.read_text(encoding="utf-8")
    assert manifest["report_synced"]
    (failure_body,) = [
        body
        for path in notes.rglob("*.md")
        if f"session_review_failure_key: {manifest['failure_key']}" in (body := path.read_text(encoding="utf-8"))
    ]
    assert diagnostic in failure_body
    if case == "execution-setup":
        assert "合成プロセスの状態を確認する" in failure_body
    if case != "child":
        assert diagnostic in manifest["error"]
        assert "次の操作:" in log_path.read_text(encoding="utf-8")
        assert not _records(engine_env, "session")
    else:
        assert _records(engine_env, "session")
