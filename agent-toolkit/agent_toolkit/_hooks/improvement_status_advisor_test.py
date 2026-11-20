"""`improvement_status_advisor`が、改善点の報告の書式と未調査の理由を送達の前後に通知することを確かめる。"""

import json
import re

import pytest

from agent_toolkit._common import mcp_environment
from agent_toolkit._common.plugin_roots import resolve_plugin_root
from agent_toolkit._hooks import improvement_status_advisor, plugin_resources, stop
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.fixture(autouse=True)
def _no_startup_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """実行環境の起動入力を試験へ混ぜない。"""
    monkeypatch.setenv(mcp_environment.SESSION_START_INPUT_ENV, "{}")


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
    assert "share/" not in body


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


@pytest.mark.parametrize(
    "text",
    [
        "作業中に自分の手順漏れを見つけたため、漏れた手順をその場で実施した。",
        "ユーザーの指摘で手順漏れが分かった。同じ誤りが再発していた。",
    ],
)
def test_stop_does_not_force_continuation_for_missing_record_lines(text: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """記録の行は以後の報告でしか補えないため、逸脱の報告で終えるStopは繰り返しても継続を求めない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    monkeypatch.setattr(stop, "CHECK_MODULE_NAMES", ("improvement_status_advisor",))
    for _ in range(3):
        assert stop.evaluate(_payload(text)) == {}


def test_stop_notice_is_not_repeated_after_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    """同じターンで一度継続した後のStopでは、改善点の通知を繰り返さない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    text = "気付いた改善点: A（AWI化ステータス: 不要 原因が未調査）"
    assert improvement_status_advisor.evaluate(_payload(text))[0] == "notify"
    assert improvement_status_advisor.evaluate(_payload(text, stop_hook_active=True)) == ("", "")


# test-copy-contract: 不正な報告区分の入力には書式修正だけを案内し、追加資料の取得へ誤誘導しない契約。
@pytest.mark.parametrize("status", ["不要。調査で正常と確認", "予定 入力が届いたら再開"])
def test_format_correction_uses_delivered_template_without_extra_reads(status: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """区分名の取り違えは通知が挙げる書式と区分で直せ、追加資料の全文取得へ誘導しない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    result = _run_hook_in_process("improvement_status_advisor", _payload(f"気付いた改善点: A（AWI化ステータス: {status}）"))
    assert result.returncode == 0
    assert not result.stderr
    output = json.loads(result.stdout)
    assert "decision" not in output
    body = output["hookSpecificOutput"]["additionalContext"]
    assert "の形に直せ。区分は" in body
    assert "cat -- " not in body
    assert "Get-Content" not in body


@pytest.mark.parametrize("shape", ["empty", "omitted", "other-report"])
@pytest.mark.parametrize("missing", [None, "作り込み側の候補", "見逃し側の候補", "比較結論"])
def test_deferral_comparison_fields_are_checked_before_delivery_and_at_stop(missing, shape, monkeypatch):
    """項目の非空だけを両入口で検査し、比較結果の採否へ踏み込まない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    fields = {"作り込み側の候補": "既存処理へ統合", "見逃し側の候補": "境界の試行", "比較結論": "継続費用が防げる損失を上回る"}
    if missing is not None:
        if shape == "empty":
            fields[missing] = ""
        else:
            del fields[missing]
    text = "気付いた改善点: A。AWI化ステータス: 不要 見送り " + "; ".join(f"{key}: {value}" for key, value in fields.items())
    if missing is not None and shape == "other-report":
        text += "\n気付いた改善点: B。AWI化ステータス: 不要 見送り " + missing + ": 別報告の候補"
    payload = json.loads(_payload(text))
    before = improvement_status_advisor.format_guidance_notice(payload)
    after = improvement_status_advisor.evaluate(_payload(text))[1]
    if missing is None:
        assert not before and not after
    else:
        assert missing in before and 'kind="warn"' in before
        assert "permissionDecision" not in before
        assert missing in after


def test_send_to_user_record_heads_exist_in_bugfix() -> None:
    """送達後に求める記録の行頭語が、bugfixの`SKILL.md`の「記録」にある。"""
    sender = (plugin_resources.plugin_root() / "hooks/send_to_user.tsx").read_text(encoding="utf-8")
    heads = re.findall(r'cues: \[[^\]]+\],\n\s*head: "([^"]+)"', sender)
    assert set(heads) == {"処置", "再発防止"}
    basics = resolve_plugin_root("agent-basics", plugin_resources.plugin_root())
    entry = (basics / "skills/bugfix/SKILL.md").read_text(encoding="utf-8")
    assert all(f"- {head}:" in entry or f"`{head}:`" in entry for head in heads)
