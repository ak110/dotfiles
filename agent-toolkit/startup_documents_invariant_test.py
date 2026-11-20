"""役割の起動直後に読ませる資料が、起動の本文の宣言だけに集まっていることを確かめる。

無条件に読ませる資料を読込表へ足すと、起動の本文の大きさに表れずに個別の読込として隠れる。
起動直後に読ませる資料は、役割文書のfrontmatterの`startup_documents`に置く。
"""

import pathlib
import re

from agent_toolkit._common import startup_documents, task_documents
from agent_toolkit._testing.normative_contract import read_table_rows

_ROOT = pathlib.Path(__file__).resolve().parent
_UNCONDITIONAL = re.compile(r"(?:起動時|着手時|起動直後|開始時|受領時)")
"""起動するたびに必ず成立する、読込表の「時点または条件」の値。"""


def test_role_documents_have_no_unconditional_read_rows() -> None:
    """役割文書の読込表に、起動するたびに成立する条件の行が無い。"""
    found = [
        f"{document.name}: {condition}"
        for document in task_documents.role_documents(_ROOT)
        for condition, _documents in read_table_rows(document.read_text(encoding="utf-8"))
        if _UNCONDITIONAL.fullmatch(condition)
    ]
    assert not found, "起動直後に読ませる資料はstartup_documentsへ置く:\n" + "\n".join(found)


def test_startup_documents_exist() -> None:
    """全ての役割の起動の本文へ入る資料が実在する。"""
    missing = []
    for document, references in startup_documents.role_startup_reading(_ROOT):
        for reference in references:
            try:
                startup_documents.resolve(reference, _ROOT)
            except ValueError as error:
                missing.append(f"{document.name}: {error}")
    assert not missing, "\n".join(missing)


def test_unconditional_row_detection_reports_startup_rows() -> None:
    """読込表の解析が、無条件の行を返し、条件付きの行と読込表でない表の行を返さない。"""
    text = (
        "| 時点または条件 | 全文読む資料 |\n| --- | --- |\n"
        "| 着手時 | `a.md` |\n| 判定する前 | `b.md` |\n\n| 条件 | 行動 |\n| --- | --- |\n| 起動時 | 手順 |\n"
    )
    assert [condition for condition, _ in read_table_rows(text) if _UNCONDITIONAL.fullmatch(condition)] == ["着手時"]
