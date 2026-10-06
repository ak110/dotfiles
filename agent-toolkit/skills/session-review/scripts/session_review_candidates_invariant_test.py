"""候補抽出と`<役割名>.subagent.md`の横断契約を検証する。"""

import pathlib
import re

import session_review_evidence as evidence


def test_delegate_completion_values_are_defined_by_task_documents() -> None:
    """除外に使う返却値を`<役割名>.subagent.md`が定義している。"""
    share = pathlib.Path(evidence.__file__).resolve().parents[3] / "share"
    documents = "\n".join(path.read_text(encoding="utf-8") for path in sorted(share.glob("*.md")))
    lines = set(documents.splitlines())

    missing = [
        value
        for value in evidence._DELEGATE_COMPLETION_VALUES  # pylint: disable=protected-access
        if value not in lines and f"`{value}`" not in documents
    ]

    assert not missing


def test_subagent_outputs_have_recognized_first_lines() -> None:
    """自由記述を返す2担当を除き、全担当の先頭行を候補抽出が認識する。"""
    share = pathlib.Path(evidence.__file__).resolve().parents[3] / "share"
    exceptions = {
        "copilot-review-audit.subagent.md": "監査結果の本文自体が成果物",
        "pick-wi-explain.subagent.md": "選定理由の説明本文自体が成果物",
    }
    documents = set(share.glob("*.subagent.md"))
    assert {path.name for path in documents if path.name in exceptions} == set(exceptions)
    for path in sorted(documents):
        if path.name in exceptions:
            continue
        body = path.read_text(encoding="utf-8")
        output = body.split("\n## 出力\n", 1)[1].split("\n## ", 1)[0]
        match = re.search(r"```text\n([^\n]+)", output)
        if match is None:
            # `## 出力`が続行不能の形式を委譲時の厳守事項へ委ね、返却値を工程の節で定める担当は、
            # 入力の宣言を除く本文中の最初の返却値で判定する。
            match = re.search(r"```text\n((?!必須入力名:)[^\n]+)", body)
        assert match is not None, path
        first = match.group(1)
        assert (
            first.startswith(evidence._RETURN_STATUS_PREFIXES)  # pylint: disable=protected-access
            or first in evidence._DELEGATE_COMPLETION_VALUES  # pylint: disable=protected-access
            or first.startswith("判定:")
        ), (path, first)
