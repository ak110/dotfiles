"""`improvement_status_advisor`が、報告の形に応じた非遮断の案内を送達の前後で返し分けることを確かめる。"""

import json
import os
import pathlib
import re
import types

import pytest

from agent_toolkit._common import mcp_environment, read_triggers, runtime_inserted, session_state
from agent_toolkit._common.plugin_roots import resolve_plugin_root
from agent_toolkit._hooks import document_reads, improvement_status_advisor, plugin_resources, rules_context, stop
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process


@pytest.fixture
def _guidance_documents() -> list[pathlib.Path]:
    root = plugin_resources.plugin_root()
    basics = resolve_plugin_root("agent-basics", root)
    return [
        basics / "skills/bugfix/references/prevention.md",
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
    assert "main-improvements.md" in body
    assert "最小調査" in body


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


def _assert_guidance_commands(body: str, paths: list[pathlib.Path]) -> None:
    assert all(path.name in body for path in paths)
    assert "cat --" not in body and "Get-Content" not in body
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
    # 取得案内は送達前の照会（`main`）が返す。送達後のStopの`evaluate`は返さない。
    assert improvement_status_advisor.evaluate(_payload("気付いた改善点: A（AWI化ステータス: 不要 非欠陥）")) == ("", "")
    body = improvement_status_advisor.read_guidance_notice(
        json.loads(_payload("気付いた改善点: A（AWI化ステータス: 不要 非欠陥）"))
    )
    assert body
    paths = _guidance_documents if kind != "add-awi" else _guidance_documents[:2]
    _assert_guidance_commands(body, paths)
    assert "prevention.md" in body
    assert "waiting-and-monitoring.md" in body
    assert ("immediate-fix.md" in body) == (kind != "add-awi")
    assert "見直せ" in body


def test_compaction_requires_guidance_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """既存の圧縮時リセット後に、取得済みだった資料を再び案内する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    text = "気付いた改善点: A（AWI化ステータス: 不要 非欠陥）"
    payload = json.loads(_payload(text))
    assert improvement_status_advisor.read_guidance_notice(payload) == ""
    rules_context.reset_main_operation_skill_ready("improvement-status")
    body = improvement_status_advisor.read_guidance_notice(payload)
    assert improvement_status_advisor.evaluate(_payload(text)) == ("", "")
    assert "prevention.md" in body


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
    _assert_guidance_commands(output["hookSpecificOutput"]["additionalContext"], _guidance_documents)


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
    before = improvement_status_advisor.read_guidance_notice(payload)
    after = improvement_status_advisor.evaluate(_payload(text))[1]
    if missing is None:
        assert not before and not after
    else:
        assert missing in before and 'kind="warn"' in before
        assert "permissionDecision" not in before
        assert missing in after


_BUGFIX = "skills/bugfix/SKILL.md"
_SPREAD = "skills/bugfix/references/spread.md"
_CAUSES = "skills/bugfix/references/causes.md"
_ABSENCE = (
    "統合の後の点検で古い版を使う誤りがあった。\n"
    "原因: 統合で受け取った版を後の判断や点検へ引き継ぐ仕組みが無い。\n"
    "処置: 引き継ぐ手順を足す。"
)
_NO_CHANGE = "設定の値は勝手に変えていない。現行の値と作業記録の一部を確かめた。"
# 送達後の期待は`hooks/send_to_user.test.tsx`と同じ固定の報告で確かめる。
# 各行は（報告, このセッションの記録, 送達前に案内する資料, 送達後に案内する記録の行頭語）。
# 記録の`read`は全文取得、`skill`は`Skill`起動の記録だけを持つ。
_REPORTS = {
    "a-自分で見つけた逸脱をbugfixの適用なしで報告": (
        "作業中に自分の手順漏れを見つけたため、漏れた手順をその場で実施した。",
        {},
        (_BUGFIX,),
        ("処置",),
    ),
    "b-修正済みだが残る影響を確かめていない": ("引数の誤りを直し、再実行で成功した。", {}, (_BUGFIX,), ("処置",)),
    "c-bugfix起動済みでも処置の行が無い": (
        "- 期待: 対象の全件を処理する。\n"
        "- 原因: 範囲の指定の誤りで末尾の1件が漏れていた。\n"
        "- 確認: 再実行で全件の処理を確かめた。",
        {_BUGFIX: "skill"},
        (),
        ("処置",),
    ),
    "d-不在の主張でspread未取得": (_ABSENCE, {_BUGFIX: "read"}, (_SPREAD,), ()),
    "e-変えていないの回答でcauses未取得": (_NO_CHANGE, {_BUGFIX: "read"}, (_CAUSES,), ()),
    "f-ユーザーの指摘で再発防止の行が無い": (
        "ユーザーの指摘で手順漏れが分かった。\n処置: 漏れた手順を実施し、残る影響が無いことを確かめた。",
        {_BUGFIX: "skill"},
        (),
        ("再発防止",),
    ),
    "対照-同じ資料を取得済み": (_ABSENCE + "\n" + _NO_CHANGE, {_BUGFIX: "read", _SPREAD: "read", _CAUSES: "read"}, (), ()),
    "対照-単なる引用": ("> 作業中に自分の手順漏れを見つけた。\n```text\n誤りを直した。\n```", {}, (), ()),
    "対照-正常な進捗": ("実装を終え、変更範囲の試験が通った。", {}, (), ()),
    "対照-逸脱が無いことを述べる進捗": ("変更範囲の試験を実行し、失敗0件で誤りは無いことを確かめた。", {}, (), ()),
}


def _basics() -> pathlib.Path:
    return resolve_plugin_root("agent-basics", plugin_resources.plugin_root())


def _set_records(kinds: dict[str, str]) -> None:
    records: dict[str, list[str]] = {}
    for relative, kind in kinds.items():
        path = _basics() / relative
        if kind == "skill":
            records[document_reads.skill_invocation_key(path, document_reads.content_fingerprint(path))] = ["main"]
        else:
            for item in document_reads.receipt_candidates([read_triggers.ReadTarget(path)]):
                records[document_reads.receipt_key(item)] = ["main"]
    session_state.update_state(
        "improvement-status",
        lambda state: {**state, rules_context.OPERATION_SKILL_READY_KEY: records, "session_title_kind": "add-awi"},
    )


def _guided(body: str, relative: str) -> bool:
    return f"{document_reads.reference_name(_basics() / relative)}: " in body


@pytest.mark.parametrize("case", list(_REPORTS))
def test_report_shapes_get_guidance_before_and_record_lines_after_delivery(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """送達前は報告の形に対応する未取得の資料だけ、送達後は欠けた記録の行だけを案内する。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    text, records, documents, heads = _REPORTS[case]
    _set_records(records)
    before = improvement_status_advisor.read_guidance_notice(json.loads(_payload(text)))
    for relative in (_BUGFIX, _SPREAD, _CAUSES):
        assert _guided(before, relative) is (relative in documents), relative
    if _CAUSES in documents:
        assert "変更履歴" in before and "要求の原文" in before
    decision, after = improvement_status_advisor.evaluate(_payload(text))
    assert decision == ("notify" if heads else "")
    assert "取得を確認できない" not in after
    for head in ("処置", "再発防止"):
        assert (f"`{head}:`の行が無い" in after) is (head in heads), head


def test_bugfix_record_does_not_count_as_applied_after_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """bugfixの起動・取得の記録があっても、送達後の記録の行の検査は省かない。"""
    monkeypatch.delenv("AGENT_TOOLKIT_DELEGATED_SESSION", raising=False)
    text = _REPORTS["a-自分で見つけた逸脱をbugfixの適用なしで報告"][0]
    for kind in ("read", "skill"):
        _set_records({_BUGFIX: kind})
        assert not improvement_status_advisor.read_guidance_notice(json.loads(_payload(text)))
        assert "`処置:`の行が無い" in improvement_status_advisor.evaluate(_payload(text))[1]


# test-copy-contract: 送達後の検査を担う2つの言語の判定語と記録の行頭語が同じ文字列であること（両ファイル冒頭の注記の契約）。
def test_report_cues_match_send_to_user() -> None:
    """送達後に使う判定語と記録の行頭語を、TypeScript側の定義と突き合わせる。"""
    sender = (plugin_resources.plugin_root() / "hooks/send_to_user.tsx").read_text(encoding="utf-8")
    negation = re.search(r'^const CUE_NEGATION = "([^"]+)";$', sender, re.MULTILINE)
    assert negation is not None and negation[1] == runtime_inserted.CUE_NEGATION
    block = re.search(r"^const REPORT_CUES = \{\n(.*?)^\} as const;$", sender, re.MULTILINE | re.DOTALL)
    assert block is not None
    cues = dict(re.findall(r'^  (\w+): "([^"]+)",$', block[1], re.MULTILINE))
    assert cues == {cue: runtime_inserted.REPORT_CUES[cue] for cue in cues}
    checks = re.findall(r'cues: \[([^\]]+)\],\n\s*head: "([^"]+)"', sender)
    assert {(frozenset(re.findall(r'"(\w+)"', listed)), head) for listed, head in checks} == {
        (required, head) for required, head, _body, _fix in improvement_status_advisor.RECORD_LINE_CHECKS
    }
    assert set().union(*(required for required, *_ in improvement_status_advisor.RECORD_LINE_CHECKS)) <= set(cues)
    entry = (_basics() / _BUGFIX).read_text(encoding="utf-8")
    assert all(
        f"- {head}:" in entry or f"`{head}:`" in entry for _required, head, *_ in improvement_status_advisor.RECORD_LINE_CHECKS
    )
