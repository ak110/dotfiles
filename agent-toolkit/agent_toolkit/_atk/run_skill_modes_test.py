"""run skill modesの契約を検証する。"""

from __future__ import annotations

import argparse
import json
import pathlib

import pytest

from agent_toolkit._atk import run_skill
from agent_toolkit._common import session_review
from agent_toolkit._testing.process_loop_support import set_orchestrate_environment
from agent_toolkit._testing.run_skill_support import _engine_env, _records, _run, _startup_input

__all__ = ["_engine_env"]


@pytest.mark.parametrize("engine", ["claude", "codex"])
def test_batch_uses_existing_runner_and_report_receipt(engine_env, tmp_path, monkeypatch, capsys, engine) -> None:
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
    code, _out, error = _run(["--args", "batch auto 任意の着眼点", "agent-toolkit:review-session"], capsys)
    assert code == 0, error
    assert not session_review.pending()
    assert len(_records(engine_env, "session")) == 1
    manifests = list((session_review.directory() / "runs").glob("*.json"))
    assert len(manifests) == 1
    value = session_review.read(manifests[0])
    assert value["status"] == "completed" and len(value["prepared"]) == 2
    records = _records(engine_env, "session")
    assert records[0]["argv"][-1] == (
        ("/goal " if engine == "claude" else "") + '<atk-auto source="run-skill" kind="goal">\n'
        "このセッションが担当するスキル`agent-toolkit:review-session batch`の工程を完遂する"
        "（起動元が終了後に行う検収と確定は含まない）\n</atk-auto>"
    )
    data = _startup_input(records[0], monkeypatch, capsys)
    assert data["arguments"] == "batch auto 任意の着眼点"
    assert data["free_text"] == "auto 任意の着眼点"
    assert data["target_repo"] == "example.com/team/repo"
    assert pathlib.Path(data["batch_manifest"]) == manifests[0]


@pytest.mark.parametrize("arguments,is_batch", [(None, False), ("auto", False), ("batchx", False), ("batch auto", True)])
def test_dispatch_selects_exact_batch_mode(monkeypatch, arguments, is_batch):
    from agent_toolkit._atk import run_skill_batch  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    observed = []

    def once(args, **_kwargs):
        observed.append((False, args.args))
        return 0

    def batch(args, execute, **_kwargs):
        observed.append((True, args.args))
        assert execute is once
        return 0

    monkeypatch.setattr(run_skill, "_dispatch_once", once)
    monkeypatch.setattr(run_skill_batch, "dispatch", batch)
    args = argparse.Namespace(skill="agent-toolkit:review-session", args=arguments)
    assert run_skill.dispatch(args) == 0
    assert observed == [(is_batch, arguments)]


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
        "（起動元が終了後に行う検収と確定は含まない）\n</atk-auto>"
    )
    assert _records(engine_env, "session")[0]["argv"][-1] == ("/goal " if engine == "claude" else "") + expected
    data = _startup_input(_records(engine_env, "session")[0], monkeypatch, capsys)
    assert data["arguments"] == data["free_text"] == arguments
    assert data["accepted_scopes"] == ["my-skill"]


def test_mode_lock_ignores_focus_and_keeps_other_modes_separate(tmp_path):
    from agent_toolkit._common import skill_modes  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    def lock(arguments):
        return run_skill._lock_path(  # noqa: SLF001  # pylint: disable=protected-access
            tmp_path, skill_modes.invocation_scope("agent-toolkit:process-wi", arguments)
        )

    assert lock("single-lane A") == lock("single-lane B")
    assert lock("single-lane A") != lock("all-repos A")
    assert lock("通常の追加指示") != lock("single-lane A")


@pytest.mark.parametrize("engine", ["claude", "codex"])
@pytest.mark.parametrize("arguments", [None, "single-lane 追記  保持\n", "all-repos 追加指示", "unknown single-lane"])
def test_process_modes_reach_public_runner_with_original_arguments(engine_env, monkeypatch, capsys, engine, arguments):
    set_orchestrate_environment(monkeypatch, f"{engine}:test-model/low")
    argv = ["--target-repo", str(engine_env.repo)]
    if arguments is not None:
        argv.extend(["--args", arguments])
    argv.append("agent-toolkit:process-wi")
    code, _out, error = _run(argv, capsys)
    assert code == 0, error
    record = _records(engine_env, "session")[0]
    data = json.loads(record["env"]["AGENT_TOOLKIT_SESSION_START_INPUT"])
    assert data["arguments"] == arguments
    mode = (
        "single-lane"
        if arguments and arguments.startswith("single-lane ")
        else "all-repos"
        if arguments and arguments.startswith("all-repos ")
        else None
    )
    assert data["mode"] == mode
    expected_skill = "agent-toolkit:process-wi" + (" " + mode if mode else "")
    expected = (
        f'<atk-auto source="run-skill" kind="goal">\nこのセッションが担当するスキル`{expected_skill}`の工程を完遂する'
        "（起動元が終了後に行う検収と確定は含まない）\n</atk-auto>"
    )
    assert record["argv"][-1] == ("/goal " if engine == "claude" else "") + expected
