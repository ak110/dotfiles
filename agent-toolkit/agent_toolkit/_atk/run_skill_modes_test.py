"""run skill modesの契約を検証する。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_toolkit._atk import run_skill
from agent_toolkit._common import session_review, skill_modes
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _records, _run, _startup_input

__all__ = ["_engine_env"]


@pytest.mark.usefixtures("leaf_git_commands")
@pytest.mark.cpu_limit(
    1.0,
    reason=("実Gitの状態・履歴を使う結合境界。修正前Linux4並列全件計測の所有CPU最大0.456秒。"),
)
@pytest.mark.parametrize(
    "engine,arguments",
    [
        ("claude", None),
        ("claude", "auto"),
        ("claude", "batchx"),
        ("claude", "batch auto 任意の着眼点"),
        ("codex", "batch auto 任意の着眼点"),
    ],
)
def test_batch_uses_existing_runner_and_report_receipt(engine_env, tmp_path, monkeypatch, capsys, engine, arguments) -> None:
    """2会話の実prepareから偽ホスト・同期済み報告・範囲確定まで公開CLIで検証する。"""
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
    code, _out, error = _run([*options, "agent-toolkit:review-session"], capsys)
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


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("arguments", [None, "追加指示"])
def test_final_goal_is_only_skill_completion(engine_env, monkeypatch, capsys, engine, arguments):
    """公開起動の最終argvで、引数の有無によらず短い目的を保持する。"""
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
    """作業の修飾名と、入口`atk-mode`への作業の名指し（`--args 'process-wi ...'`）は同じ起動入力と目的文になる。"""
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
    """入口`atk-mode`で作業を名指しして起動した実行は、入口の形でも作業の修飾名でもスキル名の省略でも再開できる。"""
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
    assert len(_records(engine_env, "session")) == 2


def test_resume_rejects_entry_form_for_other_work(engine_env, monkeypatch, capsys):
    """入口で名指しした別の作業や無関係なスキルは、元の実行と一致しないため再開しない。"""
    set_orchestrate_environment(monkeypatch, "claude:test-model/low")
    monkeypatch.setenv("FAKE_SESSION_EXIT", "1")
    code, _out, error = _run(["--target-repo", str(engine_env.repo), skill_modes.PROCESS_WI], capsys)
    assert code != 0, error
    run_id = _records(engine_env, "session")[0]["env"]["AGENT_TOOLKIT_RUN_SKILL_ID"]
    for extra in (["--args", "review-session", "agent-toolkit:atk-mode"], ["my-skill"]):
        code, _out, error = _run(["--resume", run_id, *extra], capsys)
        assert code == 2 and ("一致しない" in error or "変更できない" in error)
    assert len(_records(engine_env, "session")) == 1
