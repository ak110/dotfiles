"""process-wiの即時公開例外が通常公開を弱めないことを検証する。"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DOCUMENT_PATHS = {
    "project": _ROOT / "AGENTS.md",
    "process": _ROOT / "agent-toolkit/skills/process-wi/SKILL.md",
    "finish": _ROOT / "agent-toolkit/skills/process-wi/references/finish-session.md",
    "parent": _ROOT / "agent-toolkit/share/session-termination.parent.md",
    "subagent": _ROOT / "agent-toolkit/share/session-termination.subagent.md",
}
_REQUIRED_CLAUSES = (
    (
        "project",
        "ユーザーが局所変更の即時公開と、次回の`agent-toolkit:process-wi`での正式対応の両方を同じ指示で明示した場合だけ",
    ),
    (
        "process",
        "局所変更を現在のセッションで即時に公開することと、次回の`agent-toolkit:process-wi`で正式に対応することの両方を同じ指示で明示した場合だけ",
    ),
    (
        "process",
        "即時公開する変更に対応する近接検査を成功させ、同じ要求を正式な計画、実装、実行レビュー、全体検査及びCI成功確認へ送るAWIを登録する。",
    ),
    (
        "process",
        "この条件を満たさない公開工程は`検証・CI方針: 通常`とし、全体検査とCI成功確認を維持する。",
    ),
    ("finish", "通常の公開では`検証・CI方針: 通常`を渡す。"),
    (
        "finish",
        "`検証・CI方針: 即時対応`と、成功した近接検査のコマンド・終了コード、正式対応AWIのファイル名を渡す。",
    ),
    (
        "finish",
        "即時対応では全体検査とCI成功の待機を省略するが、push後のCI起動とrun URLは確認する。",
    ),
    (
        "finish",
        "省略した全体検査、未確定のCI、run URL及び正式対応AWIをcompletion-reportへ含める。",
    ),
    (
        "parent",
        "- `検証・CI方針`: 通常は`通常`。`agent-toolkit:process-wi`の「局所変更の即時公開」が成立する場合だけ`即時対応`",
    ),
    (
        "parent",
        "- `近接検証結果`: `即時対応`では成功したコマンドと終了コード。`通常`では`なし`",
    ),
    (
        "parent",
        "- `正式対応AWI`: `即時対応`では登録済みAWIのファイル名。`通常`では`なし`",
    ),
    (
        "parent",
        "`検証・CI方針`が`通常`の場合、`overall_verification`が`CI判定`又は`ローカル成功`である。",
    ),
    (
        "parent",
        "`検証・CI方針`が`通常`の場合、`ci_result`が`成功`であり、対象リポジトリのCI照会手段が`ci_verified_head`について同じ結論を返す",
    ),
    (
        "parent",
        "`検証・CI方針`が`即時対応`の場合、`overall_verification`が起動時に渡した近接検証の成功を挙げ、"
        "`ci_result`が`待機省略`とCIのrun URLを挙げ、"
        "`terminal_steps`が省略した全体検査と正式対応AWIを挙げる",
    ),
    (
        "subagent",
        "必須入力名: bump種別,検証・CI方針,近接検証結果,正式対応AWI,引き継ぎ記録先",
    ),
    (
        "subagent",
        "受領した近接検証結果が終了コード0を示し、正式対応AWIが`なし`でないことを確認する。",
    ),
    (
        "subagent",
        "`検証・CI方針`が`通常`の場合は、pushしたcommitの7文字以上の一意な短縮OIDをCI照会の直前に対象リポジトリで解決してCIを確認し、差分が無い場合は現在HEADのCI結論を1回確定する。",
    ),
    (
        "subagent",
        "`即時対応`の場合は、同じcommitに対応するCIの起動とrun URLを確認した時点で待機を終え、成功又は失敗を判定しない。",
    ),
    (
        "subagent",
        "overall_verification: <通常はCI判定又はローカル成功。即時対応は近接検証のコマンドと終了コードを併記する>",
    ),
    (
        "subagent",
        "ci_result: <通常は成功又は失敗。即時対応は「待機省略」とrun URL>",
    ),
    (
        "subagent",
        "terminal_steps: <実行した工程と対象。即時対応は省略した全体検査と正式対応AWIも併記する。無い場合は「なし」>",
    ),
)


def _read_documents() -> dict[str, str]:
    return {name: path.read_text(encoding="utf-8") for name, path in _DOCUMENT_PATHS.items()}


def _assert_required_clauses(documents: dict[str, str]) -> None:
    for document, clause in _REQUIRED_CLAUSES:
        assert clause in documents[document], f"{document}から必須契約が欠落: {clause}"


def test_immediate_and_normal_publication_contracts() -> None:
    """即時対応と通常公開の全契約を検査する。"""
    _assert_required_clauses(_read_documents())


@pytest.mark.parametrize(("document", "clause"), _REQUIRED_CLAUSES)
def test_each_required_clause_is_enforced(document: str, clause: str) -> None:
    """各契約句の欠落を個別に検出する。"""
    documents = _read_documents()
    assert documents[document].count(clause) == 1
    documents[document] = documents[document].replace(clause, "", 1)

    with pytest.raises(AssertionError, match="必須契約が欠落"):
        _assert_required_clauses(documents)
