"""Stopと送達前が同じ待機・送達の入力列を処理する契約。 pyfltr: allow-comment"""

import json
import pathlib
import shutil
import subprocess
from collections.abc import Callable

import pytest

from agent_toolkit._common import session_state
from agent_toolkit._common.periodic_recheck import PERIODIC_RECHECK_PROMPT
from agent_toolkit._hooks import delivered_repost_gate, stop, termination_evidence
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


_REPORT = "設定の解釈が原因だった。対象を修正し、変更範囲の検証で正常な結果を確かめた。"


def _human(text: str) -> dict:
    return {"type": "user", "origin": {"kind": "human"}, "message": {"content": text}}


def _payload(entries: list[dict], tmp_path: pathlib.Path, **extra: object) -> dict:
    path = tmp_path / "transcript.jsonl"
    path.write_text("\n".join(json.dumps(entry, ensure_ascii=False) for entry in entries), encoding="utf-8")
    return {"session_id": "intermission", "transcript_path": str(path), "cwd": str(tmp_path), **extra}


def _understood() -> list[dict]:
    return repl_send_to_user_entries("repl_understanding", {"message": "発話の理解: ご依頼の対象を修正する。"}, ok=True)


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


@pytest.mark.parametrize("answer", ["", "…", "新しい確認結果がある。"])
def test_unchanged_periodic_wait_does_not_repost_previous_correction(tmp_path: pathlib.Path, answer: str) -> None:
    entries = [
        _human("前の不具合を直して。"),
        _CAPABILITY,
        *_understood(),
        *repl_send_to_user_entries("repl_prior", {"message": _REPORT}, ok=True),
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
    entries = [_human("ここが壊れている。"), _CAPABILITY, *_understood()]
    if warned:
        entries.append(_WARNING)
    report = "## 是正報告\n" + _REPORT
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
    assert (
        stop.evaluate(json.dumps(_payload(entries, tmp_path, last_assistant_message="新しい成果を報告する。")))["decision"]
        == "block"
    )


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
    termination_evidence.observe_tool(
        json.dumps(
            {
                **payload,
                "tool_use_id": "toolu_work",
                "tool_name": "Skill",
                "tool_input": {"skill": "agent-toolkit:atk-mode", "args": "process-wi"},
                "tool_response": "起動",
            }
        ),
        after=True,
    )
    return stop.evaluate(json.dumps(payload))


@pytest.mark.parametrize("machine", ["none", "periodic", "task", "result"])
@pytest.mark.parametrize("ok", [False, True])
@pytest.mark.parametrize("final", [False, True])
def test_runtime_receipt_allows_pause_but_not_completion(tmp_path: pathlib.Path, machine: str, ok: bool, final: bool) -> None:
    result = runtime_review_stop(tmp_path, {"id": "c" * 32, "ok": ok}, machine, final=final)
    if final:
        assert "delivered_repost_gate" in str(result)
    else:
        assert result == {}


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
        [claude, "plugin", "test", "--file", str(plugin / "hooks" / name), str(plugin)],
        cwd=tmp_path,
        env=host_environ(),
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=25,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    reports = [
        line.removeprefix("claude-plugin-test-report ")
        for line in result.stdout.splitlines()
        if line.startswith("claude-plugin-test-report ")
    ]
    assert len(reports) == 1, output
    report = json.loads(reports[0])
    assert report["file"] == str(plugin / "hooks" / name)
    assert not report.get("error"), output
    assert report["results"] and all(test["failure"] is None for test in report["results"]), output
    assert any(test["title"] == "反映予約はターン後に公開コマンドを一度実行し結果を通知する" for test in report["results"])


@pytest.mark.parametrize("failed_receipt", [False, True])
def test_only_successful_report_is_delivered(tmp_path: pathlib.Path, failed_receipt: bool) -> None:
    entries = [_human("不具合を直して。"), _CAPABILITY]
    entries.extend(repl_send_to_user_entries("repl_report", {"message": _REPORT}, ok=not failed_receipt))
    assert bool(list(delivered_repost_gate.delivered_messages(entries))) is not failed_receipt
    # 表示失敗時は未送達の本文を通常応答で伝え、成功時は再掲せずに終えられる。 pyfltr: allow-comment
    closing = _REPORT if failed_receipt else "…"
    result = stop.evaluate(json.dumps(_payload(entries, tmp_path, last_assistant_message=closing)))
    assert result.get("decision") != "block"


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
    entries = [_human("委譲先の結果を待って。"), _CAPABILITY, *_understood(), *_bash_wait()]
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
