"""agent-toolkitスキルへの参照が追跡ファイルから実体へ解決することを検査する。

`git ls-files`を入力にすることで、通常の検索が省く隠しディレクトリも対象に含める。
このファイル自身も走査対象となるため、欠損参照の検体は接頭辞から組み立てる。
"""

from __future__ import annotations

import pathlib
import re
import subprocess

_PLUGIN_PREFIX = "agent-" + "toolkit"
_REFERENCE_BOUNDARY = r"(?<![A-Za-z0-9_:-])"
_SKILL_INVOCATION_PATTERN = re.compile(rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}:([A-Za-z0-9][A-Za-z0-9_-]*)")
_SKILL_PATH_PATTERN = re.compile(
    rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}/skills/[A-Za-z0-9][A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+)*/?"
)
_SOURCE_SUFFIXES = frozenset({".json", ".md", ".py"})
_EXCLUDED_SOURCE = pathlib.Path("docs/development/incidents.md")


def _tracked_source_paths(root: pathlib.Path) -> list[pathlib.Path]:
    """Git追跡ファイルのうち検査対象の拡張子を持つ相対パスを返す。"""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return sorted(
        pathlib.Path(raw.decode("utf-8"))
        for raw in result.stdout.split(b"\0")
        if raw and pathlib.Path(raw.decode("utf-8")).suffix in _SOURCE_SUFFIXES
    )


def _collect_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    """指定した追跡ファイルからスキルの起動名と相対パス参照を収集する。"""
    references: list[tuple[str, pathlib.Path]] = []
    for relative in sources:
        if relative == _EXCLUDED_SOURCE:
            # 事故記録は確定当時の参照位置を保持するため、過去の名称を検査対象にしない。
            continue
        content = (root / relative).read_text(encoding="utf-8")
        references.extend((match.group(), relative) for match in _SKILL_INVOCATION_PATTERN.finditer(content))
        references.extend((match.group().rstrip("/"), relative) for match in _SKILL_PATH_PATTERN.finditer(content))
    return references


def _reference_exists(root: pathlib.Path, reference: str) -> bool:
    """起動名又はリポジトリ相対パスが実体へ解決する場合に真を返す。"""
    invocation_prefix = f"{_PLUGIN_PREFIX}:"
    if reference.startswith(invocation_prefix):
        skill_name = reference.removeprefix(invocation_prefix)
        return (root / _PLUGIN_PREFIX / "skills" / skill_name / "SKILL.md").is_file()
    return (root / reference).exists()


def _unresolved_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    """実体へ解決できない参照を返す。"""
    return [
        (reference, source)
        for reference, source in _collect_references(root, sources)
        if not _reference_exists(root, reference)
    ]


def _format_unresolved(entries: list[tuple[str, pathlib.Path]]) -> str:
    """未解決参照を参照元と対にして整形する。"""
    return "\n".join(f"{reference} <- {source}" for reference, source in entries)


def test_agent_toolkit_references_resolve() -> None:
    """追跡ファイルのagent-toolkitスキル参照が全て実体へ解決する。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    sources = _tracked_source_paths(root)
    assert any(path.parts[0] == ".claude" for path in sources)
    unresolved = _unresolved_references(root, sources)
    assert not unresolved, _format_unresolved(unresolved)


def test_existing_references_resolve(tmp_path: pathlib.Path) -> None:
    """実在する起動名と相対パスを受理する。"""
    skill = tmp_path / _PLUGIN_PREFIX / "skills" / "present"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: present\n---\n", encoding="utf-8")
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text(
        f"{_PLUGIN_PREFIX}:present\n{_PLUGIN_PREFIX}/skills/present/SKILL.md\n",
        encoding="utf-8",
    )

    assert _unresolved_references(tmp_path, [source]) == []


def test_unresolved_reference_is_reported(tmp_path: pathlib.Path) -> None:
    """欠損参照を参照文字列と参照元の対で報告する。"""
    source = pathlib.Path("source.md")
    missing = f"{_PLUGIN_PREFIX}:missing"
    (tmp_path / source).write_text(f"{missing}\n", encoding="utf-8")

    unresolved = _unresolved_references(tmp_path, [source])
    assert unresolved == [(missing, source)]
    formatted = _format_unresolved(unresolved)
    assert missing in formatted
    assert str(source) in formatted
