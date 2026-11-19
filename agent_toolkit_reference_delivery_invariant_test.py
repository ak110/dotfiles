"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import ast
import pathlib
import re

from agent_toolkit._common import read_triggers


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
