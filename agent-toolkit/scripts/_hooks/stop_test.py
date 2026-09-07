"""Stopイベント共通入口の判定順、例外隔離及び応答集約を検証する。"""

import importlib
import json
import pathlib
import shlex

import pytest
from _testing.helpers import SESSION_STATE_FILENAME_TEMPLATE, _write_transcript

from _hooks import stop
from _hooks import stop_gate as _stop_gate

_HOOKS_PATH = pathlib.Path(__file__).resolve().parents[2] / "hooks" / "hooks.json"


def _replace_checks(monkeypatch: pytest.MonkeyPatch, results: dict[str, tuple[str, str]]) -> None:
    """各判定の`evaluate`を指定結果へ置き換える。"""
    for module_name, result in results.items():
        module = importlib.import_module(f"_hooks.{module_name}")
        monkeypatch.setattr(module, "evaluate", lambda _payload, result=result: result)


def _state_path(directory: pathlib.Path, session_id: str) -> pathlib.Path:
    """検体のセッション状態ファイルを返す。"""
    return directory / SESSION_STATE_FILENAME_TEMPLATE.format(session_id=session_id)


def _read_state(directory: pathlib.Path, session_id: str) -> dict:
    """検体のセッション状態を返す。"""
    return json.loads(_state_path(directory, session_id).read_text(encoding="utf-8"))


def _set_state_directory(monkeypatch: pytest.MonkeyPatch, directory: pathlib.Path) -> None:
    """セッション状態とStop判定ログの一時ディレクトリを固定する。"""
    monkeypatch.setenv("TMPDIR", str(directory))
    monkeypatch.setenv("TEMP", str(directory))
    monkeypatch.setenv("TMP", str(directory))


def test_blocks_and_notifications_are_aggregated_in_check_order(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(
        monkeypatch,
        {
            "autonomous_exit": ("block", "自律終了"),
            "plan_save_advisor": ("approve", ""),
            "agents_server_session_advisor": ("notify", "未観測session"),
            "pending_question_advisor": ("block", "問いかけ"),
        },
    )

    assert stop.evaluate("{}") == {
        "decision": "block",
        "reason": "自律終了\n\n問いかけ\n\n未観測session",
    }


def test_notifications_are_aggregated_without_block(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(
        monkeypatch,
        {
            "autonomous_exit": ("approve", ""),
            "plan_save_advisor": ("approve", ""),
            "agents_server_session_advisor": ("notify", "通知1"),
            "pending_question_advisor": ("notify", "通知2"),
        },
    )

    assert stop.evaluate("{}") == {
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "additionalContext": "通知1\n\n通知2",
        }
    }


def test_all_approve_returns_empty_object(monkeypatch: pytest.MonkeyPatch) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})

    assert not stop.evaluate("{}")


def test_block_below_limit_increments_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """上限未満のblockは遮断を維持し、連続回数を増やす。"""
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"autonomous_exit": ("block", "自律終了")})
    session_id = "below-limit"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 6}),
        encoding="utf-8",
    )

    result = stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": True}))

    assert result["decision"] == "block"
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 7


def test_block_limit_approves_resets_count_and_logs_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """上限到達時はapproveし、回数と遮断判定を記録する。"""
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"autonomous_exit": ("block", "自律終了")})
    session_id = "at-limit"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 7, "autonomous_exit_invoked": False}),
        encoding="utf-8",
    )

    assert not stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": True}))
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 0
    log_text = (tmp_path / f"claude-agent-toolkit-stop-{session_id}.log").read_text(encoding="utf-8")
    assert "decision=approve_block_limit_reached" in log_text
    assert '"blocking_checks": ["autonomous_exit"]' in log_text
    assert '"autonomous_exit_invoked": false' in log_text


def test_first_block_starts_count_at_one(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """再入でない入力は既存値にかかわらず連続回数を1から始める。"""
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    _replace_checks(monkeypatch, {"pending_question_advisor": ("block", "問いかけ")})
    session_id = "first-block"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 5}),
        encoding="utf-8",
    )

    result = stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": False}))

    assert result["decision"] == "block"
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 1


def test_approve_resets_consecutive_block_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    """遮断が無い入力は連続回数を0へ戻す。"""
    _set_state_directory(monkeypatch, tmp_path)
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    session_id = "approve-reset"
    _state_path(tmp_path, session_id).write_text(
        json.dumps({"stop_consecutive_block_count": 4}),
        encoding="utf-8",
    )

    assert not stop.evaluate(json.dumps({"session_id": session_id, "stop_hook_active": True}))
    assert _read_state(tmp_path, session_id)["stop_consecutive_block_count"] == 0


def test_exception_isolated_per_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    autonomous_exit = importlib.import_module("_hooks.autonomous_exit")

    def raise_error(_payload: str) -> tuple[str, str]:
        raise RuntimeError("故障")

    monkeypatch.setattr(autonomous_exit, "evaluate", raise_error)

    assert not stop.evaluate("{}")
    assert capsys.readouterr().err == "[stop/autonomous_exit] 想定外エラー: RuntimeError: 故障\n"


def test_stop_evaluations_scan_transcript_once(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    monkeypatch.setenv("AGENT_TOOLKIT_PROCESS_LOOP_SESSION", "1")
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.delenv("DOTFILES_AUTONOMOUS_EXIT_REQUIRED", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("TEMP", str(tmp_path))
    monkeypatch.setenv("TMP", str(tmp_path))
    plan_save_advisor = importlib.import_module("_hooks.plan_save_advisor")
    monkeypatch.setattr(plan_save_advisor, "_ENV_PROCESS_LOOP_SESSION", "UNSET_PROCESS_LOOP")
    monkeypatch.setattr(plan_save_advisor, "_LEGACY_ENV_PROCESS_LOOP_SESSION", "UNSET_LEGACY_PROCESS_LOOP")
    monkeypatch.setattr(plan_save_advisor, "working_plans_root", lambda: tmp_path / "plans")
    transcript = _write_transcript(tmp_path, [])
    payload = json.dumps(
        {
            "session_id": "scan-once",
            "transcript_path": str(transcript),
            "background_tasks": [],
        }
    )
    original = _stop_gate._read_transcript_entries  # pylint: disable=protected-access
    calls = 0
    waits = 0

    def finish_transcript(_path: str) -> None:
        nonlocal waits
        waits += 1
        _write_transcript(tmp_path, [{"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}}])

    def count_reads(path: str) -> list[dict]:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(_stop_gate, "_wait_for_end_turn", finish_transcript)
    monkeypatch.setattr(_stop_gate, "_read_transcript_entries", count_reads)
    _stop_gate._PENDING_ASYNC_WORK_CACHE.clear()  # pylint: disable=protected-access

    assert stop.evaluate(payload)["decision"] == "block"
    assert waits == 1
    assert calls == 1


def test_registered_stop_checks_document_delegated_execution() -> None:
    manifest = json.loads(_HOOKS_PATH.read_text(encoding="utf-8"))
    module_names: list[str] = []
    for event_name in ("Stop", "SubagentStop"):
        for matcher_group in manifest["hooks"][event_name]:
            for hook in matcher_group["hooks"]:
                module_name = shlex.split(hook["command"])[-1]
                module = importlib.import_module(f"_hooks.{module_name}")
                module_names.extend(getattr(module, "CHECK_MODULE_NAMES", (module_name,)))

    for module_name in module_names:
        docstring = importlib.import_module(f"_hooks.{module_name}").__doc__ or ""
        assert any(line.startswith("委譲先での実行可否:") for line in docstring.splitlines()), module_name
