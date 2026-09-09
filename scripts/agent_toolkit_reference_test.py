"""agent-toolkitスキルとPythonファイルへの参照が追跡ファイルから実体へ解決することを検査する。

`git ls-files`を入力にすることで、通常の検索が省く隠しディレクトリも対象に含める。
検査対象は`.json`・`.md`・`.py`であり、`install-claude.sh`と`install-claude.ps1`は含まない。
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
_PYTHON_PATH_PATTERN = re.compile(
    rf"{_REFERENCE_BOUNDARY}{_PLUGIN_PREFIX}/(?:scripts|agent_toolkit)/"
    r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py"
)
_SOURCE_SUFFIXES = frozenset({".json", ".md", ".py"})
_INCIDENTS = pathlib.Path("docs/development/incidents.md")
_ALLOWED_UNRESOLVED_REFERENCE_COUNTS = {
    (f"{_PLUGIN_PREFIX}:agent-standards", _INCIDENTS): 1,
    (f"{_PLUGIN_PREFIX}:feedback-standards", _INCIDENTS): 1,
    (f"{_PLUGIN_PREFIX}:process-feedbacks", _INCIDENTS): 5,
    (f"{_PLUGIN_PREFIX}:reviewee-standards", _INCIDENTS): 1,
    (f"{_PLUGIN_PREFIX}:shell-exec", _INCIDENTS): 1,
    (f"{_PLUGIN_PREFIX}/scripts/hook.py", _INCIDENTS): 1,
}


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
        content = (root / relative).read_text(encoding="utf-8")
        references.extend((match.group(), relative) for match in _SKILL_INVOCATION_PATTERN.finditer(content))
        references.extend((match.group().rstrip("/"), relative) for match in _SKILL_PATH_PATTERN.finditer(content))
        references.extend((match.group(), relative) for match in _PYTHON_PATH_PATTERN.finditer(content))
    return references


def _reference_exists(root: pathlib.Path, reference: str) -> bool:
    """起動名又はリポジトリ相対パスが実体へ解決する場合に真を返す。"""
    invocation_prefix = f"{_PLUGIN_PREFIX}:"
    if reference.startswith(invocation_prefix):
        skill_name = reference.removeprefix(invocation_prefix)
        return (root / _PLUGIN_PREFIX / "skills" / skill_name / "SKILL.md").is_file()
    return (root / reference).exists()


def _unresolved_references(root: pathlib.Path, sources: list[pathlib.Path]) -> list[tuple[str, pathlib.Path]]:
    """実体へ解決できない参照と不足した既知の事故記録参照を返す。"""
    allowed_counts = {key: count for key, count in _ALLOWED_UNRESOLVED_REFERENCE_COUNTS.items() if key[1] in sources}
    unresolved: list[tuple[str, pathlib.Path]] = []
    for reference, source in _collect_references(root, sources):
        if _reference_exists(root, reference):
            continue
        key = (reference, source)
        if allowed_counts.get(key, 0) > 0:
            allowed_counts[key] -= 1
            continue
        unresolved.append(key)
    for key, remaining_count in allowed_counts.items():
        unresolved.extend([key] * remaining_count)
    return unresolved


def _format_unresolved(entries: list[tuple[str, pathlib.Path]]) -> str:
    """未解決参照を参照元と対にして整形する。"""
    return "\n".join(f"{reference} <- {source}" for reference, source in entries)


def test_agent_toolkit_references_resolve() -> None:
    """追跡中の`.json`・`.md`・`.py`にあるスキルとPython参照が全て実体へ解決する。"""
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

    assert not _unresolved_references(tmp_path, [source])


def test_python_reference_templates_and_globs_are_ignored(tmp_path: pathlib.Path) -> None:
    """Python参照の雛形とグロブを未解決の実体参照として扱わない。"""
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text(
        "\n".join(
            [
                f"{_PLUGIN_PREFIX}/agent_toolkit/*.py",
                f"{_PLUGIN_PREFIX}/agent_toolkit/<name>.py",
                f"{_PLUGIN_PREFIX}/skills/*/scripts/",
            ]
        ),
        encoding="utf-8",
    )

    assert not _unresolved_references(tmp_path, [source])


def _known_legacy_references() -> list[str]:
    """事故記録へ保持する既知の失効参照を返す。"""
    return [
        f"{_PLUGIN_PREFIX}:agent-standards",
        f"{_PLUGIN_PREFIX}:feedback-standards",
        *[f"{_PLUGIN_PREFIX}:process-feedbacks"] * 5,
        f"{_PLUGIN_PREFIX}:reviewee-standards",
        f"{_PLUGIN_PREFIX}:shell-exec",
        f"{_PLUGIN_PREFIX}/scripts/hook.py",
    ]


def test_incident_history_requires_exact_known_legacy_references(tmp_path: pathlib.Path) -> None:
    """既知の失効参照を過不足なく保持し、各参照の不足と超過を報告する。"""
    source = pathlib.Path("docs/development/incidents.md")
    (tmp_path / source).parent.mkdir(parents=True)
    references = _known_legacy_references()
    target = tmp_path / source
    target.write_text("\n".join(references), encoding="utf-8")
    assert not _unresolved_references(tmp_path, [source])

    for legacy in dict.fromkeys(references):
        missing = references.copy()
        missing.remove(legacy)
        target.write_text("\n".join(missing), encoding="utf-8")
        assert _unresolved_references(tmp_path, [source]) == [(legacy, source)]

        excessive = [*references, legacy]
        target.write_text("\n".join(excessive), encoding="utf-8")
        assert _unresolved_references(tmp_path, [source]) == [(legacy, source)]


def test_incident_history_rejects_different_unresolved_references(tmp_path: pathlib.Path) -> None:
    """既知参照を別の失効起動名へ置換し、失効パスも加えた違反を報告する。"""
    source = pathlib.Path("docs/development/incidents.md")
    (tmp_path / source).parent.mkdir(parents=True)
    references = _known_legacy_references()
    replaced = references.pop(0)
    missing_invocation = f"{_PLUGIN_PREFIX}:missing"
    missing_path = f"{_PLUGIN_PREFIX}/skills/missing/SKILL.md"
    (tmp_path / source).write_text(
        "\n".join([missing_invocation, missing_path, *references]),
        encoding="utf-8",
    )

    unresolved = _unresolved_references(tmp_path, [source])
    assert unresolved == [(missing_invocation, source), (missing_path, source), (replaced, source)]
    formatted = _format_unresolved(unresolved)
    assert missing_invocation in formatted
    assert missing_path in formatted
    assert replaced in formatted
    assert str(source) in formatted
