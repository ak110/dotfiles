"""run skill modesの契約を検証する。"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess

import pytest

from agent_toolkit._atk import run_skill, run_skill_state, weekly_schedule
from agent_toolkit._atk.wi import frontmatter
from agent_toolkit._common import session_activity, session_review, skill_modes
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _records, _run, _startup_input

__all__ = ["_engine_env"]


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.456秒。"),
)
@pytest.mark.parametrize(
    "engine,arguments,skill",
    [
        ("claude", None, "agent-toolkit:review-session"),
        ("claude", "auto", "agent-toolkit:review-session"),
        ("claude", "batchx", "agent-toolkit:review-session"),
        ("claude", "batch auto 任意の着眼点", "agent-toolkit:review-session"),
        ("codex", "batch auto 任意の着眼点", "agent-toolkit:review-session"),
        ("claude", "batch auto 任意の着眼点", "review-session"),
    ],
)
def test_batch_uses_existing_runner_and_report_receipt(
    engine_env, tmp_path, monkeypatch, capsys, engine, arguments, skill
) -> None:
    """2会話の実prepareから偽ホスト・同期済み報告・範囲確定まで公開CLIで検証する。"""

    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    for name in ("one", "two"):
        transcript = tmp_path / f"{name}.jsonl"
        transcript.write_text(
            json.dumps({"type": "user", "message": {"role": "user", "content": "初期要求"}}) + "\n", encoding="utf-8"
        )
        session_review.record(
            {
                "hook_event_name": "SessionStart",
                "session_id": name,
                "transcript_path": str(transcript),
                "cwd": str(engine_env.repo),
            },
            host="claude",
            target_repo="https://example.com/team/repo",
        )
    options = ["--target-repo", str(engine_env.repo)]
    if arguments is not None:
        options.extend(["--args", arguments])
    code, _out, error = _run([*options, skill], capsys)
    assert code == 0, error
    assert len(_records(engine_env, "session")) == 1
    manifests = list((session_review.directory() / "runs").glob("*.json"))
    records = _records(engine_env, "session")
    data = _startup_input(records[0], monkeypatch, capsys)
    assert data["arguments"] == arguments
    if arguments != "batch auto 任意の着眼点":
        assert not manifests
        assert len(session_review.pending()) == 2
        assert data["mode"] is None
        assert data["free_text"] == arguments
        return
    assert not session_review.pending()
    if engine == "claude":
        assert records[0]["env"]["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    assert len(manifests) == 1
    value = session_review.read(manifests[0])
    assert value["status"] == "completed" and len(value["prepared"]) == 2
    assert records[0]["argv"][-1] == (
        '<atk-auto source="run-skill" kind="goal">\n'
        "このセッションが担当するスキル`agent-toolkit:review-session batch`の工程を完遂する"
        "（起動元が終了後に行う検収と確定は含まない）。"
        "スキル`agent-toolkit:atk-mode`を作業`review-session batch`で起動して始める"
        "（起動入力の`free_text`が空でなければ引数の後ろへ続ける）。\n</atk-auto>"
    )
    assert data["arguments"] == "batch auto 任意の着眼点"
    assert data["free_text"] == "auto 任意の着眼点"
    assert data["target_repo"] == "example.com/team/repo"
    assert pathlib.Path(data["batch_manifest"]) == manifests[0]
    assert pathlib.Path(data["batch_analysis"]) == session_review.batch_analysis_path(manifests[0])
    assert "| one | 完了 |" in pathlib.Path(data["batch_analysis"]).read_text(encoding="utf-8")


@pytest.mark.usefixtures("leaf_git_commands")
def test_batch_rejects_finish_without_report_and_resumes_prepared_scope(engine_env, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(session_activity, "host_processes", lambda: ())
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    monkeypatch.setenv("FAKE_REPORT_MODE", "missing")
    transcript = tmp_path / "one.jsonl"
    transcript.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "振り返り対象"}}) + "\n", encoding="utf-8"
    )
    session_review.record(
        {
            "hook_event_name": "SessionStart",
            "session_id": "one",
            "transcript_path": str(transcript),
            "cwd": str(engine_env.repo),
        },
        host="claude",
        target_repo="https://example.com/team/repo",
    )
    code, out, err = _run(["--target-repo", str(engine_env.repo), "--args", "batch", "agent-toolkit:review-session"], capsys)
    assert code == 2 and not out
    assert "exit code 2" in err
    (path,) = (session_review.directory() / "runs").glob("*.json")
    failed = session_review.read(path)
    assert failed["status"] == "failed" and not failed.get("analysis_succeeded")
    assert failed["report_steps"][-1]["status"] == "報告用UWIが無い"
    assert "終了状態: exit code 2" in pathlib.Path(failed["log_path"]).read_text(encoding="utf-8")
    assert session_review.previous_scope(failed["targets"][0]) is None

    analysis = session_review.batch_analysis_path(path)
    analysis.unlink()
    monkeypatch.setenv("FAKE_REPORT_MODE", "saved")
    code, out, err = _run(["--resume", path.stem], capsys)
    assert code == 0, err
    assert "成功:" in out
    completed = session_review.read(path)
    assert completed["status"] == "completed" and completed["prepared"] == failed["prepared"]
    assert "| one | 完了 |" in analysis.read_text(encoding="utf-8")
    assert session_review.previous_scope(failed["targets"][0]) is not None
    assert not session_review.pending()


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.parametrize("mode", ["architecture", "ui-sweep"])
def test_weekly_run_record_is_completed_only_after_the_original_slot_report(engine_env, monkeypatch, capsys, mode):
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    code, out, error = _run(["--work-dir", str(engine_env.repo), "--args", mode, "review-session"], capsys)
    assert (code, out) == (1, "") and "結果UWIが保存されていない" in error
    initial = _records(engine_env, "session")[0]
    run_id = initial["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    saved = run_skill_state.load(run_id)
    slot = saved.weekly_slot
    assert slot is not None and slot.startswith(mode + "@")
    assert saved.status == "interrupted"
    assert weekly_schedule.saved_slot(slot) is None
    notes = pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"])
    original = notes / "2026/10" / f"{run_id}.md"
    content = original.read_text(encoding="utf-8").replace(
        f"run_skill_run_id: {run_id}", f"run_skill_run_id: {run_id}\nweekly_slot: {slot}"
    )
    other = original.with_name("other.md")
    other.write_text(content.replace(run_id, "f" * 32), encoding="utf-8")
    subprocess.run(["git", "-C", str(notes), "add", str(other)], check=True)
    subprocess.run(["git", "-C", str(notes), "commit", "-qm", "other run"], check=True)
    original.write_text(content, encoding="utf-8")
    assert weekly_schedule.saved_slot(slot) is not None

    monkeypatch.setenv("FAKE_REPORT_WEEKLY", "true")
    code, out, error = _run(["--resume", run_id], capsys)
    assert code == 0 and f"週枠 {slot}" in out, error
    assert run_skill_state.load(run_id).status == "completed"
    assert weekly_schedule.saved_slot(slot) is not None
    assert len(_records(engine_env, "session")) == 2
    assert _run(["--resume", run_id], capsys)[0] == 0
    assert len(_records(engine_env, "session")) == 2


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("arguments", [None, "追加指示"])
def test_final_goal_is_only_skill_completion(engine_env, monkeypatch, capsys, engine, arguments):
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    options = ["--target-repo", str(engine_env.repo)]
    if arguments is not None:
        options.extend(["--args", arguments])
    code, _out, error = _run([*options, "my-skill"], capsys)
    assert code == 0, error
    expected = (
        '<atk-auto source="run-skill" kind="goal">\nこのセッションが担当するスキル`my-skill`の工程を完遂する'
        "（起動元が終了後に行う検収と確定は含まない）。"
        "スキル`my-skill`を起動して始める（起動入力の`free_text`が空でなければ引数として渡す）。\n</atk-auto>"
    )
    assert _records(engine_env, "session")[0]["argv"][-1] == expected
    data = _startup_input(_records(engine_env, "session")[0], monkeypatch, capsys)
    assert data["arguments"] == data["free_text"] == arguments
    assert data["accepted_scopes"] == ["my-skill"]


def test_mode_lock_ignores_focus_and_keeps_other_modes_separate(tmp_path):
    def lock(arguments):
        return run_skill._lock_path(  # noqa: SLF001  # pylint: disable=protected-access
            tmp_path, skill_modes.invocation_scope(skill_modes.PROCESS_WI, arguments)
        )

    assert lock("single-lane A") == lock("single-lane B")
    assert lock("single-lane A") != lock("all-repos A")
    assert lock("通常の追加指示") != lock("single-lane A")


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("arguments", [None, "single-lane 追記  保持\n", "all-repos 追加指示", "unknown single-lane"])
@pytest.mark.parametrize("named_by_entry", [False, True])
def test_process_modes_reach_public_runner_with_original_arguments(
    engine_env, monkeypatch, capsys, engine, arguments, named_by_entry
):
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    argv = ["--target-repo", str(engine_env.repo)]
    if named_by_entry:
        argv.extend(["--args", "process-wi" if arguments is None else f"process-wi {arguments}", "agent-toolkit:atk-mode"])
    else:
        argv.extend([*(["--args", arguments] if arguments is not None else []), skill_modes.PROCESS_WI])
    code, _out, error = _run(argv, capsys)
    assert code == 0, error
    record = _records(engine_env, "session")[0]
    data = json.loads(record["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"])
    mode = (
        "single-lane"
        if arguments and arguments.startswith("single-lane ")
        else "all-repos"
        if arguments and arguments.startswith("all-repos ")
        else None
    )
    assert data["free_text"] == (arguments.removeprefix(f"{mode} ") if arguments and mode else arguments)
    assert data["mode"] == mode
    assert data["skill"] == skill_modes.PROCESS_WI
    assert data["arguments"] == arguments
    expected_skill = skill_modes.PROCESS_WI + (" " + mode if mode else "")
    expected_work = "process-wi" + (" " + mode if mode else "")
    expected = (
        f'<atk-auto source="run-skill" kind="goal">\nこのセッションが担当するスキル`{expected_skill}`の工程を完遂する'
        "（起動元が終了後に行う検収と確定は含まない）。"
        f"スキル`agent-toolkit:atk-mode`を作業`{expected_work}`で起動して始める"
        "（起動入力の`free_text`が空でなければ引数の後ろへ続ける）。\n</atk-auto>"
    )
    assert record["argv"][-1] == expected


@pytest.mark.parametrize("resume_form", ["entry", "entry-with-work", "qualified", "omitted"])
def test_resume_accepts_entry_and_qualified_forms_of_the_same_work(engine_env, monkeypatch, capsys, resume_form):
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    monkeypatch.setenv("FAKE_SESSION_EXIT", "1")
    code, _out, error = _run(
        ["--target-repo", str(engine_env.repo), "--args", "process-wi single-lane X", "agent-toolkit:atk-mode"], capsys
    )
    assert code != 0, error
    run_id = _records(engine_env, "session")[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    monkeypatch.setenv("FAKE_SESSION_EXIT", "0")
    skill = {
        "entry": ["agent-toolkit:atk-mode"],
        "entry-with-work": ["--args", "process-wi single-lane X", "agent-toolkit:atk-mode"],
        "qualified": [skill_modes.PROCESS_WI],
        "omitted": [],
    }[resume_form]
    code, _out, error = _run(["--resume", run_id, *skill], capsys)
    assert code == 0, error
    records = _records(engine_env, "session")
    assert len(records) == 2
    initial, resumed = (_startup_input(record, monkeypatch, capsys) for record in records)
    assert resumed["skill"] == skill_modes.PROCESS_WI
    for field in ("skill", "mode", "free_text"):
        assert resumed[field] == initial[field]


@pytest.mark.parametrize("arguments", ["process-wi", "single-lane X", "process-wi all-repos X"])
def test_resume_rejects_different_explicit_invocation(engine_env, monkeypatch, capsys, arguments):
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    monkeypatch.setenv("FAKE_SESSION_EXIT", "1")
    code, _out, error = _run(["--target-repo", str(engine_env.repo), "--args", "single-lane X", skill_modes.PROCESS_WI], capsys)
    assert code != 0, error
    run_id = _records(engine_env, "session")[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    monkeypatch.setenv("FAKE_SESSION_EXIT", "0")
    code, _out, error = _run(["--resume", run_id, "--args", arguments, "agent-toolkit:atk-mode"], capsys)
    assert code == 2, error
    assert len(_records(engine_env, "session")) == 1


def test_resume_rejects_entry_form_for_other_work(engine_env, monkeypatch, capsys):
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    monkeypatch.setenv("FAKE_SESSION_EXIT", "1")
    code, _out, error = _run(["--target-repo", str(engine_env.repo), skill_modes.PROCESS_WI], capsys)
    assert code != 0, error
    run_id = _records(engine_env, "session")[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    for extra in (["--args", "review-session", "agent-toolkit:atk-mode"], ["my-skill"]):
        code, _out, error = _run(["--resume", run_id, *extra], capsys)
        assert code == 2 and ("一致しない" in error or "変更できない" in error)
    assert len(_records(engine_env, "session")) == 1


@pytest.mark.parametrize(
    ("mode", "resume_form"),
    [(None, "unqualified"), (None, "qualified"), (None, "entry"), (None, "omitted"), ("batch", "omitted")],
)
def test_resume_completes_run_saved_with_unqualified_work_name(engine_env, monkeypatch, capsys, mode, resume_form):
    """作業名を修飾名へそろえる前のCLIで中断した実行は、各指定の再開で完了し、保存記録と報告のscopeは保存時の値を保つ。

    そろえる前の`atk run-skill review-session`は、起動入力の作業名と取込対象scopeを無修飾で保存した。
    再開したホストはその起動入力どおり無修飾の`scope`で報告を保存する。
    """
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    monkeypatch.setenv("FAKE_SESSION_EXIT", "1")
    monkeypatch.setenv("FAKE_REPORT_MODE", "missing")
    code, _out, error = _run(["--target-repo", str(engine_env.repo), "agent-toolkit:review-session"], capsys)
    assert code != 0, error
    run_id = _records(engine_env, "session")[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    saved = engine_env.state / "agent-toolkit" / "run-skill" / "runs" / f"{run_id}.json"
    payload = json.loads(saved.read_text(encoding="utf-8"))
    legacy_scope = "review-session" + (f" {mode}" if mode else "")
    payload["input"].update(
        skill="review-session", arguments=mode, mode=mode, free_text="" if mode else None, accepted_scopes=[legacy_scope]
    )
    saved.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("FAKE_SESSION_EXIT", "0")
    monkeypatch.setenv("FAKE_REPORT_MODE", "saved")
    skill = {
        "unqualified": ["review-session"],
        "qualified": ["agent-toolkit:review-session"],
        "entry": ["agent-toolkit:atk-mode"],
        "omitted": [],
    }[resume_form]
    code, _out, error = _run(["--resume", run_id, *skill], capsys)
    assert code == 0, error
    record = json.loads(saved.read_text(encoding="utf-8"))
    assert record["status"] == "completed"
    assert record["input"] == payload["input"]
    report = frontmatter.parse_frontmatter(
        (pathlib.Path(os.environ["AGENT_TOOLKIT_PRIVATE_NOTES"]) / "2026/10" / f"{run_id}.md").read_text(encoding="utf-8")
    )
    assert report is not None and report[0]["scope"] == legacy_scope
