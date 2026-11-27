"""agent-basicsをagent-toolkitなしで導入したエージェントが、配布物の中だけで指示を理解・実行できることを検査する。

機械的に判定できる依存（agent-toolkitの名前空間・ファイル・コマンド・MCP、参照先の不在、読込表の別ファイル化）だけを扱う。
新しく持ち込まれた未定義の用語は検出できないため、`.claude/skills/agent-toolkit-edit/references/agent-documents.md`「agent-basicsとの責務境界」の
単体利用の確認で補う。
"""

import pathlib
import re

import pytest

from scripts.verification_scan_rules import BASICS_SEARCH_SKILLS, scan_files

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_BASICS = _REPO_ROOT / "agent-basics"
_SKILLS = _BASICS / "skills"

# 人間向けの導入案内はagent-toolkitとの併用手順を説明するため対象外とする（README.md）。
_DISTRIBUTED_DOCUMENTS = scan_files(__file__, "distributed")


_TOOLKIT_DEPENDENCIES = {
    "agent-toolkitの名前空間": re.compile(r"agent-toolkit:"),
    "agent-toolkitの共有文書": re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/skills/atk-mode/"),
    "atkコマンド": re.compile(r"(?<![\w./-])atk(?=[\s`])"),
    "atk-autoの標識": re.compile(r"atk-auto"),
    "agent-toolkitのMCPツール": re.compile(r"agents_server|mcp__plugin_agent-toolkit"),
}


_PROJECT_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "CLAUDE.local.md"})
_PATH_SPAN = re.compile(r"`([^`\n]+?\.md)(?:#[^`\n]*)?`|\]\(([^)\s]+?\.md)(?:#[^)\s]*)?\)")


def _toolkit_dependencies(path: pathlib.Path) -> list[str]:
    found: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        found.extend(f"{path}:{number}: {name}" for name, pattern in _TOOLKIT_DEPENDENCIES.items() if pattern.search(line))
    return found


def _unresolved_references(path: pathlib.Path, skills: pathlib.Path) -> list[str]:
    """資料を指すMarkdownのパスのうち、agent-basicsの中に実体が無いものを返す。

    「`agent-basics:bugfix`の`references/x.md`」や「同スキルの`references/x.md`」のように
    スキル名を文脈で示す参照があるため、配布物内のいずれかのスキルの同じ相対位置に実体があれば解決済みとする。
    """
    distribution = skills.parent.resolve()
    skill_dirs = [entry for entry in skills.iterdir() if entry.is_dir()]
    owner = next(parent for parent in path.parents if parent.parent == skills)
    unresolved: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in _PATH_SPAN.finditer(line):
            value = match[1] or match[2]
            if any(char in value for char in "<*{$ ") or value.startswith(("http://", "https://")):
                continue
            if value in _PROJECT_FILES:
                continue
            # 公式ドキュメントのURL配下のページ名を列挙する行は、外部の資料を指す。
            if "/" not in value and "https://" in line:
                continue
            if value.startswith("agent-basics:"):
                rest = value.removeprefix("agent-basics:")
                candidates = [skills / rest] if "/" in rest else [skills / rest / "SKILL.md"]
            else:
                candidates = [path.parent / value, owner / value, skills / value]
                candidates += [directory / value for directory in skill_dirs]
                candidates += [directory / "references" / value for directory in skill_dirs]
            # Claude Code・Codexともプラグインのルート（skillsの親）全体を配布するため、
            # ルート直下の第三者表示なども配布物に含める。
            targets = [candidate.resolve() for candidate in candidates]
            if any(target.is_relative_to(distribution) and target.is_file() for target in targets):
                continue
            repository_path = (distribution.parent / value).resolve()
            if any(not target.is_relative_to(distribution) and target.is_file() for target in [*targets, repository_path]):
                reason = "配布物の外を指す。単体配布の中から読める資料へ置き換える"
            else:
                reason = "資料が見つからない。単体配布の現行パスへ置き換える"
            unresolved.append(f"{path}:{number}: {value}: {reason}")
    return unresolved


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "配布対象全資料の参照・依存を走査する不変条件。母集団を縮めると欠落を見" + "逃す。同群Linux CI所有CPU最大0.384秒。"
    ),
)
def test_agent_basics_does_not_depend_on_agent_toolkit() -> None:
    assert _DISTRIBUTED_DOCUMENTS
    found = [line for path in _DISTRIBUTED_DOCUMENTS for line in _toolkit_dependencies(path)]
    assert not found, "\n".join(found)


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "配布対象全資料の参照・依存を走査する不変条件。母集団を縮めると欠落を見" + "逃す。同群Linux CI所有CPU最大0.384秒。"
    ),
)
def test_agent_basics_references_resolve_inside_distribution() -> None:
    unresolved = [line for path in _DISTRIBUTED_DOCUMENTS for line in _unresolved_references(path, _SKILLS)]
    assert not unresolved, (
        "単体配布で開けない参照（監査記録は条文から指さず、記録の見出しのファイルパスと節名を索引にする）:\n"
        + "\n".join(unresolved)
    )


@pytest.mark.parametrize(
    ("reference", "conditions"),
    [
        ("gitlab-ci.md", ("編集", "確認", "参照")),
        ("pytilpack.md", ("読み書き", "参照", "新しく実装する")),
    ],
)
def test_technical_materials_keep_read_only_decision_triggers(reference: str, conditions: tuple[str, ...]) -> None:
    text = (_SKILLS / "writing-standards/SKILL.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("|") and f"`references/{reference}`" in line]
    assert rows

    judgments = [row for row in rows if "| 操作 |" not in row and "| 起動時 |" not in row]
    assert any(all(condition in row for condition in conditions) for row in judgments)
    description = text.split("---", 2)[1]
    assert all(condition in description for condition in conditions)


@pytest.mark.parametrize("skill", BASICS_SEARCH_SKILLS)
def test_search_skills_are_only_published_from_basics(skill: str) -> None:
    assert (_SKILLS / skill / "SKILL.md").is_file()
    assert not (_REPO_ROOT / "agent-toolkit/skills" / skill / "SKILL.md").exists()


@pytest.mark.cpu_limit(
    1.5,
    reason=(
        "配布対象全資料の参照・依存を走査する不変条件。母集団を縮めると欠落を見" + "逃す。同群Linux CI所有CPU最大0.384秒。"
    ),
)
def test_reading_tables_are_not_split_into_other_files() -> None:
    listed = scan_files(__file__)
    assert listed
    split_files = [path.relative_to(_REPO_ROOT).as_posix() for path in scan_files(__file__, "names")]
    markers = [
        f"{path.relative_to(_REPO_ROOT)}:{number}"
        for path in listed
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "read-table-source" in line
    ]
    assert not split_files, "\n".join(split_files)
    assert not markers, "\n".join(markers)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("詳細は`agent-toolkit:atk-mode`に従う。", "agent-toolkitの名前空間"),
        ("`${CLAUDE_PLUGIN_ROOT}/skills/atk-mode/roles/exec.subagent.md`を読む。", "agent-toolkitの共有文書"),
        ("`atk wi show`で取得する。", "atkコマンド"),
        ("全体を`atk-auto`要素で囲む。", "atk-autoの標識"),
        ("`agents_server`の`start`で起動する。", "agent-toolkitのMCPツール"),
    ],
)
def test_toolkit_dependency_is_detected(tmp_path: pathlib.Path, line: str, expected: str) -> None:
    path = tmp_path / "SKILL.md"
    path.write_text(f"# 例\n\n{line}\n", encoding="utf-8")
    assert _toolkit_dependencies(path) == [f"{path}:3: {expected}"]


def test_reference_outside_distribution_is_detected(tmp_path: pathlib.Path) -> None:
    """agent-basicsの中に無い役割文書と監査記録への参照を、参照先の不在として検出する。"""
    skills = tmp_path / "plugin/skills"
    outside = tmp_path / "docs/development/audit-records.md"
    outside.parent.mkdir(parents=True)
    outside.write_text("# 監査\n", encoding="utf-8")
    (skills / "sample/references").mkdir(parents=True)
    (skills / "sample/references/rule.md").write_text("# 基準\n", encoding="utf-8")
    path = skills / "sample/SKILL.md"
    path.write_text(
        "# 例\n\n`references/rule.md`と`docs/development/audit-records.md`と"
        "`docs/development/audit-records-search.md`と`AGENTS.md`に従い、"
        "`skills/atk-mode/roles/exec.subagent.md`を読む。\n",
        encoding="utf-8",
    )
    assert _unresolved_references(path, skills) == [
        f"{path}:3: docs/development/audit-records.md: 配布物の外を指す。単体配布の中から読める資料へ置き換える",
        f"{path}:3: docs/development/audit-records-search.md: 資料が見つからない。単体配布の現行パスへ置き換える",
        f"{path}:3: skills/atk-mode/roles/exec.subagent.md: 資料が見つからない。単体配布の現行パスへ置き換える",
    ]
