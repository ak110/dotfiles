"""計画作業rootの保存確認Stopフックの公開契約を検証する。"""

import json
import os
import pathlib
import subprocess

import _fork_runner
import _plan_file
import pytest
from _test_helpers import SESSION_STATE_FILENAME_TEMPLATE, _write_transcript

_SCRIPT = pathlib.Path(__file__).resolve().parent / "hook.py"


def _write_state(state_dir: pathlib.Path, session_id: str, state: dict) -> None:
    path = state_dir / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _run(
    payload: object,
    *,
    state_dir: pathlib.Path,
    home: pathlib.Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload_text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    env = os.environ.copy()
    env.update(
        {
            "TMPDIR": str(state_dir),
            "TEMP": str(state_dir),
            "TMP": str(state_dir),
            "HOME": str(home),
            "USERPROFILE": str(home),
        }
    )
    for name in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "DOTFILES_AUTONOMOUS_EXIT_REQUIRED",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "CLAUDE_CODE_SESSION_ID",
    ):
        env.pop(name, None)
    env.update(extra_env or {})
    return _fork_runner.run_script(_SCRIPT, argv=("plan_save_advisor",), input=payload_text, env=env)


def _payload(session_id: str, transcript: pathlib.Path, **extra: object) -> dict:
    return {"session_id": session_id, "transcript_path": str(transcript), **extra}


def _decision(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0
    return json.loads(result.stdout)


def test_existing_working_plans_block_once_then_approve(tmp_path: pathlib.Path) -> None:
    """当該セッションの編集の有無によらず、作業rootに残る計画を1回だけ通知する。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    first = plans / "first.md"
    second = plans / "second.md"
    first.write_text("# first\n", encoding="utf-8")
    second.write_text("# second\n", encoding="utf-8")
    transcript = _write_transcript(tmp_path, [])
    session_id = "block-once"
    for plan in (first, second):
        _plan_file.write_owner_record(plan, session_id=session_id)

    first_result = _decision(_run(_payload(session_id, transcript), state_dir=tmp_path, home=home))
    second_result = _decision(_run(_payload(session_id, transcript), state_dir=tmp_path, home=home))

    assert first_result["decision"] == "block"
    assert str(first) in first_result["reason"]
    assert str(second) in first_result["reason"]
    assert "残りのバンドルはその場に残して" in first_result["reason"]
    assert "atk plans commit <計画作業ルート内の計画ファイル（メイン）名>" in first_result["reason"]
    assert not second_result


def test_nested_working_plans_are_reported(tmp_path: pathlib.Path) -> None:
    """日付階層へ置かれた計画も通知の対象とする。"""
    home = tmp_path / "home"
    nested = home / ".claude" / "plans" / "2026" / "09"
    nested.mkdir(parents=True)
    plan = nested / "01-nested-a1b2.md"
    plan.write_text("# nested\n", encoding="utf-8")
    _plan_file.write_owner_record(plan, session_id="nested")
    transcript = _write_transcript(tmp_path, [])

    result = _decision(_run(_payload("nested", transcript), state_dir=tmp_path, home=home))

    assert result["decision"] == "block"
    assert str(plan) in result["reason"]


def test_absent_working_root_approves(tmp_path: pathlib.Path) -> None:
    """作業rootが存在しない場合は通知しない。"""
    transcript = _write_transcript(tmp_path, [])

    result = _run(_payload("no-root", transcript), state_dir=tmp_path, home=tmp_path / "home")

    assert not _decision(result)


@pytest.mark.parametrize(
    ("state", "payload_extra", "environment"),
    [
        ({"working_plan_save_notified": True}, {}, {}),
        ({}, {"stop_hook_active": True}, {}),
        ({}, {"background_tasks": [{"type": "subagent", "id": "pending"}]}, {}),
        ({}, {}, {"AGENT_TOOLKIT_DELEGATED_SESSION": "1"}),
        ({}, {}, {"AGENT_TOOLKIT_PROCESS_LOOP_SESSION": "1"}),
        ({}, {}, {"DOTFILES_AUTONOMOUS_EXIT_REQUIRED": "1"}),
    ],
)
def test_suppression_conditions_approve(
    tmp_path: pathlib.Path,
    state: dict,
    payload_extra: dict,
    environment: dict[str, str],
) -> None:
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    plan = plans / "plan.md"
    plan.write_text("# plan\n", encoding="utf-8")
    transcript = _write_transcript(tmp_path, [])
    session_id = "suppressed"
    _plan_file.write_owner_record(plan, session_id=session_id)
    _write_state(tmp_path, session_id, state)

    result = _run(
        _payload(session_id, transcript, **payload_extra),
        state_dir=tmp_path,
        home=home,
        extra_env=environment,
    )

    assert not _decision(result)


def test_paths_outside_the_working_root_approve(tmp_path: pathlib.Path) -> None:
    """保存先へ戻した計画と作業rootの対象外ファイルは通知しない。"""
    home = tmp_path / "home"
    working_root = home / ".claude" / "plans"
    working_root.mkdir(parents=True)
    (working_root / "note.txt").write_text("対象外\n", encoding="utf-8")
    private_plan = tmp_path / "private-notes" / "plans" / "2026" / "09" / "01-saved-a1b2.md"
    private_plan.parent.mkdir(parents=True)
    private_plan.write_text("# saved\n", encoding="utf-8")
    _plan_file.write_owner_record(private_plan, session_id="outside-working-root")
    transcript = _write_transcript(tmp_path, [])

    result = _run(_payload("outside-working-root", transcript), state_dir=tmp_path, home=home)

    assert not _decision(result)


@pytest.mark.parametrize("payload", ["not json", {}, {"session_id": ""}])
def test_invalid_payload_approves(tmp_path: pathlib.Path, payload: object) -> None:
    result = _run(payload, state_dir=tmp_path, home=tmp_path / "home")
    assert not _decision(result)


@pytest.mark.parametrize(
    ("owner_records", "expected_blocked"),
    [
        pytest.param({}, (), id="所有記録なし"),
        pytest.param({"own.md": "current"}, ("own.md",), id="自セッションの所有記録"),
        pytest.param({"other.md": "another"}, (), id="他セッションの所有記録"),
        pytest.param({"own.md": "current", "other.md": "another"}, ("own.md",), id="自他の混在"),
    ],
)
def test_notified_plans_are_limited_to_the_current_session(
    tmp_path: pathlib.Path,
    owner_records: dict[str, str],
    expected_blocked: tuple[str, ...],
) -> None:
    """所有記録が当該セッションを示す計画だけを通知し、他は承認する。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    for name in ("own.md", "other.md"):
        (plans / name).write_text(f"# {name}\n", encoding="utf-8")
    for name, session in owner_records.items():
        _plan_file.write_owner_record(plans / name, session_id=session)
    transcript = _write_transcript(tmp_path, [])

    result = _decision(_run(_payload("current", transcript), state_dir=tmp_path, home=home))

    if not expected_blocked:
        assert not result
        return
    assert result["decision"] == "block"
    for name in expected_blocked:
        assert str(plans / name) in result["reason"]
    for name in ("own.md", "other.md"):
        if name not in expected_blocked:
            assert str(plans / name) not in result["reason"]


@pytest.mark.parametrize("record", ["{不正なJSON", '{"recorded_at": "2026-09-03T00:00:00+09:00"}'])
def test_unreadable_owner_record_approves(tmp_path: pathlib.Path, record: str) -> None:
    """所有記録をJSONとして解釈できない場合と`session_id`が無い場合は通知しない。"""
    home = tmp_path / "home"
    plans = home / ".claude" / "plans"
    plans.mkdir(parents=True)
    plan = plans / "broken.md"
    plan.write_text("# broken\n", encoding="utf-8")
    _plan_file.owner_record_path(plan).write_text(record, encoding="utf-8")
    transcript = _write_transcript(tmp_path, [])

    result = _run(_payload("current", transcript), state_dir=tmp_path, home=home)

    assert not _decision(result)
