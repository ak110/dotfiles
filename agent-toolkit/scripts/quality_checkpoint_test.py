"""Codex品質確認通知handlerと共通entrypointの境界を検証する。"""

import json
import pathlib
import subprocess
import sys

import posttooluse
import pytest
import quality_checkpoint as subject

_SCRIPT = pathlib.Path(__file__).resolve().parent / "claude_hook.py"


def _payload(
    *,
    source: object = "compact",
    event: object = "SessionStart",
    permission_mode: object = "default",
) -> str:
    return json.dumps(
        {
            "session_id": "session-1",
            "transcript_path": None,
            "cwd": "/workspace",
            "hook_event_name": event,
            "model": "gpt-5",
            "permission_mode": permission_mode,
            "source": source,
        },
        ensure_ascii=False,
    )


def test_compact_emits_non_blocking_quality_context(capsys: pytest.CaptureFixture[str]) -> None:
    assert subject.main(_payload()) == 0
    output = json.loads(capsys.readouterr().out)
    notice = output["hookSpecificOutput"]["additionalContext"]

    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert notice.startswith("[auto-generated: agent-toolkit/quality_checkpoint]")
    assert subject.QUALITY_CHECKPOINT_NOTICE in notice
    assert "Auto-generated hook notice" in notice


def test_notice_rejects_decision_neutral_repetition() -> None:
    assert "Do not repeat searches or checks that cannot change the next decision." in subject.QUALITY_CHECKPOINT_NOTICE


@pytest.mark.parametrize("source", ["startup", "resume", "clear"])
def test_non_compact_session_start_is_silent(source: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert subject.main(_payload(source=source)) == 0
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("permission_mode", ["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"])
def test_known_permission_mode_is_accepted(permission_mode: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert subject.main(_payload(source="startup", permission_mode=permission_mode)) == 0
    assert capsys.readouterr().out == ""


def test_unknown_permission_mode_raises_value_error() -> None:
    with pytest.raises(ValueError):
        subject.main(_payload(permission_mode="unexpected"))


@pytest.mark.parametrize("field", ["unexpected", "agent_id"])
def test_unknown_session_start_field_raises_without_notice(field: str, capsys: pytest.CaptureFixture[str]) -> None:
    payload = json.loads(_payload())
    payload[field] = "silently-ignored"

    with pytest.raises(ValueError, match="既知フィールドだけ"):
        subject.main(json.dumps(payload))
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "payload_text",
    [
        "{",
        "[]",
        json.dumps({}),
        _payload(source=None),
        _payload(source=""),
        _payload(source="unexpected"),
        _payload(event="PostToolUse"),
    ],
)
def test_invalid_payload_raises_value_error(payload_text: str) -> None:
    with pytest.raises(ValueError):
        subject.main(payload_text)


@pytest.mark.parametrize(
    "field",
    [
        "session_id",
        "transcript_path",
        "cwd",
        "hook_event_name",
        "model",
        "permission_mode",
        "source",
    ],
)
def test_missing_session_start_field_raises_value_error(field: str) -> None:
    payload = json.loads(_payload())
    del payload[field]

    with pytest.raises(ValueError):
        subject.main(json.dumps(payload))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", None),
        ("transcript_path", 1),
        ("cwd", None),
        ("hook_event_name", None),
        ("model", None),
        ("permission_mode", None),
        ("source", None),
    ],
)
def test_invalid_session_start_field_type_raises_value_error(field: str, value: object) -> None:
    payload = json.loads(_payload())
    payload[field] = value

    with pytest.raises(ValueError):
        subject.main(json.dumps(payload))


def test_notice_body_has_one_shared_source() -> None:
    assert posttooluse.QUALITY_CHECKPOINT_NOTICE is subject.QUALITY_CHECKPOINT_NOTICE


def test_common_entrypoint_reports_handler_error_without_blocking(tmp_path: pathlib.Path) -> None:
    del tmp_path
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "quality_checkpoint"],
        input="{",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr.startswith("[quality_checkpoint] 想定外エラー: ValueError:")
    assert "Traceback (most recent call last):" in result.stderr
