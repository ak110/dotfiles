"""Stopと送達前が同じ待機・是正の入力列を処理する契約。 pyfltr: allow-comment"""

import json
import pathlib
import shutil
import subprocess
from collections.abc import Callable

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_PROMPT
from agent_toolkit._hooks import stop
from agent_toolkit._hooks.bugfix_gate_test import _BUGFIX, _TYPED_REPORT, _classified, _human, _payload
from agent_toolkit._hooks.termination_order_advisor_test import _start_skill
from agent_toolkit._testing.helpers import repl_send_to_user_entries
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process

_SEND = "mcp__plugin_agent-toolkit_agents_server__send_to_user"
_CAPABILITY = {
    "type": "attachment",
    "attachment": {
        "type": "hook_additional_context",
        "hookEvent": "UserPromptSubmit",
        "hookName": "prompt.submit",
        "content": ['<atk-auto source="send-to-user-contract" kind="notice">'],
    },
}
_WARNING = {
    "type": "attachment",
    "attachment": {
        "type": "hook_additional_context",
        "content": ['<atk-auto source="fixture" kind="warn">未処置の警告</atk-auto>'],
    },
}
_TASK = (
    "<task-notification><task-id>wait-task</task-id><tool-use-id>toolu_wait</tool-use-id>"
    "<status>completed</status></task-notification>"
)


@pytest.fixture(autouse=True)
def _interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "AGENT_TOOLKIT_DELEGATED_SESSION",
        "AGENT_TOOLKIT_OWNER_SESSION",
        "AGENT_TOOLKIT_PROCESS_LOOP_SESSION",
        "AGENT_TOOLKIT_RUN_SKILL_ID",
    ):
        monkeypatch.delenv(name, raising=False)


def _automatic(text: str) -> dict:
    return {"type": "attachment", "attachment": {"type": "queued_command", "prompt": text}}


def _bash_wait() -> list[dict]:
    return [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_wait",
                        "name": "Bash",
                        "input": {"command": "atk agents wait", "run_in_background": True},
                    }
                ]
            },
        },
        {
            "type": "user",
            "toolUseResult": {"backgroundTaskId": "wait-task"},
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": "toolu_wait"}],
            },
        },
    ]


def _machine_input(source: str) -> list[dict]:
    if source == "periodic":
        return [{"type": "user", "isMeta": True, "message": {"content": PERIODIC_RECHECK_PROMPT}}]
    if source == "result":
        return [_automatic('<atk-auto source="runtime-changes" kind="notice">反映コマンドの応答</atk-auto>')]
    if source == "task":
        other = _TASK.replace("toolu_wait", "toolu_other").replace("wait-task", "other-task")
        return [{"type": "user", "origin": {"kind": "task-notification"}, "message": {"content": other}}]
    return []


@pytest.mark.parametrize("machine", ["none", "periodic", "task"])
@pytest.mark.parametrize("same_work", [False, True])
@pytest.mark.parametrize("fresh_correction", [False, True])
def test_wait_only_defers_the_correction_owning_it(
    tmp_path: pathlib.Path, machine: str, same_work: bool, fresh_correction: bool
) -> None:
    entries = [_human("前の不具合を直して。"), _CAPABILITY, *_classified(), *_BUGFIX]
    if fresh_correction:
        entries.extend(repl_send_to_user_entries("repl_prior", {"message": _TYPED_REPORT}, ok=True))
    if same_work and not fresh_correction:
        entries.extend(_bash_wait())
    if fresh_correction:
        entries.extend([_human("別の不具合を直して。"), *_classified(call_id="new")])
        if same_work:
            entries.extend(_bash_wait())
    entries.extend(_machine_input(machine))
    payload = _payload(
        entries,
        tmp_path,
        last_assistant_message="…",
        background_tasks=[{"id": "wait-task" if same_work else "unrelated", "type": "Bash", "status": "running"}],
    )
    result = stop.evaluate(json.dumps(payload))
    if machine != "none" and same_work and not fresh_correction:
        assert result == {}
    else:
        assert result["decision"] == "block"
        assert ("起動していない" in str(result["reason"])) is fresh_correction


@pytest.mark.parametrize("answer", ["", "…", "新しい確認結果がある。"])
def test_unchanged_periodic_wait_does_not_repost_previous_correction(tmp_path: pathlib.Path, answer: str) -> None:
    entries = [
        _human("前の不具合を直して。"),
        _CAPABILITY,
        *_classified(),
        *_BUGFIX,
        *repl_send_to_user_entries("repl_prior", {"message": _TYPED_REPORT}, ok=True),
        *_machine_input("periodic"),
    ]
    payload = _payload(
        entries,
        tmp_path,
        last_assistant_message=answer,
        background_tasks=[{"id": "wait-task", "type": "Bash", "status": "running"}],
    )
    result = stop.evaluate(json.dumps(payload))
    if answer and answer != "…":
        assert "delivered_repost_gate" in str(result)
    else:
        assert result == {}


def _send(entries: list[dict], tmp_path: pathlib.Path, message: str, *, repl: bool) -> dict:
    result = _run_hook_in_process(
        "pretooluse",
        _payload(
            entries,
            tmp_path,
            tool_name=_SEND,
            tool_use_id="repl_send" if repl else "toolu_send",
            tool_input={"message": message},
        ),
    )
    assert result.returncode == 0 and not result.stderr
    return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout.strip() else {}


@pytest.mark.parametrize("repl", [False, True])
@pytest.mark.parametrize("warned", [False, True])
def test_correction_recovery_sends_once(tmp_path: pathlib.Path, repl: bool, warned: bool) -> None:
    entries = [_human("ここが壊れている。"), _CAPABILITY, *_classified()]
    if warned:
        entries.append(_WARNING)
    output = _send(entries, tmp_path, "## 是正報告", repl=repl)
    reason = output["permissionDecisionReason"]
    for label in ("対策:", "回帰防止:", "類似見直し:", "再発防止:"):
        assert label in reason
    assert ("hook通知への対応:" in reason) is warned
    assert reason.index("起動していない") < reason.index("対策:") < reason.index("1回送達")
    assert "再送・再掲せず `…` だけ" in reason
    assert "新しい入力や成果" in reason

    blocked_stop = stop.evaluate(json.dumps(_payload(entries, tmp_path, last_assistant_message="…")))
    stop_reason = str(blocked_stop["reason"])
    assert stop_reason.index("対策:") < stop_reason.index("1回送達")
    assert "再送・再掲せず `…` だけ" in stop_reason

    entries.extend(_BUGFIX)
    report = "## 是正報告\n" + _TYPED_REPORT
    if warned:
        report += "\nhook通知への対応: 警告の原因を解消した。"
    assert _send(entries, tmp_path, report, repl=repl).get("permissionDecision") != "deny"
    call = "repl_delivered" if repl else "toolu_delivered"
    if repl:
        delivered = repl_send_to_user_entries(call, {"message": report}, ok=True)
    else:
        delivered = [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "id": call, "name": _SEND, "input": {"message": report}}]},
            },
            _ui_result(call, True),
        ]
    entries.extend(delivered)
    entries.append({"type": "attachment", "attachment": {"commandMode": "task-notification", "prompt": _TASK}})
    assert stop.evaluate(json.dumps(_payload(entries, tmp_path, last_assistant_message="…"))) == {}
    entries.append(_human("別の不具合を直して。"))
    assert stop.evaluate(json.dumps(_payload(entries, tmp_path, last_assistant_message="…")))["decision"] == "block"


def _ui_result(call: str, receipt: dict | bool) -> dict:
    return {
        "type": "user",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": call,
                    "is_error": False,
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(receipt if isinstance(receipt, dict) else {"id": "c" * 32, "ok": receipt}),
                        }
                    ],
                }
            ]
        },
    }


def runtime_review_stop(tmp_path: pathlib.Path, receipt: dict, machine: str, *, final: bool = False) -> dict[str, object]:
    call = "toolu_change"
    entries = [
        _CAPABILITY,
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "id": call,
                        "name": "mcp__plugin_agent-toolkit_agents_server__apply_runtime_changes",
                        "input": {"targets": ["plugins", "agents_server", "pyfltr"]},
                    }
                ]
            },
        },
        _ui_result(call, receipt),
        *_machine_input(machine),
    ]
    payload = _payload(entries, tmp_path, last_assistant_message="## 作業完了報告\n作業を完了した。" if final else "…")
    _start_skill(
        payload["session_id"], pathlib.Path(payload["transcript_path"]), "agent-toolkit:atk-mode", arguments="process-wi"
    )
    return stop.evaluate(json.dumps(payload))


@pytest.mark.parametrize("machine", ["none", "periodic", "task", "result"])
@pytest.mark.parametrize("ok", [False, True])
@pytest.mark.parametrize("final", [False, True])
def test_runtime_receipt_allows_pause_but_not_completion(tmp_path: pathlib.Path, machine: str, ok: bool, final: bool) -> None:
    result = runtime_review_stop(tmp_path, {"id": "c" * 32, "ok": ok}, machine, final=final)
    if ok and not final and machine != "result":
        assert result == {}
    else:
        assert "termination_order_advisor" in str(result)


@pytest.mark.cpu_limit(1.0, reason="実Stopに続けてClaude CodeのFunction hooks試験用コピーを構成する")
def test_runtime_stop_then_function_hook(tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]]) -> None:
    claude = shutil.which("claude", path=host_environ().get("PATH", ""))
    if claude is None:
        pytest.skip("Claude CodeのFunction hooks試験ランタイムが未導入")
    receipt = {
        "id": "c" * 32,
        "ok": True,
        "reason": (
            "変更反映を予約した。まだ反映完了ではない。現在のターンを終えて反映結果を待て。"
            "agents_serverの再接続は実行中の子turnを中断し、既存の復旧で同じ会話を再開する。"
        ),
    }
    stop_result = runtime_review_stop(tmp_path, receipt, "periodic")
    evidence = {"receipt": receipt, "prompt": PERIODIC_RECHECK_PROMPT, "answer": "…", "output": stop_result}
    source = pathlib.Path(__file__).resolve().parents[2]
    plugin = tmp_path / "agent-toolkit"
    shutil.copytree(source / ".claude-plugin", plugin / ".claude-plugin")
    shutil.copytree(source / "hooks", plugin / "hooks", ignore=shutil.ignore_patterns("*.test.ts", "*.test.tsx"))
    common = plugin / "agent_toolkit" / "_common"
    common.mkdir(parents=True)
    shutil.copyfile(source / "agent_toolkit" / "_common" / "output-policy.ts", common / "output-policy.ts")
    name = "ui_requests.test.tsx"
    native_test = (source / "hooks" / name).read_text(encoding="utf-8")
    original = 'JSON.parse("null") as RuntimeStopEvidence | null'
    assert native_test.count(original) == 1
    fixture = json.dumps(json.dumps(evidence, ensure_ascii=False), ensure_ascii=False)
    injected = native_test.replace(original, f"JSON.parse({fixture}) as RuntimeStopEvidence | null")
    (plugin / "hooks" / name).write_text(injected, encoding="utf-8")
    result = subprocess.run(
        [claude, "plugin", "test", str(plugin)],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "(pass) 反映予約はターン後に公開コマンドを一度実行し結果を通知する" in output


@pytest.mark.parametrize("failed_receipt", [False, True])
@pytest.mark.parametrize("started_before_send", [False, True])
def test_only_successful_report_after_bugfix_is_reusable(
    tmp_path: pathlib.Path, failed_receipt: bool, started_before_send: bool
) -> None:
    entries = [_human("不具合を直して。"), _CAPABILITY, *_classified()]
    report = repl_send_to_user_entries("repl_report", {"message": _TYPED_REPORT}, ok=not failed_receipt)
    entries.extend([*_BUGFIX, *report] if started_before_send else [*report, *_BUGFIX])
    result = stop.evaluate(json.dumps(_payload(entries, tmp_path, last_assistant_message="…")))
    assert (result.get("decision") != "block") is (started_before_send and not failed_receipt)
    if failed_receipt:
        assert "診断を解消し" in str(result["reason"])
        assert "再送・再掲せず `…` だけ" in str(result["reason"])


@pytest.mark.parametrize("recheck", [False, True])
@pytest.mark.parametrize(
    "notification",
    [
        "queued",
        "completed",
        "recovered",
        "partial",
        "read_failed",
        "failed",
        "unrelated",
        "missing",
        "bash_preview",
        "bash_marker",
        "bash_full",
        "saved_read",
        "repeated",
    ],
)
def test_wait_reuses_notification_before_starting_another_wait(
    tmp_path: pathlib.Path, notification: str, recheck: bool
) -> None:
    entries = [_human("委譲先の結果を待って。"), _CAPABILITY, *_classified("非是正"), *_bash_wait()]
    path = str(tmp_path / "saved-wait")
    saved = str(tmp_path / "persisted-wait")
    task = _TASK.replace("</task-notification>", f"<output-file>{path}</output-file></task-notification>")
    if notification == "failed":
        task = _TASK.replace("completed", "failed")
    elif notification == "unrelated":
        task = task.replace("toolu_wait", "toolu_other").replace("wait-task", "other-task")
    notice = {"type": "attachment", "attachment": {"commandMode": "task-notification", "prompt": task}}
    if notification == "queued":
        entries.append({"type": "queue-operation", "operation": "enqueue", "content": task})
    elif notification != "missing":
        entries.append(notice)
    if notification in {"bash_preview", "bash_marker", "bash_full", "saved_read"}:
        receipt = {"persistedOutputPath": saved} if notification in {"bash_preview", "saved_read"} else {}
        content = "終了: 1件\n終了行: session_id=previous status=completed"
        if notification == "bash_marker":
            content = f"<persisted-output>\nOutput too large (24KB). Full output saved to: {saved}\n</persisted-output>"
        entries.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_cat",
                                "name": "Bash",
                                "input": {"command": f'cat "{path}"'},
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "toolUseResult": receipt,
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_cat",
                                "is_error": False,
                                "content": content,
                            }
                        ]
                    },
                },
            ]
        )
    if notification in {"recovered", "partial", "read_failed", "saved_read", "repeated"}:
        args: dict[str, str | int] = {"file_path": saved if notification == "saved_read" else path}
        if notification == "partial":
            args["limit"] = 1
        entries.extend(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_read",
                                "name": "Read",
                                "input": args,
                            }
                        ]
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_read",
                                "is_error": notification == "read_failed",
                                "content": "終了: 1件\n終了行: session_id=previous status=completed",
                            }
                        ]
                    },
                },
            ]
        )
    if notification == "repeated":
        entries.append(notice)
    if recheck:
        entries.extend(_machine_input("periodic"))
    payload = _payload(entries, tmp_path, last_assistant_message="…", background_tasks=[])
    session_state.update_state(
        payload["session_id"],
        lambda state: {
            **state,
            "agents_server_sessions": {
                "remaining": {"owner_agent_id": "main", "pending_observation": True},
            },
        },
    )
    result = stop.evaluate(json.dumps(payload))
    assert "delivered_repost_gate" not in str(result)
    if notification == "queued":
        assert "agents_server_session_advisor" not in str(result)
        assert result.get("continue") is False
    else:
        body = str(result)
        assert "agents_server_session_advisor" in body
        recovered_or_unrelated = notification in {
            "recovered",
            "bash_full",
            "saved_read",
            "repeated",
            "failed",
            "unrelated",
            "missing",
        }
        assert ("バックグラウンド起動" in body) is recovered_or_unrelated
        assert ("保存出力と終了状態を回収" in body) is not recovered_or_unrelated
