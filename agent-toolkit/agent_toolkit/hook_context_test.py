"""登録hook入口の最終通知と、保存した全文・配送記録の対応を検証する。

handler出力だけを合成し、hook.mainの合流・保存・最終配送を実行する。
入力のturn_idによるホスト識別はhost.pyの公開契約を使う。
"""

import importlib
import json
import os
import pathlib
import re
import shlex

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import session_state
from agent_toolkit._hooks import notice, rules_context
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.mark.parametrize("additional", [False, True])
def test_codex_final_context_includes_long_decision_reason_in_budget(monkeypatch, additional):
    """判定理由だけが長い応答も、拒否と詳細を保持した短い最終通知へ変換する。"""
    module = importlib.import_module("agent_toolkit._hooks.pretooluse")
    reason = "拒否した境界の詳細: " + "\n".join(f"/owned-resource-{number:04d}/ requires ownership" for number in range(600))

    def handler(_payload, **_kwargs):
        specific = {"permissionDecision": "deny", "permissionDecisionReason": reason}
        if additional:
            specific["additionalContext"] = "追加通知の固有本文"
        notice.emit_response({"hookSpecificOutput": specific})
        return 0

    monkeypatch.setattr(module, "main", handler)
    result = _run_hook_in_process("pretooluse", {"session_id": "long-decision", "turn_id": "turn"})
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    context = specific.get("additionalContext", "")
    assert len((context + specific["permissionDecisionReason"]).encode("utf-8")) <= 2000
    match = re.search(r"保存先: (.+)\n", context)
    assert match is not None
    saved = pathlib.Path(match[1])
    command = (
        "Get-Content -LiteralPath '" + str(saved).replace("'", "''") + "' -Encoding utf8"
        if os.name == "nt"
        else "cat -- " + shlex.quote(str(saved))
    )
    assert f"`{command}`を実行" in context
    full = saved.read_text(encoding="utf-8")
    assert reason in full
    assert ("追加通知の固有本文" in full) is additional


@pytest.mark.parametrize("failure", [False, True])
def test_codex_final_context_spills_whole_notices_without_consuming_receipt(monkeypatch, failure):
    """追加入力の障害通知も全文へ保存し、短い参照では資料取得を確定しない。"""
    module = importlib.import_module("agent_toolkit._hooks.pretooluse")
    identifier = "bounded-final"
    original = "\n\n".join(f"通知{index}: /資料/" + "長いパスと取得例" * 80 for index in range(5))

    def handler(_payload, **_kwargs):
        notice.stage_delivery(original, identifier, rules_context.OPERATION_SKILL_READY_KEY, "main", "source-body")
        notice.emit_response(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": original,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "独立した所有境界",
                }
            }
        )
        if failure:
            raise RuntimeError("合流後の障害" + "診断" * 1500)
        return 0

    monkeypatch.setattr(module, "main", handler)
    payload = {"session_id": identifier, "turn_id": "turn", "hook_event_name": "PreToolUse"}
    result = _run_hook_in_process("pretooluse", payload)
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny"
    assert specific["permissionDecisionReason"] == "独立した所有境界"
    context = specific["additionalContext"]
    assert len(context.encode("utf-8")) <= 2000
    match = re.search(r"保存先: (.+)\n", context)
    assert match is not None
    saved = pathlib.Path(match[1])
    command = (
        "Get-Content -LiteralPath '" + str(saved).replace("'", "''") + "' -Encoding utf8"
        if os.name == "nt"
        else "cat -- " + shlex.quote(str(saved))
    )
    assert f"`{command}`を実行" in context
    assert saved.is_absolute() and saved.parent == managed_temp.resolve_session_temp(identifier)
    full = saved.read_text(encoding="utf-8")
    assert full.startswith(original)
    assert ("合流後の障害" in full) is failure
    assert "permissionDecisionReason:\n独立した所有境界" in full
    assert not session_state.read_state(identifier).get(rules_context.OPERATION_SKILL_READY_KEY)
    if not failure:
        repeated = _run_hook_in_process("pretooluse", payload)
        specific = json.loads(repeated.stdout)["hookSpecificOutput"]
        assert specific["permissionDecision"] == "deny"
        assert not specific.get("additionalContext")
        assert not session_state.read_state(identifier).get(rules_context.OPERATION_SKILL_READY_KEY)


@pytest.mark.parametrize(
    "codex,subcommand,large", [(True, "pretooluse", False), (False, "pretooluse", True), (True, "posttooluse", True)]
)
def test_context_that_needs_no_spill_preserves_delivery(monkeypatch, codex, subcommand, large):
    module = importlib.import_module(f"agent_toolkit._hooks.{subcommand}")
    identifier = "unchanged-context"
    original = "資料本文" * (1000 if large else 1)

    def handler(_payload, **_kwargs):
        notice.stage_delivery(original, identifier, rules_context.OPERATION_SKILL_READY_KEY, "main", "received")
        notice.emit_response({"hookSpecificOutput": {"additionalContext": original}})
        return 0

    monkeypatch.setattr(module, "main", handler)
    payload = {"session_id": identifier, **({"turn_id": "turn"} if codex else {})}
    result = _run_hook_in_process(subcommand, payload)
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"] == original
    assert session_state.read_state(identifier)[rules_context.OPERATION_SKILL_READY_KEY]["main"] == ["received"]


def test_codex_spill_failure_preserves_denial_and_unreceived_state(monkeypatch):
    module = importlib.import_module("agent_toolkit._hooks.pretooluse")
    original = "長い通知" * 1000

    def unavailable(_session, **_kwargs):
        raise OSError("書込不可")

    def handler(_payload, **_kwargs):
        notice.stage_delivery(original, "save-failure", rules_context.OPERATION_SKILL_READY_KEY, "main", "unreceived")
        notice.emit_response(
            {
                "hookSpecificOutput": {
                    "additionalContext": original,
                    "permissionDecision": "deny",
                    "permissionDecisionReason": "保護を維持",
                }
            }
        )
        return 0

    monkeypatch.setattr(module, "main", handler)
    monkeypatch.setattr(managed_temp, "resolve_session_temp", unavailable)
    result = _run_hook_in_process("pretooluse", {"session_id": "save-failure", "turn_id": "turn"})
    assert result.returncode == 0 and not result.stderr
    specific = json.loads(result.stdout)["hookSpecificOutput"]
    assert specific["permissionDecision"] == "deny" and specific["permissionDecisionReason"] == "保護を維持"
    assert "保存に失敗" in specific["additionalContext"]
    assert len(specific["additionalContext"].encode("utf-8")) <= 2000
    assert not session_state.read_state("save-failure").get(rules_context.OPERATION_SKILL_READY_KEY)
