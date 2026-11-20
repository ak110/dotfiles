"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import ast
import pathlib
import re

from _test_helpers import _PLUGIN_PREFIX, _tracked_source_paths, resolve_plugin_reference


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
    atk_mode = root / "agent-toolkit/skills/atk-mode"
    handoff = atk_mode / "playbooks/process-wi-all-repos.md"
    reference = "`agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md`「取得済み候補の受領」"
    assert resolve_plugin_reference("agent-toolkit:atk-mode/playbooks/process-wi-all-repos.md", root) == handoff
    assert "\n## 取得済み候補の受領\n" in handoff.read_text(encoding="utf-8")
    for source, condition in (
        (atk_mode / "playbooks/process-wi.md", "all-reposの統括からリポジトリ担当として候補を受け取った"),
        (atk_mode / "roles/repository-process.subagent.md", "受け取った候補の扱い"),
    ):
        lines = [line for line in source.read_text(encoding="utf-8").splitlines() if condition in line]
        assert any(reference in line for line in lines), source


def _plugin_documents(root: pathlib.Path) -> list[pathlib.Path]:
    """配布する2つのプラグインの追跡中のMarkdownを返す。"""
    return [
        root / path
        for path in _tracked_source_paths(root, frozenset({".md"}))
        if path.as_posix().startswith((f"{_PLUGIN_PREFIX}/", "agent-basics/"))
    ]


_RELATIVE_REFERENCE = re.compile(r"`(\.\./[^`\s]+)`|\]\((\.\./[^)\s]+)\)")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")


def _escaping_relative_references(source: pathlib.Path) -> list[str]:
    """`../`で始まる相対パスのうち、文書を所有するプラグインのrootの外を指すものを返す。コードフェンスの内側は例示として除く。"""
    owner = next((parent for parent in source.parents if (parent / ".claude-plugin/plugin.json").is_file()), None)
    if owner is None:
        return []
    found = []
    fence = ""
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if marker := _FENCE.match(line):
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        for match in _RELATIVE_REFERENCE.finditer(line):
            value = (match[1] or match[2]).split("#", 1)[0]
            if not (source.parent / value).resolve().is_relative_to(owner.resolve()):
                found.append(f"{source}:{number}: {value}")
    return found


def test_plugin_relative_references_stay_inside_owner_plugin() -> None:
    """プラグイン文書の相対パスは所有プラグインのrootの中を指す。

    リポジトリではagent-toolkitとagent-basicsが隣り合うが、配布キャッシュでは版ごとの別ディレクトリに置かれるため、
    rootの外を指す相対パスは配布物で届かない。別のプラグインの資料は`agent-basics:<スキル>/<パス>`の論理名で書く。
    """
    root = pathlib.Path(__file__).resolve().parent
    documents = _plugin_documents(root)
    assert any(path.is_relative_to(root / "agent-basics") for path in documents)
    found = [item for source in documents for item in _escaping_relative_references(source)]
    assert not found, "\n".join(found)


def test_escaping_relative_reference_is_reported(tmp_path: pathlib.Path) -> None:
    """rootの外を指す相対パスだけを報告し、root内の相対パスとコードフェンス内の例示は報告しない。"""
    plugin = tmp_path / _PLUGIN_PREFIX
    (plugin / ".claude-plugin").mkdir(parents=True)
    (plugin / ".claude-plugin/plugin.json").write_text("{}", encoding="utf-8")
    source = plugin / "skills/sample/SKILL.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "`../other/SKILL.md`\n[外](../../../agent-basics/skills/x/SKILL.md)\n```text\n`../../../outside.md`\n```\n",
        encoding="utf-8",
    )
    assert _escaping_relative_references(source) == [f"{source}:2: ../../../agent-basics/skills/x/SKILL.md"]
