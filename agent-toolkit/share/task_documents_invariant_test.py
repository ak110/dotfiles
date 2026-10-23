"""配布する役割定義の設定と返却形式の静的整合性を確かめる。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from agent_toolkit._common import task_documents
from agent_toolkit._hooks import rules_context
from agent_toolkit._testing.task_document_contract import validate_frontmatter


@pytest.mark.parametrize("path", sorted(rules_context.SHARE_DIR.glob("*.subagent.md")), ids=lambda path: path.name)
def test_distributed_frontmatter_is_valid(path: Path) -> None:
    """新しい定義もファイル追加だけで静的整合性の試験へ含める。"""
    result = validate_frontmatter(path, path.read_text(encoding="utf-8"))
    assert isinstance(result, task_documents.TaskDocumentDeclaration), result
    assert result == task_documents.read_declaration(path)


def test_share_task_documents_have_no_bare_return_line_examples() -> None:
    """`share/*.md`の返却形式の書式例が、フェンス外の裸のラベル行として置かれていないことを確かめる。

    条件付き出力の書式例をフェンスの外へ置くと、常時出力する行と誤読される。
    母集団は`rules_context.SHARE_DIR`直下の`*.md`全体とし、フェンスの内外を判別したうえで走査する。
    """
    bare_label_line = re.compile(r"^[^\s#\-*>|`][^\n:`]*: \S.*$")
    offending: list[str] = []
    for path in sorted(rules_context.SHARE_DIR.glob("*.md")):
        in_fence = False
        in_frontmatter = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if lineno == 1 and line == "---":
                in_frontmatter = True
                continue
            if in_frontmatter:
                if line == "---":
                    in_frontmatter = False
                continue
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            # HTMLコメントは機械宣言であり、返却形式の裸のラベル行には含めない。
            if line.lstrip().startswith("<!--") and line.rstrip().endswith("-->"):
                continue
            if bare_label_line.match(line):
                offending.append(f"{path.name}:{lineno}: {line}")
    assert not offending
