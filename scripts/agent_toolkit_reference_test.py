"""agent-toolkitスキルとPythonファイルへの参照が追跡ファイルから実体へ解決することを検査する。

`git ls-files`を入力にすることで、通常の検索が省く隠しディレクトリも対象に含める。
検査対象は`.json`・`.md`・`.py`であり、`install-claude.sh`と`install-claude.ps1`は含まない。
規範Markdownが`` `<パス>.md` ``の直後に「<名前>」で節を指す参照は、参照先に同じ名前が残ることも検査する。
このファイル自身も走査対象となるため、欠損参照のテスト入力は接頭辞から組み立てる。
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
_INCIDENTS_RUNTIME = pathlib.Path("docs/development/incidents-runtime.md")
_INCIDENTS_VALIDATION = pathlib.Path("docs/development/incidents-validation.md")
_INCIDENTS_WORKFLOWS = pathlib.Path("docs/development/incidents-workflows.md")
_AUDIT_RECORDS = pathlib.Path("docs/development/audit-records.md")
_SESSION_RECORDS = pathlib.Path("agent-toolkit/agent_toolkit/_atk/session_records.py")
_SESSION_RECORDS_TEST = pathlib.Path("agent-toolkit/agent_toolkit/_atk/session_records_test.py")
_PROCESS_LOOP_TEST = pathlib.Path("agent-toolkit/agent_toolkit/_atk/wi/process_loop_test.py")
_ALLOWED_UNRESOLVED_REFERENCE_COUNTS = {
    (f"{_PLUGIN_PREFIX}:agent-standards", _INCIDENTS_VALIDATION): 1,
    (f"{_PLUGIN_PREFIX}:feedback-standards", _INCIDENTS_WORKFLOWS): 1,
    (f"{_PLUGIN_PREFIX}:process-feedbacks", _INCIDENTS_VALIDATION): 1,
    (f"{_PLUGIN_PREFIX}:process-feedbacks", _INCIDENTS_WORKFLOWS): 4,
    (f"{_PLUGIN_PREFIX}:reviewee-standards", _INCIDENTS_WORKFLOWS): 1,
    (f"{_PLUGIN_PREFIX}:shell-exec", _INCIDENTS_WORKFLOWS): 1,
    (f"{_PLUGIN_PREFIX}/scripts/hook.py", _INCIDENTS_RUNTIME): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _INCIDENTS_RUNTIME): 5,
    (f"{_PLUGIN_PREFIX}:exit-session", _AUDIT_RECORDS): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _SESSION_RECORDS): 1,
    (f"{_PLUGIN_PREFIX}:exit-session", _SESSION_RECORDS_TEST): 2,
    (f"{_PLUGIN_PREFIX}:exit-session", _PROCESS_LOOP_TEST): 1,
}


def _codex_snapshot_path(source: pathlib.Path) -> pathlib.Path | None:
    """agent-toolkit配下の原本に対応するCodex生成snapshotのパスを返す。"""
    if not source.parts or source.parts[0] != _PLUGIN_PREFIX:
        return None
    return pathlib.Path(f"{_PLUGIN_PREFIX}-codex", *source.parts[1:])


def _allowed_unresolved_reference_counts(
    sources: list[pathlib.Path],
) -> dict[tuple[str, pathlib.Path], int]:
    """検査対象に実在する原本とCodex生成snapshotの既知参照数を返す。"""
    source_set = set(sources)
    allowed_counts: dict[tuple[str, pathlib.Path], int] = {}
    for (reference, source), count in _ALLOWED_UNRESOLVED_REFERENCE_COUNTS.items():
        candidates = (source, _codex_snapshot_path(source))
        for candidate in candidates:
            if candidate is not None and candidate in source_set:
                allowed_counts[(reference, candidate)] = count
    return allowed_counts


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
        if raw
        and pathlib.Path(raw.decode("utf-8")).suffix in _SOURCE_SUFFIXES
        and (root / pathlib.Path(raw.decode("utf-8"))).is_file()
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
    allowed_counts = _allowed_unresolved_reference_counts(sources)
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


def test_codex_snapshot_requires_exact_known_legacy_references(tmp_path: pathlib.Path) -> None:
    """Codex生成snapshotでも原本と同数の既知の失効参照だけを受理する。"""
    source = _codex_snapshot_path(_SESSION_RECORDS)
    assert source is not None
    target = tmp_path / source
    target.parent.mkdir(parents=True)
    legacy = f"{_PLUGIN_PREFIX}:exit-session"
    target.write_text(legacy, encoding="utf-8")

    assert not _unresolved_references(tmp_path, [source])

    target.write_text(f"{legacy}\n{legacy}\n", encoding="utf-8")
    assert _unresolved_references(tmp_path, [source]) == [(legacy, source)]


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


def _known_legacy_references() -> dict[pathlib.Path, list[str]]:
    """分割後の事故記録へ保持する既知の失効参照を返す。"""
    return {
        _INCIDENTS_RUNTIME: [
            f"{_PLUGIN_PREFIX}/scripts/hook.py",
            *[f"{_PLUGIN_PREFIX}:exit-session"] * 5,
        ],
        _INCIDENTS_VALIDATION: [
            f"{_PLUGIN_PREFIX}:agent-standards",
            f"{_PLUGIN_PREFIX}:process-feedbacks",
        ],
        _INCIDENTS_WORKFLOWS: [
            f"{_PLUGIN_PREFIX}:feedback-standards",
            *[f"{_PLUGIN_PREFIX}:process-feedbacks"] * 4,
            f"{_PLUGIN_PREFIX}:reviewee-standards",
            f"{_PLUGIN_PREFIX}:shell-exec",
        ],
    }


def test_incident_history_requires_exact_known_legacy_references(tmp_path: pathlib.Path) -> None:
    """既知の失効参照を過不足なく保持し、各参照の不足と超過を報告する。"""
    by_source = _known_legacy_references()
    for source, references in by_source.items():
        target = tmp_path / source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(references), encoding="utf-8")
    sources = list(by_source)
    assert not _unresolved_references(tmp_path, sources)

    for source, references in by_source.items():
        target = tmp_path / source
        for legacy in dict.fromkeys(references):
            missing = references.copy()
            missing.remove(legacy)
            target.write_text("\n".join(missing), encoding="utf-8")
            assert _unresolved_references(tmp_path, sources) == [(legacy, source)]

            excessive = [*references, legacy]
            target.write_text("\n".join(excessive), encoding="utf-8")
            assert _unresolved_references(tmp_path, sources) == [(legacy, source)]
        target.write_text("\n".join(references), encoding="utf-8")


def test_incident_history_rejects_different_unresolved_references(tmp_path: pathlib.Path) -> None:
    """既知参照を別の失効起動名へ置換し、失効パスも加えた違反を報告する。"""
    by_source = _known_legacy_references()
    for current_source, current_references in by_source.items():
        target = tmp_path / current_source
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join(current_references), encoding="utf-8")
    source = _INCIDENTS_VALIDATION
    references = by_source[source].copy()
    replaced = references.pop(0)
    missing_invocation = f"{_PLUGIN_PREFIX}:missing"
    missing_path = f"{_PLUGIN_PREFIX}/skills/missing/SKILL.md"
    (tmp_path / source).write_text(
        "\n".join([missing_invocation, missing_path, *references]),
        encoding="utf-8",
    )

    unresolved = _unresolved_references(tmp_path, list(by_source))
    assert unresolved == [(missing_invocation, source), (missing_path, source), (replaced, source)]
    formatted = _format_unresolved(unresolved)
    assert missing_invocation in formatted
    assert missing_path in formatted
    assert replaced in formatted
    assert str(source) in formatted


# 見出し名で節を指す参照を検査する規範Markdownの範囲。
_NORMATIVE_MARKDOWN_PREFIXES = (
    f"{_PLUGIN_PREFIX}/rules/",
    f"{_PLUGIN_PREFIX}/skills/",
    f"{_PLUGIN_PREFIX}/share/",
    ".claude/skills/",
    ".chezmoi-source/dot_claude/",
)
_PLUGIN_ROOT_VARIABLES = ("${CLAUDE_PLUGIN_ROOT}/", "${PLUGIN_ROOT}/", "<plugin root>/")
_HEADING_REFERENCE_PATTERN = re.compile(
    rf"(?:{_PLUGIN_PREFIX}:(?P<skill>[A-Za-z0-9_-]+)`?の)?`(?P<path>[^`\s]+?\.md)`(?:の)?「(?P<name>[^」\n]+)」"
)


def _normative_markdown_paths(sources: list[pathlib.Path]) -> list[pathlib.Path]:
    """追跡ファイルのうち見出し名参照を検査する規範Markdownを返す。"""
    return [
        path
        for path in sources
        if path.suffix == ".md" and (path.as_posix() == "AGENTS.md" or path.as_posix().startswith(_NORMATIVE_MARKDOWN_PREFIXES))
    ]


def _resolve_heading_reference_target(
    root: pathlib.Path,
    source: pathlib.Path,
    skill: str | None,
    reference: str,
    markdown_paths: list[pathlib.Path],
) -> pathlib.Path | None:
    """参照が一意に指すMarkdownを返す。一意に決まらない参照は検査の対象外としてNoneを返す。"""
    for variable in _PLUGIN_ROOT_VARIABLES:
        reference = reference.replace(variable, f"{_PLUGIN_PREFIX}/")
    if skill:
        bases = [pathlib.Path(_PLUGIN_PREFIX, "skills", skill), pathlib.Path(_PLUGIN_PREFIX, "skills")]
    else:
        bases = [pathlib.Path("."), *source.parents]
    for base in bases:
        candidate = root / base / reference
        if candidate.is_file():
            return candidate
    if skill:
        return None
    suffix_matches = [path for path in markdown_paths if path.as_posix().endswith(f"/{reference}")]
    return root / suffix_matches[0] if len(suffix_matches) == 1 else None


def _unresolved_heading_references(
    root: pathlib.Path,
    sources: list[pathlib.Path],
    markdown_paths: list[pathlib.Path],
) -> list[tuple[str, pathlib.Path]]:
    """参照先に同名の見出しも本文の文字列も無い見出し名参照を、参照と参照元の対で返す。"""
    unresolved: list[tuple[str, pathlib.Path]] = []
    for source in sources:
        content = (root / source).read_text(encoding="utf-8")
        for match in _HEADING_REFERENCE_PATTERN.finditer(content):
            target = _resolve_heading_reference_target(root, source, match.group("skill"), match.group("path"), markdown_paths)
            if target is None:
                continue
            if match.group("name") not in target.read_text(encoding="utf-8"):
                unresolved.append((f"{match.group('path')}「{match.group('name')}」", source))
    return unresolved


def test_normative_heading_references_resolve() -> None:
    """規範Markdownの見出し名参照が、参照先に残る見出し又は本文の文字列へ解決する。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    tracked = _tracked_source_paths(root)
    markdown_paths = [path for path in tracked if path.suffix == ".md"]
    sources = _normative_markdown_paths(tracked)
    assert any(path.as_posix().startswith(".claude/skills/") for path in sources)
    unresolved = _unresolved_heading_references(root, sources, markdown_paths)
    assert not unresolved, _format_unresolved(unresolved)


def test_heading_reference_fails_after_heading_removal(tmp_path: pathlib.Path) -> None:
    """参照先の見出しを削除すると、その見出しを名前で指す全ての参照を未解決として報告する。"""
    rules = tmp_path / _PLUGIN_PREFIX / "rules" / "01-agent.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("# 規範\n\n### 調査と検証\n\n観測で主張を支える。\n", encoding="utf-8")
    skill = pathlib.Path(_PLUGIN_PREFIX, "skills", "sample", "SKILL.md")
    (tmp_path / skill).parent.mkdir(parents=True)
    (tmp_path / skill).write_text(
        "\n".join(
            [
                f"`{_PLUGIN_PREFIX}/rules/01-agent.md`「調査と検証」に従う。",
                f"`{_PLUGIN_PREFIX}/rules/01-agent.md`の「調査と検証」を読む。",
                "`${CLAUDE_PLUGIN_ROOT}/rules/01-agent.md`「調査と検証」を参照する。",
            ]
        ),
        encoding="utf-8",
    )
    sources = [skill]
    markdown_paths = [pathlib.Path(_PLUGIN_PREFIX, "rules", "01-agent.md"), skill]
    assert not _unresolved_heading_references(tmp_path, sources, markdown_paths)

    rules.write_text("# 規範\n\n観測で主張を支える。\n", encoding="utf-8")
    unresolved = _unresolved_heading_references(tmp_path, sources, markdown_paths)
    assert [source for _, source in unresolved] == [skill] * 3
    assert all(reference.endswith("「調査と検証」") for reference, _ in unresolved)


def test_heading_reference_resolves_skill_qualified_path(tmp_path: pathlib.Path) -> None:
    """スキル名で修飾した参照資料のパスを、そのスキルの配下で解決する。"""
    target = tmp_path / _PLUGIN_PREFIX / "skills" / "commit" / "references" / "push-and-ci.md"
    target.parent.mkdir(parents=True)
    target.write_text("## 公開状態の4項目\n", encoding="utf-8")
    source = pathlib.Path(".claude", "skills", "sample", "SKILL.md")
    (tmp_path / source).parent.mkdir(parents=True)
    (tmp_path / source).write_text(
        f"`{_PLUGIN_PREFIX}:commit`の`references/push-and-ci.md`「公開状態の4項目」と「存在しない節」\n"
        f"`{_PLUGIN_PREFIX}:commit`の`references/push-and-ci.md`「存在しない節」\n",
        encoding="utf-8",
    )
    markdown_paths = [target.relative_to(tmp_path), source]

    assert _unresolved_heading_references(tmp_path, [source], markdown_paths) == [
        ("references/push-and-ci.md「存在しない節」", source)
    ]
