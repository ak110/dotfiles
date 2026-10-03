"""返却形式の書式例がコードブロック内に置かれていることを確かめる。"""

from __future__ import annotations

import re

import pytest

from agent_toolkit._hooks import rules_context

pytestmark = pytest.mark.repo_invariant


def test_share_task_documents_have_no_bare_return_line_examples() -> None:
    """`share/*.md`の返却形式の書式例が、フェンス外の裸のラベル行として置かれていないことを確かめる。

    条件付き出力の書式例をフェンスの外へ置くと、常時出力する行と誤読される。
    母集団は`rules_context.SHARE_DIR`直下の`*.md`全体とし、フェンスの内外を判別したうえで走査する。
    """
    bare_label_line = re.compile(r"^[^\s#\-*>|`][^\n:`]*: \S.*$")
    offending: list[str] = []
    for path in sorted(rules_context.SHARE_DIR.glob("*.md")):
        in_fence = False
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("```"):
                in_fence = not in_fence
                continue
            if in_fence:
                continue
            if bare_label_line.match(line):
                offending.append(f"{path.name}:{lineno}: {line}")
    assert not offending
