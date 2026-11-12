"""`improvement_status_advisor`が、原因の未調査を理由にした`不要`の報告行だけへ非遮断の通知を返すことを確かめる。"""

import json
import os
import pathlib
import re
import shlex
import types

import pytest

from agent_toolkit._common import mcp_environment, read_triggers, session_state
from agent_toolkit._common.plugin_roots import resolve_plugin_root
from agent_toolkit._hooks import document_reads, improvement_status_advisor, plugin_resources, rules_context, stop
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.fixture
def _guidance_documents() -> list[pathlib.Path]:
    root = plugin_resources.plugin_root()
    basics = resolve_plugin_root("agent-basics", root)
    return [
        basics / "skills/bugfix/references/root-cause-prevention.md",
        root / "skills/delegation/references/waiting-and-monitoring.md",
        root / "skills/process-wi/references/immediate-fix.md",
    ]


@pytest.fixture(autouse=True)
def _guidance_already_read(_guidance_documents: list[pathlib.Path], monkeypatch: pytest.MonkeyPatch) -> None:
    """元の理由判定の試験は、判定資料を取得済みの状態から行う。"""
    monkeypatch.setenv(mcp_environment.SESSION_START_INPUT_ENV, "{}")
    recorded = {
        document_reads.receipt_key(item): ["main"]
        for item in document_reads.receipt_candidates([read_triggers.ReadTarget(path) for path in _guidance_documents])
    }
    session_state.update_state(
        "improvement-status",
        lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: recorded, "session_title_kind": "process-wi"},
    )


def _payload(text: str, **extra: object) -> str:
    return json.dumps({"session_id": "improvement-status", "last_assistant_message": text, **extra}, ensure_ascii=False)


@pytest.mark.parametrize(
    "text",
    [
        "気付いた改善点: 再現しない失敗（AWI化ステータス: 不要 原因が未調査で、"
        "このセッションでは再現条件を特定できないため。必要なら調査する）",
        "- 気付いた改善点: A（AWI化ステータス: 不要 見送り 原因を調べていないため）",
    ],
)
def test_uninvestigated_reason_is_notified(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """観測した報告行と区分名付きの未調査理由へ、最小調査か引き継ぎを促す通知を返す。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    decision, body = improvement_status_advisor.evaluate(_payload(text))
    assert decision == "notify"
    assert 'kind="warn"' in body
    assert "最小調査" in body
    assert "引き継げ" in body


@pytest.mark.parametrize(
    "text",
    [
        "気付いた改善点: A（AWI化ステータス: 不要 処置済み abc1234。原因は未確定だが同じ入力で再現しない）",
        "気付いた改善点: 終了証拠の再現条件（AWI化ステータス: 不要 被覆 原因は未確定だが、"
        "既存AWI 20261106-174207-001.mdへ観測と調査を引き継ぎ済み）",
        "気付いた改善点: A（AWI化ステータス: 不要 非欠陥 未調査を理由に見送らず、調査で仕様どおりと確認した）",
        "気付いた改善点: A（AWI化ステータス: 予定 認可待ち 原因が未調査）",
        "気付いた改善点: A（AWI化ステータス: 済 20261106-000000-001.md）",
        "```text\n気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）\n```",
        "> 気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）",
        "AWI化ステータス: 不要 原因が未調査（報告行の外の言及）",
    ],
)
def test_other_reports_examples_and_quotes_are_not_notified(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """処置を定めた理由、未調査を理由にしない説明、他の値、例と引用、報告行の外には通知しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert improvement_status_advisor.evaluate(_payload(text)) == ("", "")


def test_subagent_and_delegated_session_are_not_notified(monkeypatch: pytest.MonkeyPatch) -> None:
    """委譲先は改善点を委譲元へ返すため、委譲先では判定しない。"""
    text = "気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）"
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    assert improvement_status_advisor.evaluate(_payload(text, agent_id="subagent-1")) == ("", "")
    monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    assert improvement_status_advisor.evaluate(_payload(text)) == ("", "")


def test_stop_aggregates_notice_without_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stopの集約は通知を文脈へ渡し、ターンの終了を遮断しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("improvement_status_advisor",))
    result = stop.evaluate(_payload("気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）"))
    assert "decision" not in result
    output = result["hookSpecificOutput"]
    assert isinstance(output, dict)
    context = output["additionalContext"]
    assert "原因を調べていない" in context


def _assert_guidance_commands(body: str, paths: list[pathlib.Path], *, windows: bool) -> None:
    if windows:
        decoded = re.findall(r"(?m)^Get-Content -LiteralPath '((?:''|[^'])*)' -Encoding utf8$", body)
        assert [value.replace("''", "'") for value in decoded] == list(map(str, paths))
    else:
        commands = [shlex.split(line) for line in body.splitlines() if line.startswith("cat -- ")]
        assert commands == [["cat", "--", str(path)] for path in paths]
    assert all(path.is_file() for path in paths)


@pytest.mark.parametrize("windows", [False, True])
@pytest.mark.parametrize("kind", ["process-wi", "single-lane", "add-awi"])
def test_missing_guidance_is_notified_before_report(
    kind: str, monkeypatch: pytest.MonkeyPatch, _guidance_documents: list[pathlib.Path], windows: bool
) -> None:
    """未取得の通常報告は判定資料を案内し、process-wiだけ対処時機の基準も加える。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(
        improvement_status_advisor, "os", types.SimpleNamespace(name="nt" if windows else "posix", environ=os.environ)
    )
    session_state.update_state(
        "improvement-status", lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: {}, "session_title_kind": kind}
    )
    decision, body = improvement_status_advisor.evaluate(_payload("気付いた改善点: A（AWI化ステータス: 不要 非欠陥）"))
    assert decision == "notify"
    paths = _guidance_documents if kind != "add-awi" else _guidance_documents[:2]
    _assert_guidance_commands(body, paths, windows=windows)
    assert "root-cause-prevention.md" in body
    assert "waiting-and-monitoring.md" in body
    assert ("immediate-fix.md" in body) == (kind != "add-awi")
    assert "見直し" in body


def test_compaction_requires_guidance_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の圧縮時リセット後に、取得済みだった資料を再び案内する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    text = "気付いた改善点: A（AWI化ステータス: 不要 非欠陥）"
    assert improvement_status_advisor.evaluate(_payload(text)) == ("", "")
    rules_context.reset_main_operation_skill_ready("improvement-status")
    decision, body = improvement_status_advisor.evaluate(_payload(text))
    assert decision == "notify"
    assert "root-cause-prevention.md" in body


# test-copy-contract: 不正な報告区分の入力には書式修正だけを案内し、追加資料の取得へ誤誘導しない契約。
@pytest.mark.parametrize("status", ["不要。調査で正常と確認", "予定 入力が届いたら再開"])
def test_format_correction_uses_delivered_template_without_extra_reads(status: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """区分名の取り違えは常時配送された書式で直せ、追加資料の全文取得へ誘導しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    session_state.update_state("improvement-status", lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: {}})
    result = _run_hook_in_process("improvement_status_advisor", _payload(f"気付いた改善点: A（AWI化ステータス: {status}）"))
    assert result.returncode == 0
    body = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "テンプレート直下" in body
    assert "cat -- " not in body
    assert "Get-Content" not in body


@pytest.mark.parametrize("windows", [False, True])
def test_guidance_hook_entry_returns_nonblocking_context(
    monkeypatch: pytest.MonkeyPatch, _guidance_documents: list[pathlib.Path], windows: bool
) -> None:
    """送達前照会の公開hookは未取得案内を通知欄へ渡し、拒否へ変換しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(
        improvement_status_advisor, "os", types.SimpleNamespace(name="nt" if windows else "posix", environ=os.environ)
    )
    session_state.update_state("improvement-status", lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: {}})
    result = _run_hook_in_process("improvement_status_advisor", _payload("気付いた改善点: A（AWI化ステータス: 即時対応）"))
    assert result.returncode == 0
    assert not result.stderr
    output = json.loads(result.stdout)
    assert "decision" not in output
    _assert_guidance_commands(output["hookSpecificOutput"]["additionalContext"], _guidance_documents, windows=windows)
