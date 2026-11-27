"""run skillの契約を検証する。"""

from __future__ import annotations

import datetime
import json
import os
import pathlib

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import run_skill_batch, weekly_schedule
from agent_toolkit._atk.wi import add as wi_add
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._common import session_review
from agent_toolkit._testing import git_repository, wi_entry_files
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _Env, _log_files, _records, _run, _startup_input

__all__ = ["_engine_env", "_records", "_run", "_startup_input"]


def test_claude_session_runs_skill_with_goal_and_reports_log(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Claude候補を採用した起動はホストの`/goal`を付けない目的文で対象リポジトリのrootから始まり、成功行でログの場所を示す。

    目的文に報告用UWIの保存と過去の実行のUWIの適用が欠けると、定期実行の結果がユーザーへ届かず、
    回答済みの対応も実施されない。
    """
    monkeypatch.delenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS", "600000")
    code, out, err = _run(
        ["--target-repo", str(engine_env.repo / "sub"), "--args", "対象: web", "example-plugin:check-logs"], capsys
    )

    assert code == 0, err
    sessions = _records(engine_env, "session")
    assert len(sessions) == 1
    argv = sessions[0]["argv"]
    assert sessions[0]["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] is None
    assert sessions[0]["env"]["CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS"] == "0"
    assert argv[:2] == ["claude", "-p"]
    for option, value in (
        ("--permission-mode", "auto"),
        ("--permission-prompts", "none"),
        ("--model", "sonnet"),
        ("--effort", "high"),
    ):
        assert argv[argv.index(option) + 1] == value
    session_id = argv[argv.index("--session-id") + 1]
    assert argv[-1] == (
        '<atk-auto source="run-skill" kind="goal">\n'
        "このセッションが担当するスキル`example-plugin:check-logs`の工程を完遂する"
        "（起動元が終了後に行う検収と確定は含まない）。"
        "スキル`example-plugin:check-logs`を起動して始める（起動入力の`free_text`が空でなければ引数として渡す）。\n</atk-auto>"
    )
    data = json.loads(sessions[0]["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"])
    assert data["arguments"] == data["free_text"] == "対象: web"
    assert data["accepted_scopes"] == ["example-plugin:check-logs"]
    assert data["run_id"] == sessions[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    assert pathlib.Path(sessions[0]["cwd"]).resolve() == engine_env.repo.resolve()
    lines = out.splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("成功: ")
    log_path = _log_files(engine_env)[0]
    assert str(log_path) in lines[0]
    log = log_path.read_text(encoding="utf-8")
    for expected in (
        "採用した候補: claude:sonnet/high",
        f"Claudeのセッション識別子: {session_id}",
        "[stdout] session stdout line",
        "[stderr] session stderr line",
        "終了状態: exit code 0",
    ):
        assert expected in log


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("disabled", ["0", "1"])
def test_regular_run_skill_preserves_background_setting(engine_env, monkeypatch, capsys, engine, disabled) -> None:
    monkeypatch.setenv("CLAUDE_CODE_DISABLE_BACKGROUND_TASKS", disabled)
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    assert os.environ["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == disabled
    assert _records(engine_env, "session")[0]["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == disabled


def test_codex_session_receives_goal_without_slash_command(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Codexにも`atk-auto`要素の目的文だけを渡す。"""
    set_orchestrate_environment(monkeypatch, "codex:gpt-5.6-sol/low")

    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)

    assert code == 0, err
    sessions = _records(engine_env, "session")
    assert sessions[0]["argv"][:6] == ["codex", "exec", "--model", "gpt-5.6-sol", "-c", "model_reasoning_effort=low"]
    assert sessions[0]["argv"][-1].startswith('<atk-auto source="run-skill" kind="goal">')


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("mode", ["missing", "question", "other", "unsynced"])
def test_normal_exit_requires_same_run_committed_report(
    engine_env: _Env, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], engine: str, mode: str
) -> None:
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    monkeypatch.setenv("FAKE_REPORT_MODE", mode)
    code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
    assert code == 1
    assert not out
    assert "報告用UWI" in err and "ローカル保存" in err and "次の操作:" in err
    assert str(_log_files(engine_env)[0]) in err


def test_weekly_meta_preserves_report_save_failure_after_child_exits_zero(engine_env, monkeypatch, capsys):
    monkeypatch.setenv("FAKE_REPORT_MODE", "missing")
    reports = []

    def save_failure(_key, message):
        reports.append(message())
        return True

    monkeypatch.setattr(run_skill_batch, "_save_failure_report", save_failure)
    code, _out, error = _run(["--target-repo", str(engine_env.repo), "--args", "meta", "agent-toolkit:review-session"], capsys)
    assert code == 1 and "報告用UWIのローカル保存を確認できない" in error
    assert len(reports) == 1 and "終了状態: exit code 0" in reports[0]
    saved = session_review.read(weekly_schedule.record_path("meta"))
    assert saved["failure_stage"] == "report-save" and saved["exit_code"] == 1
    assert "報告用UWIのローカル保存を確認できない" in saved["failure_diagnostics"]["error"]
    assert str(_log_files(engine_env)[0]) not in reports[0]


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("push_fails,answered", [(False, False), (True, False), (False, True)])
def test_report_success_does_not_require_remote_push(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    engine: str,
    push_fails: bool,
    answered: bool,
) -> None:
    """報告用UWIのローカルcommitがあれば、bare remoteが送信を拒否しても成功する。  pyfltr: allow-comment"""
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    origin = git_repository.init_bare_repository(tmp_path / "origin.git")
    git_repository.commit_all(notes, "initial")
    git_repository.run_git(notes, "remote", "add", "origin", str(origin))
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD")
    monkeypatch.setenv("FAKE_REPORT_REMOTE", "1")
    if answered:
        monkeypatch.setenv("FAKE_REPORT_ANSWER", "1")
    if push_fails:
        pre_receive = origin / "hooks" / "pre-receive"
        pre_receive.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        pre_receive.chmod(0o755)
        monkeypatch.setenv("FAKE_REPORT_PUSH_FAIL", "1")
    code, out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
    assert code == 0, err
    assert out


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.25,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.699秒。"),
)
def test_wi_save_and_edit_bind_report_to_run(
    engine_env: _Env,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    del engine_env
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    monkeypatch.setenv("AGENT_TOOLKIT_RUN_SKILL_ID", "run-1")
    names = wi_add.add_entries(
        notes,
        messages=["---\nrun_skill_report: true\n---\n実行結果を確認してください。"],
        target_repo="github.com/example/repo",
        source="run-skill",
        now=datetime.datetime(2026, 10, 18),
        entry_type="uwi",
        scope="my-skill",
        question_type="post-approval",
    )
    path = notes / "2026/10" / names[0]
    saved = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert saved is not None and saved[0]["run_skill_run_id"] == "run-1"
    assert saved[0]["state"] == "inbox"
    assert wi_add.has_synced_run_report(notes, run_id="run-1", scope="my-skill")
    body_file = tmp_path / "report-update.md"
    body_file.write_text("更新後の実行結果を確認してください。", encoding="utf-8")
    with pytest.raises(SystemExit) as updated:
        atk.main(
            [
                "wi",
                "edit",
                names[0],
                "--body-file",
                str(body_file),
                "--target-repo=github.com/example/repo",
            ],
            home=tmp_path,
        )
    assert updated.value.code == 0, capsys.readouterr().err
    saved = frontmatter.parse_frontmatter(path.read_text(encoding="utf-8"))
    assert saved is not None and saved[0]["run_skill_run_id"] == "run-1"
    assert saved[0]["state"] == "inbox"
    assert "更新後の実行結果" in saved[1]
    assert wi_add.has_synced_run_report(notes, run_id="run-1", scope="my-skill")


@pytest.mark.parametrize("collision", ["none", "branch-tag", "remote-current", "remote-ref"])
def test_report_sync_uses_current_branch_config_with_ref_collisions(tmp_path: pathlib.Path, collision: str) -> None:
    """同名の別refがあっても、現在branchの送信先へ到達した報告だけを同期済みとする。"""
    notes = git_repository.init_repository(
        tmp_path / "notes", initial_branch="review/reports", files={"README": "base\n"}, commit_message="base"
    )
    origin = git_repository.init_bare_repository(tmp_path / "origin.git")
    other = git_repository.init_bare_repository(tmp_path / "other.git")
    git_repository.run_git(notes, "remote", "add", "origin", str(origin))
    git_repository.run_git(notes, "remote", "add", "other", str(other))
    git_repository.run_git(notes, "push", "-u", "origin", "HEAD:refs/heads/published/reports")
    git_repository.run_git(notes, "push", "other", "HEAD:refs/heads/unrelated")

    git_repository.run_git(notes, "config", "branch.heads/review/reports.remote", "other")
    git_repository.run_git(notes, "config", "branch.heads/review/reports.merge", "refs/heads/unrelated")
    if collision == "branch-tag":
        git_repository.run_git(notes, "tag", "review/reports")
    elif collision == "remote-current":
        git_repository.run_git(notes, "update-ref", "refs/remotes/review/reports", "HEAD")
    elif collision == "remote-ref":
        git_repository.run_git(notes, "tag", "origin/published/reports")
        git_repository.run_git(notes, "branch", "--no-track", "origin/published/reports", "HEAD")
    report = wi_entry_files.entry_path(notes, "report.md")
    content = (
        "---\nstate: inbox\ntype: uwi\nsource: run-skill\nscope: my-skill\n"
        "run_skill_report: true\nrun_skill_run_id: run-1\n---\n\n実行の報告\n"
    )
    report.write_text(content, encoding="utf-8")
    git_repository.commit_all(notes, "report")

    assert not wi_add.has_synced_run_report(notes, run_id="run-1", scope="my-skill")
    git_repository.run_git(notes, "push", "origin", "HEAD:refs/heads/published/reports")
    assert wi_add.has_synced_run_report(notes, run_id="run-1", scope="my-skill")
    report.write_text(content + "未commitの変更\n", encoding="utf-8")
    assert not wi_add.has_synced_run_report(notes, run_id="run-1", scope="my-skill")


def test_diagnostic_stream_preserves_conversation_but_redacts_errors(engine_env, capsys):
    conversation = '{"type":"assistant","text":"password=fake-conversation"}'
    error = '{"type":"error","message":"password=fake-error"}'
    engine = engine_env.bin_dir / "claude"
    script = engine.read_text(encoding="utf-8")
    engine.write_text(
        script.replace('print("session stdout line")', f"print({conversation!r})\nprint({error!r})").replace(
            'print("session stderr line", file=sys.stderr)', 'print("E401 password=fake-stderr", file=sys.stderr)'
        ),
        encoding="utf-8",
    )
    code, _out, err = _run(["--target-repo", str(engine_env.repo), "my-skill"], capsys)
    assert code == 0, err
    path = _log_files(engine_env)[0]
    saved = path.read_text(encoding="utf-8")
    assert conversation in saved
    assert "fake-error" not in saved and "fake-stderr" not in saved
    assert "E401" in saved
