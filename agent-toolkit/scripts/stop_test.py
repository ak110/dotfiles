"""Stopイベント共通入口の判定順、例外隔離及び応答集約を検証する。"""

import importlib
import json
import pathlib
import shlex

import _stop_gate
import pytest
import stop
from _test_helpers import _write_transcript

_HOOKS_PATH = pathlib.Path(__file__).resolve().parents[1] / "hooks" / "hooks.json"


def _replace_checks(monkeypatch: pytest.MonkeyPatch, results: dict[str, tuple[str, str]]) -> None:
    """各判定の`evaluate`を指定結果へ置き換える。"""
    for module_name, result in results.items():
        module = importlib.import_module(module_name)
        monkeypatch.setattr(module, "evaluate", lambda _payload, result=result: result)


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


def test_exception_isolated_per_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    _replace_checks(monkeypatch, {name: ("approve", "") for name in stop.CHECK_MODULE_NAMES})
    autonomous_exit = importlib.import_module("autonomous_exit")

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
    plan_save_advisor = importlib.import_module("plan_save_advisor")
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
                module = importlib.import_module(module_name)
                module_names.extend(getattr(module, "CHECK_MODULE_NAMES", (module_name,)))

    for module_name in module_names:
        docstring = importlib.import_module(module_name).__doc__ or ""
        assert any(line.startswith("委譲先での実行可否:") for line in docstring.splitlines()), module_name
