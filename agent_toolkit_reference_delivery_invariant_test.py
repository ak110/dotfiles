"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import ast
import pathlib
import re

from agent_toolkit._common import read_triggers
from agent_toolkit._testing.normative_contract import read_contract


def test_session_state_design_reference_reaches_local_development_rule() -> None:
    """配布実装の開発用docstringから、実在するローカル規約の設計節に到達する。"""
    root = pathlib.Path(__file__).resolve().parent
    source = root / "agent-toolkit/agent_toolkit/_common/session_state.py"
    docstring = ast.get_docstring(ast.parse(source.read_text(encoding="utf-8")))
    assert docstring is not None
    reference = re.search(r"`([^`]+internals-session-state-common\.md)`", docstring)
    assert reference is not None
    target = root / reference[1]
    assert target == root / "docs/development/internals-session-state-common.md"
    assert "## 状態ファイルの設計" in target.read_text(encoding="utf-8")


def test_all_repos_recipient_condition_reaches_candidate_handoff() -> None:
    """all-repos統括から候補を受け取るリポジトリ担当を、入口と役割文書の両方から受渡しの節へ接続する。"""
    root = pathlib.Path(__file__).resolve().parent
    plugin = root / "agent-toolkit"
    atk_mode = plugin / "skills/atk-mode"
    handoff = atk_mode / "playbooks/process-wi-all-repos.md"
    with read_triggers.resolution_scope():
        for source, condition in (
            (atk_mode / "playbooks/process-wi.md", "all-reposの統括からリポジトリ担当として候補を受け取った"),
            (atk_mode / "roles/repository-process.subagent.md", "「取得済み候補の受領」"),
        ):
            resolved = [
                found
                for _number, line, visible in read_triggers.document_lines(source.read_text(encoding="utf-8"))
                if visible and condition in line
                for found in read_triggers.material_targets(line, source, plugin, references_only=True)
            ]
            assert any(
                found.path == handoff and "取得済み候補の受領" in found.heading and found.body() for found in resolved
            ), source


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
