"""agent-toolkit参照の主題別不変条件を検証する。"""

from __future__ import annotations

import pathlib
import re

from agent_toolkit._common import read_triggers

from _test_helpers import _PLUGIN_PREFIX, _format_unresolved, _tracked_source_paths

# 見出し名で節を指す参照を検査する規範Markdownの範囲。
_NORMATIVE_MARKDOWN_PREFIXES = (
    "agent-basics/skills/",
    f"{_PLUGIN_PREFIX}/rules/",
    f"{_PLUGIN_PREFIX}/skills/",
    f"{_PLUGIN_PREFIX}/share/",
    ".claude/skills/",
    ".chezmoi-source/dot_claude/",
)


_PLUGIN_ROOT_VARIABLES = ("${CLAUDE_PLUGIN_ROOT}/", "${PLUGIN_ROOT}/", "<plugin root>/")


_HEADING_REFERENCE_PATTERN = re.compile(
    rf"(?:(?P<plugin>{_PLUGIN_PREFIX}|agent-basics):(?P<skill>[A-Za-z0-9_-]+)`?の)?`(?P<path>[^`\s]+?\.md)`(?:の)?「(?P<name>[^」\n]+)」"
)


# `` `<パス>.md`の`### <見出し>` ``のように見出し記号付きで節を指す記法。
_HEADING_SYMBOL_REFERENCE_PATTERN = re.compile(
    rf"(?:(?P<plugin>{_PLUGIN_PREFIX}|agent-basics):(?P<skill>[A-Za-z0-9_-]+)`?の)?"
    r"`(?P<path>[^`\s]+?\.md)`の`#{2,6} (?P<name>[^`\n]+)`"
)


# 規範Markdownに加えて見出し名参照を検査する設計文書。
_EXTENDED_HEADING_REFERENCE_DOCS = re.compile(r"docs/development/(?:design[^/]*|architecture|operations)\.md")


def _normative_markdown_paths(sources: list[pathlib.Path]) -> list[pathlib.Path]:
    """追跡ファイルのうち見出し名参照を検査する規範Markdownを返す。"""
    return [
        path
        for path in sources
        if path.suffix == ".md" and (path.as_posix() == "AGENTS.md" or path.as_posix().startswith(_NORMATIVE_MARKDOWN_PREFIXES))
    ]


def _extended_heading_reference_sources(sources: list[pathlib.Path]) -> list[pathlib.Path]:
    """規範Markdownの外で見出し名参照を検査する追跡ファイル（テスト以外のPythonと設計文書）を返す。

    テストは欠落した参照をfixtureとして意図的に含み、方針記録（`concepts*.md`）と障害記録（`incidents*.md`）は
    当時の節名を記録として保持するため、検査の対象から外す。
    """
    return [
        path
        for path in sources
        if (path.suffix == ".py" and not path.name.endswith("_test.py"))
        or _EXTENDED_HEADING_REFERENCE_DOCS.fullmatch(path.as_posix())
    ]


def _resolve_heading_reference_target(
    root: pathlib.Path,
    source: pathlib.Path,
    skill: str | None,
    reference: str,
    markdown_paths: list[pathlib.Path],
    plugin: str | None = None,
) -> tuple[pathlib.Path | None, bool]:
    """参照が一意に指すMarkdownと、候補が複数あって一意に決まらないかを返す。

    候補が0件の参照（生成物の名前や`<役割名>`のような置換用の名前）は検査の対象外として`(None, False)`を返す。
    スキル名で修飾した参照がそのスキルの配下に無い場合と、末尾一致の候補が複数ある場合は`(None, True)`を返す。
    """
    for variable in _PLUGIN_ROOT_VARIABLES:
        reference = reference.replace(variable, f"{_PLUGIN_PREFIX}/")
    if reference.startswith(("agent-toolkit:", "agent-basics:")):
        return read_triggers.resolve_document(reference, root / source, root / _PLUGIN_PREFIX), False
    if skill:
        bases = [pathlib.Path(plugin or _PLUGIN_PREFIX, "skills", skill), pathlib.Path(plugin or _PLUGIN_PREFIX, "skills")]
    else:
        bases = [pathlib.Path("."), *source.parents]
    for base in bases:
        candidate = root / base / reference
        if candidate.is_file():
            return read_triggers.resolve_read_source(candidate, root / _PLUGIN_PREFIX), False
    if skill:
        return None, True
    suffix_matches = [path for path in markdown_paths if path.as_posix().endswith(f"/{reference}")]
    if len(suffix_matches) == 1:
        return read_triggers.resolve_read_source(root / suffix_matches[0], root / _PLUGIN_PREFIX), False
    return None, len(suffix_matches) > 1


# 「<節名>」の直後で段落番号により位置を指す記法。節の改訂で番号がずれても検査を通るため失敗にする。
_PARAGRAPH_NUMBER_PATTERN = re.compile(r"の?(?:第?\d+段落|段落\d+)")


# 参照より前の同じ文でバッククォートにより名指しする識別子。参照先の節がその識別子を定めると読める記法を対象にする。
_NAMED_IDENTIFIER_PATTERN = re.compile(r"`([a-z][a-z0-9]*_[a-z0-9_]+)`")


_SENTENCE_BOUNDARY_PATTERN = re.compile(r"。|\n|：|: ")


def _missing_named_identifiers(content: str, match: re.Match[str], target: pathlib.Path) -> list[str]:
    """参照より前の同じ文で名指しした識別子のうち、参照先の節の本文に無いものを返す。

    表の行は列ごとに別の対象を並べるため除く。行全体を対象にすると、参照と無関係な識別子を数えて誤検出が増える
    （2026-11-06の試作で行全体は76件、同じ文で参照より前に限ると3件で、3件とも実際の不足だった）。
    """
    line_start = content.rfind("\n", 0, match.start()) + 1
    if content[line_start:].lstrip().startswith("|"):
        return []
    boundaries = [found.end() for found in _SENTENCE_BOUNDARY_PATTERN.finditer(content, 0, match.start())]
    sentence = content[boundaries[-1] if boundaries else 0 : match.start()]
    names = [found.group(1) for found in _NAMED_IDENTIFIER_PATTERN.finditer(sentence)]
    if not names:
        return []
    try:
        body = read_triggers.ReadTarget(target, match.group("name")).body()
    except ValueError:
        return []
    return [name for name in names if name not in body]


def _unresolved_heading_references(
    root: pathlib.Path,
    sources: list[pathlib.Path],
    markdown_paths: list[pathlib.Path],
) -> list[tuple[str, pathlib.Path]]:
    """見出し名参照の不備を、参照と参照元の対で返す。

    不備は、参照先に同名の見出しも本文の文字列も無いこと、参照先を一意に解決できないこと、
    節名の後に段落番号で位置を指すこと、参照より前の同じ文で名指しした識別子が参照先の節に無いことである。
    """
    unresolved: list[tuple[str, pathlib.Path]] = []
    for source in sources:
        content = (root / source).read_text(encoding="utf-8")
        matches = [*_HEADING_REFERENCE_PATTERN.finditer(content), *_HEADING_SYMBOL_REFERENCE_PATTERN.finditer(content)]
        for match in matches:
            target, ambiguous = _resolve_heading_reference_target(
                root, source, match.group("skill"), match.group("path"), markdown_paths, match.group("plugin")
            )
            line = content[
                content.rfind("\n", 0, match.start()) + 1 : content.find("\n", match.end())
                if "\n" in content[match.end() :]
                else len(content)
            ]
            contextual = [
                item
                for item in read_triggers.material_targets(
                    line,
                    root / source,
                    root / _PLUGIN_PREFIX,
                    references_only=True,
                    context=read_triggers.reference_context(
                        content[: content.rfind("\n", 0, match.start())], root / source, root / _PLUGIN_PREFIX
                    ),
                )
                if match.group("name") in item.heading and item.path.is_file()
            ]
            if len({item.path for item in contextual}) == 1:
                target = contextual[0].path
            reference = f"{match.group('path')}「{match.group('name')}」"
            if target is None:
                if ambiguous:
                    unresolved.append((f"{reference}（参照先を一意に解決できない）", source))
                continue
            if match.group("name") not in target.read_text(encoding="utf-8"):
                unresolved.append((reference, source))
                continue
            if _PARAGRAPH_NUMBER_PATTERN.match(content, match.end()):
                unresolved.append((f"{reference}（段落番号で位置を指す）", source))
            unresolved.extend(
                (f"{reference}（`{name}`が参照先の節に無い）", source)
                for name in _missing_named_identifiers(content, match, target)
            )
    return unresolved


def test_normative_heading_references_resolve() -> None:
    """規範Markdownの見出し名参照が、参照先に残る見出しまたは本文の文字列へ解決する。"""
    root = pathlib.Path(__file__).resolve().parent
    tracked = _tracked_source_paths(root)
    markdown_paths = [path for path in tracked if path.suffix == ".md"]
    sources = _normative_markdown_paths(tracked)
    assert any(path.as_posix().startswith(".claude/skills/") for path in sources)
    with read_triggers.resolution_scope():
        unresolved = _unresolved_heading_references(root, sources, markdown_paths)
    assert not unresolved, _format_unresolved(unresolved)


def test_code_and_design_heading_references_resolve() -> None:
    """テスト以外のPythonと設計文書の見出し名参照が、参照先に残る見出しまたは本文の文字列へ解決する。"""
    root = pathlib.Path(__file__).resolve().parent
    tracked = _tracked_source_paths(root)
    markdown_paths = [path for path in tracked if path.suffix == ".md"]
    sources = _extended_heading_reference_sources(tracked)
    assert any(path.suffix == ".py" for path in sources)
    assert any(path.as_posix().startswith("docs/development/design") for path in sources)
    with read_triggers.resolution_scope():
        unresolved = _unresolved_heading_references(root, sources, markdown_paths)
    assert not unresolved, _format_unresolved(unresolved)


def test_heading_symbol_reference_fails_after_heading_removal(tmp_path: pathlib.Path) -> None:
    """見出し記号付きで節を指す参照も、参照先から節が消えると未解決として報告する。"""
    standard = tmp_path / "docs" / "standard.md"
    standard.parent.mkdir(parents=True)
    standard.write_text("# 基準\n\n### 受入シナリオ\n", encoding="utf-8")
    source = pathlib.Path("tool.py")
    (tmp_path / source).write_text(
        'MESSAGE = "`docs/standard.md`の`### 受入シナリオ`と`docs/standard.md`の`### 消えた節`"\n', encoding="utf-8"
    )
    markdown_paths = [standard.relative_to(tmp_path)]

    assert _unresolved_heading_references(tmp_path, [source], markdown_paths) == [("docs/standard.md「消えた節」", source)]


def test_heading_reference_follows_explicit_compatibility_source(tmp_path: pathlib.Path) -> None:
    """転送先の節の実在を検査し、消えた節を互換入口の案内文で補わない。"""
    toolkit = tmp_path / "agent-toolkit"
    basics = tmp_path / "agent-basics"
    (basics / ".claude-plugin").mkdir(parents=True)
    (basics / ".claude-plugin/plugin.json").write_text('{"name":"agent-basics"}', encoding="utf-8")
    actual = basics / "skills/writing-standards/references/rule.md"
    actual.parent.mkdir(parents=True)
    actual.write_text("# 品質\n## 判定\n本文\n", encoding="utf-8")
    wrapper = toolkit / "skills/sample/references/rule.md"
    wrapper.parent.mkdir(parents=True)
    wrapper.write_text(
        "# 入口\n\n<!-- read-source: agent-basics:writing-standards/references/rule.md -->\n\n判定を引き継ぐ。\n",
        encoding="utf-8",
    )
    source = pathlib.Path("source.md")
    (tmp_path / source).write_text("`agent-basics:writing-standards`の`references/rule.md`「判定」\n", encoding="utf-8")
    paths = [actual.relative_to(tmp_path), wrapper.relative_to(tmp_path), source]
    assert not _unresolved_heading_references(tmp_path, [source], paths)
    actual.write_text("# 品質\n節を撤去した。\n", encoding="utf-8")
    assert _unresolved_heading_references(tmp_path, [source], paths) == [("references/rule.md「判定」", source)]


def test_heading_reference_keeps_same_skill_context(tmp_path: pathlib.Path) -> None:
    """直前に起動した同スキルのSKILLを、参照元自身のSKILLと区別する。"""
    root = tmp_path / "agent-basics/skills"
    manifest = root.parent / ".claude-plugin/plugin.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"name":"agent-basics"}', encoding="utf-8")
    bugfix = root / "bugfix/SKILL.md"
    bugfix.parent.mkdir(parents=True)
    bugfix.write_text("# 不具合\n## 所要の原因調査\n基準\n", encoding="utf-8")
    source = pathlib.Path("agent-toolkit/share/exec-review-responses.md")
    (tmp_path / source).parent.mkdir(parents=True)
    (tmp_path / source).parent.parent.joinpath("SKILL.md").write_text("# レビュー\n", encoding="utf-8")
    (tmp_path / source).write_text(
        "`agent-basics:bugfix`を起動し、同スキルの`SKILL.md`「所要の原因調査」へ渡す。\n", encoding="utf-8"
    )
    assert not _unresolved_heading_references(tmp_path, [source], [bugfix.relative_to(tmp_path), source])


def test_extended_heading_reference_sources_exclude_tests_and_records() -> None:
    """テスト、方針記録および障害記録を見出し名参照の拡大範囲から外し、コードと設計文書を含める。"""
    paths = [
        pathlib.Path("src/sample.py"),
        pathlib.Path("src/sample_test.py"),
        pathlib.Path("docs/development/design-hosts.md"),
        pathlib.Path("docs/development/architecture.md"),
        pathlib.Path("docs/development/operations.md"),
        pathlib.Path("docs/development/concepts-runtime.md"),
        pathlib.Path("docs/development/incidents-validation.md"),
    ]

    assert _extended_heading_reference_sources(paths) == [paths[0], paths[2], paths[3], paths[4]]


def test_heading_reference_fails_after_heading_removal(tmp_path: pathlib.Path) -> None:
    """参照先の見出しを削除すると、その見出しを名前で指す全ての参照を未解決として報告する。"""
    rules = tmp_path / _PLUGIN_PREFIX / "share" / "rules" / "common" / "01-agent.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("# 規範\n\n### 調査と検証\n\n観測で主張を支える。\n", encoding="utf-8")
    skill = pathlib.Path(_PLUGIN_PREFIX, "skills", "sample", "SKILL.md")
    (tmp_path / skill).parent.mkdir(parents=True)
    (tmp_path / skill).write_text(
        "\n".join(
            [
                f"`{_PLUGIN_PREFIX}/share/rules/common/01-agent.md`「調査と検証」に従う。",
                f"`{_PLUGIN_PREFIX}/share/rules/common/01-agent.md`の「調査と検証」を読む。",
                "`${CLAUDE_PLUGIN_ROOT}/share/rules/common/01-agent.md`「調査と検証」を参照する。",
            ]
        ),
        encoding="utf-8",
    )
    sources = [skill]
    markdown_paths = [pathlib.Path(_PLUGIN_PREFIX, "share", "rules", "common", "01-agent.md"), skill]
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


def test_ambiguous_heading_reference_is_reported(tmp_path: pathlib.Path) -> None:
    """候補が複数ある参照を未解決として報告し、候補0件の生成物名と一意な参照は報告しない。

    `share/`配下から「本スキルの`SKILL.md`「<節名>」」と書くと、どのSKILL.mdも参照先として一意に決まらない。
    """
    for skill in ("first", "second"):
        document = tmp_path / _PLUGIN_PREFIX / "skills" / skill / "SKILL.md"
        document.parent.mkdir(parents=True)
        document.write_text("# スキル\n\n## 共通操作\n", encoding="utf-8")
    operations = tmp_path / _PLUGIN_PREFIX / "share" / "operations.md"
    operations.parent.mkdir(parents=True)
    operations.write_text("## 共通操作\n", encoding="utf-8")
    source = pathlib.Path(_PLUGIN_PREFIX, "share", "criteria.md")
    (tmp_path / source).write_text(
        "本スキルの`SKILL.md`「共通操作」に従う。\n`operations.md`「共通操作」に従う。\n`stats.md`「集計」を読む。\n",
        encoding="utf-8",
    )
    markdown_paths = [path.relative_to(tmp_path) for path in tmp_path.rglob("*.md")]

    assert _unresolved_heading_references(tmp_path, [source], markdown_paths) == [
        ("SKILL.md「共通操作」（参照先を一意に解決できない）", source)
    ]


def test_paragraph_number_reference_is_reported(tmp_path: pathlib.Path) -> None:
    """節名の後に段落番号で位置を指す参照を報告する。節の改訂で番号がずれても見出しの実在だけでは検出できない。"""
    target = tmp_path / "docs" / "prevention.md"
    target.parent.mkdir(parents=True)
    target.write_text("# 再発防止\n\n## 再発防止策\n\n本文\n\n## 自動検出の選定\n\n基準\n", encoding="utf-8")
    source = pathlib.Path("rule.md")
    (tmp_path / source).write_text(
        "`docs/prevention.md`「再発防止策」の段落4に従う。\n`docs/prevention.md`「自動検出の選定」に従う。\n", encoding="utf-8"
    )

    assert _unresolved_heading_references(tmp_path, [source], [target.relative_to(tmp_path), source]) == [
        ("docs/prevention.md「再発防止策」（段落番号で位置を指す）", source)
    ]


def test_named_identifier_must_exist_in_referenced_section(tmp_path: pathlib.Path) -> None:
    """参照より前の同じ文で名指しした識別子が参照先の節に無ければ報告し、表の行と別の文の識別子は判定しない。"""
    target = tmp_path / "docs" / "integration.md"
    target.parent.mkdir(parents=True)
    target.write_text(
        "# 統合\n\n## 履歴統合\n\n`history_compare`で照合する。\n\n## 別の節\n\n`guard_value`を記録する。\n",
        encoding="utf-8",
    )
    source = pathlib.Path("rule.md")
    (tmp_path / source).write_text(
        "\n".join(
            [
                "`guard_value`の規約は、`docs/integration.md`「履歴統合」にのみ定義されている。",
                "`history_compare`の手順は`docs/integration.md`「履歴統合」に従う。",
                "`guard_value`を残す。次に`docs/integration.md`「履歴統合」へ進む。",
                "| `guard_value` | `docs/integration.md`「履歴統合」 |",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert _unresolved_heading_references(tmp_path, [source], [target.relative_to(tmp_path), source]) == [
        ("docs/integration.md「履歴統合」（`guard_value`が参照先の節に無い）", source)
    ]
