"""UWIの通常投入・回答保存から設定PreToolUseの実際の許可応答までを通す。"""

from __future__ import annotations

import datetime
import json
import pathlib
from typing import Any

import pytest

from agent_toolkit._atk.wi import add, entries, frontmatter, operation_permissions, sync, uwi, uwi_scan
from agent_toolkit._common import approval_classifier, delegated_session, function_hook_requests, operation_approvals
from agent_toolkit._common.session_state import read_state
from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit._testing import git_repository


@pytest.fixture(name="classifier_snapshot")
def _classifier_snapshot(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    config = {
        "allow": [],
        "environment": [],
        "soft_deny": ["Local Cleanup: explicit consent required"],
        "hard_deny": ["Data Exfiltration: blocked even with consent"],
    }

    def inspect(value: str, **_kwargs: Any) -> approval_classifier.Classification:
        return approval_classifier.classify(value, config, version="2.1.325")

    monkeypatch.setattr(approval_classifier, "inspect", inspect)
    return config


@pytest.mark.cpu_limit(
    2.0,
    reason=(
        "UWIの投入・回答保存とhook判定を通し、許可と拒否の境界を確かめる"
        + "。 実Gitのworkとnotesで許可要求/回答の保存から公開"
        + "hookの消費・取消までを確認する。Linux CPU最大0.613秒"
        + "。"
    ),
)
@pytest.mark.parametrize(
    "reason,allowed,changed,scope,attempts,interactive,cancel,tool,legacy",
    [
        ("[Data Exfiltration]", False, False, "once", 1, True, False, "Bash", False),
        ("[Local Cleanup]", True, False, "count:2", 1, True, True, "Bash", False),
        ("[Local Cleanup]", True, False, "once", 1, True, False, "Bash", True),
    ],
)
def test_uwi_creation_answer_and_pretooluse(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    classifier_snapshot: dict[str, list[str]],
    reason: str,
    allowed: bool,
    changed: bool,
    scope: str,
    attempts: int,
    interactive: bool,
    cancel: bool,
    tool: str,
    legacy: bool,
) -> None:
    work = git_repository.init_repository(tmp_path / "work", origin="https://github.com/example/repo.git")
    notes = git_repository.init_repository(tmp_path / "notes", commit_message="fixture")
    storage = sync.Storage(local_only=True)
    monkeypatch.setattr(sync, "DEFAULT_STORAGE", storage)
    monkeypatch.setattr(uwi_scan, "private_notes_root", lambda: notes)
    monkeypatch.delenv(delegated_session.DELEGATED_SESSION_ENV, raising=False)
    monkeypatch.delenv(delegated_session.OWNER_SESSION_ENV, raising=False)

    def screen(_session: str, **kwargs: Any) -> bool:
        return interactive and kwargs["context_check"](
            {
                "flag_auto_mode_present": False,
                "auto_mode": {"soft_deny": classifier_snapshot["soft_deny"], "hard_deny": classifier_snapshot["hard_deny"]},
            }
        )

    monkeypatch.setattr(function_hook_requests, "interactive_available", screen)
    payload = {
        "session_id": "approval-flow",
        "tool_name": tool,
        "tool_input": {"command": "printf ok"} if tool == "Bash" else {"code": "await read('one'); await read('two');"},
        "cwd": str(work),
        "permission_mode": "auto",
        "tool_use_id": "attempt-1",
        "reason": reason,
        "hook_event_name": "PermissionDenied",
    }
    operation_permissions.observe_denial(payload)
    request = operation_permissions.pending_requests("approval-flow")[0]["request"]
    if scope != "once":
        request = operation_permissions.prepare_request("approval-flow", request["request_id"], scope=scope)
        assert request is not None
    question = "この操作を許可するか。\n\n```json\n" + operation_approvals.request_display(request) + "\n```"
    message = frontmatter.serialize_frontmatter(
        {"permission_request": json.dumps(request, ensure_ascii=False)},
        question,
    )
    if legacy:
        message = f"`Bash`で`printf ok`を`{work}`から実行する今回の操作を許可するか。"
    filename = add.add_entries(
        notes,
        messages=[message],
        target_repo="github.com/example/repo",
        source="test",
        now=datetime.datetime(2026, 11, 15, tzinfo=datetime.UTC),
        entry_type="uwi",
        question_type="choice",
        choices=request["allow_choice"] + ",拒否する",
        storage=storage,
    )[0]
    assert uwi.answer_uwi(notes, filename=filename, answer=request["allow_choice"], storage=storage)
    if changed:
        classifier_snapshot["environment"].append("Changed policy context")
    capsys.readouterr()
    execution = {key: value for key, value in payload.items() if key != "reason"}
    execution["hook_event_name"] = "PreToolUse"
    for attempt in range(attempts):
        execution["tool_use_id"] = f"execution-{attempt}"
        assert dispatch.main(json.dumps(execution)) == 0
        output = capsys.readouterr().out
        result = json.loads(output) if output else {}
        decision = result.get("hookSpecificOutput", {}).get("permissionDecision")
        assert (decision == "allow") is allowed
    if cancel:
        revoked = operation_permissions.revoke_permission("approval-flow", request["request_id"])
        assert revoked == {"changed": True, "complete": True}
    saved = frontmatter.parse_frontmatter(entries.entry_path(notes, filename).read_text(encoding="utf-8"))
    assert saved is not None
    if legacy:
        assert ("legacy_operation_approval" in saved[0]) is allowed
        if allowed:
            assert saved[0]["legacy_operation_approval"]["state"] == "consumed"
    else:
        assert saved[0]["operation_approval"]["state"] == ("revoked" if cancel else "consumed" if allowed else "approved")
    assert dispatch.main(json.dumps(execution)) == 0
    output = capsys.readouterr().out
    result = json.loads(output) if output else {}
    assert result.get("hookSpecificOutput", {}).get("permissionDecision") != "allow"


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "本物の回答イベントを記録し、入力answersによるプログラム回答は採"
        + "用しない。 実Gitの所属を持つ要求を回答イベントへ結び付け、入力によ"
        + "る偽の回答を拒否する。同群CPU最大0.613秒。"
    ),
)
@pytest.mark.usefixtures("classifier_snapshot")
def test_direct_answer_is_bound_to_displayed_request(tmp_path: pathlib.Path) -> None:
    work = git_repository.init_repository(tmp_path / "work", origin="https://github.com/example/repo.git")
    payload = {
        "session_id": "dialog-flow",
        "tool_name": "Bash",
        "tool_input": {"command": "printf ok"},
        "cwd": str(work),
        "reason": "[Local Cleanup]",
        "tool_use_id": "dialog-attempt",
        "hook_event_name": "PermissionDenied",
    }
    operation_permissions.observe_denial(payload)
    request = operation_permissions.pending_requests("dialog-flow")[0]["request"]
    text = "この操作を許可するか。\n" + operation_approvals.request_display(request)
    question = {"question": text, "options": [{"label": request["allow_choice"]}, {"label": "拒否する"}]}
    answer = {
        "session_id": "dialog-flow",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [question]},
        "tool_response": {"answers": {text: request["allow_choice"]}},
        "hook_event_name": "PostToolUse",
    }
    fake = {**answer, "tool_input": {"questions": [question], "answers": {text: request["allow_choice"]}}}
    operation_permissions.observe_answer(fake)
    assert "direct_operation_approvals" not in read_state("dialog-flow")
    operation_permissions.observe_answer(answer)
    saved = next(iter(read_state("dialog-flow")["direct_operation_approvals"].values()))
    assert operation_approvals.matches(saved["metadata"], text, request["allow_choice"], request)
