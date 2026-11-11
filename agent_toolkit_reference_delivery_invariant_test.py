"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import ast
import pathlib
import re

from agent_toolkit._common import read_triggers
from agent_toolkit._testing.normative_contract import read_contract


def test_ci_failure_read_row_delivers_quality_and_operations() -> None:
    """同じ失敗受領条件で、品質と工程の二資料が資料列と、そこから導出した配送操作の両方から取得される。"""
    root = pathlib.Path(__file__).resolve().parent
    plugin = root / "agent-toolkit"
    source = root / ".claude/skills/merge-pr/SKILL.md"
    quality = root / "agent-basics/skills/bugfix/references/ci-failure-handling.md"
    operations = plugin / "skills/commit/references/ci-failure-handling.md"
    rows = read_triggers.parse_tables(source, plugin, strict=True)
    row = next(row for row in rows if quality in row.documents)
    assert set(row.documents) == {quality, operations}
    assert set(row.effective_operations) == {
        "read:agent-basics:*/skills/bugfix/references/ci-failure-handling.md",
        "read:agent-toolkit:*/skills/commit/references/ci-failure-handling.md",
    }
    assert all(target.body() for target in row.targets)


def test_session_state_design_reference_reaches_local_development_rule() -> None:
    """配布実装の開発用docstringから、実在するローカル規約の設計節に到達する。"""
    root = pathlib.Path(__file__).resolve().parent
    source = root / "agent-toolkit/agent_toolkit/_common/session_state.py"
    docstring = ast.get_docstring(ast.parse(source.read_text(encoding="utf-8")))
    assert docstring is not None
    reference = re.search(r"`([^`]+session-state-and-flags\.md)`", docstring)
    assert reference is not None
    target = root / reference[1]
    assert target == root / ".claude/skills/agent-toolkit-edit/references/session-state-and-flags.md"
    text = read_contract(target, root / "agent-toolkit", ("session-state-and-flags-common.md", "状態ファイルの設計"))
    assert "## 状態ファイルの設計" in text


def test_all_repos_recipient_condition_reaches_candidate_handoff() -> None:
    """現行all-repos統括から取得済み候補を受け取る担当へ、受渡し契約を接続する。"""
    root = pathlib.Path(__file__).resolve().parent
    source = root / "agent-toolkit/skills/process-wi/references/lanes.md"
    row_text = next(
        line for line in source.read_text(encoding="utf-8").splitlines() if "| `all-repos-orchestration.md` |" in line
    )
    assert "`agent-toolkit:process-wi all-repos`の統括から" in row_text
    rows = read_triggers.parse_tables(source, root / "agent-toolkit", strict=True)
    expected = source.with_name("all-repos-orchestration.md")
    row = next(row for row in rows if expected in row.documents)
    assert row.effective_operations == ("read:agent-toolkit:*/skills/process-wi/references/all-repos-orchestration.md",)
    assert row.targets[0].body()


def test_quality_definition_forwarders_reach_distinct_basics_documents() -> None:
    """移設後の品質定義の転送が自己循環せず、正式提供元の実在する節へ到達する。"""
    root = pathlib.Path(__file__).resolve().parent
    plugin = root / "agent-toolkit"
    found = []
    for source in sorted((plugin / "share").glob("*.md")):
        for line in source.read_text(encoding="utf-8").splitlines():
            if not line.startswith("品質の定義については、"):
                continue
            reference = re.search(r"`([^`]+)`「([^」]+)」", line)
            assert reference is not None, (source, line)
            target = read_triggers.resolve_document(reference[1], source, plugin)
            assert target is not None and target != source
            assert target.is_relative_to(root / "agent-basics")
            assert read_triggers.ReadTarget(target, reference[2]).body()
            found.append((source.name, reference[2]))
    read_contract(plugin / "share/exec-review-criteria.md", plugin, ("exec-review-criteria-execution.md", "実行レビューの判定"))
    assert {name for name, _heading in found} == {
        "exec-review-responses.md",
        "exec-review-criteria.md",
        "exec-review-criteria-execution.md",
    }
