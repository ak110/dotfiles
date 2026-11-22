"""候補抽出と`<役割名>.subagent.md`の横断契約を検証する。"""

import pathlib
import re

import session_evidence_candidates as evidence_candidates
import session_review_evidence as evidence
from agent_toolkit._common import task_documents


def test_subagent_outputs_have_recognized_first_lines() -> None:
    plugin_root = pathlib.Path(evidence.__file__).resolve().parents[3]
    exceptions = {
        "copilot-review-audit.subagent.md": "監査結果の本文自体が成果物",
    }
    documents = set(task_documents.role_documents(plugin_root))
    assert {path.name for path in documents if path.name in exceptions} == set(exceptions)
    for path in sorted(documents):
        if path.name in exceptions:
            continue
        body = path.read_text(encoding="utf-8")
        output = body.split("\n## 出力\n", 1)[1].split("\n## ", 1)[0]
        match = re.search(r"```text\n([^\n]+)", output)
        if match is None:
            match = re.search(r"```text\n((?!必須入力名:)[^\n]+)", body)
        assert match is not None, path
        first = match.group(1)
        assert (
            first.startswith(evidence_candidates._RETURN_STATUS_PREFIXES)  # pylint: disable=protected-access
            or first in evidence_candidates._DELEGATE_COMPLETION_VALUES  # pylint: disable=protected-access
            or first.startswith("判定:")
        ), (path, first)
