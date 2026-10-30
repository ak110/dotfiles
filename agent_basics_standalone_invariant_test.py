"""agent-basicsをagent-toolkitなしで導入したエージェントが、配布物の中だけで指示を理解・実行できることを検査する。

機械的に判定できる依存（agent-toolkitの名前空間・ファイル・コマンド・MCP、参照先の不在、読込表の別ファイル化）だけを扱う。
新しく持ち込まれた未定義の用語は検出できないため、`.claude/skills/agent-toolkit-edit/SKILL.md`「agent-basicsとの責務境界」の
単体利用の確認で補う。
"""

import pathlib
import re
import subprocess

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parent
_BASICS = _REPO_ROOT / "agent-basics"
_SKILLS = _BASICS / "skills"

# 人間向けの導入案内はagent-toolkitとの併用手順を説明するため対象外とする（README.md）。
_DISTRIBUTED_DOCUMENTS = sorted(_SKILLS.rglob("*.md"))

# agent-toolkitを導入していないと解決・実行できない参照。
# 条件付きの言及として許す形は設けず、agent-toolkitに固有の手順は該当工程のスキルへ置く。
_TOOLKIT_DEPENDENCIES = {
    "agent-toolkitの名前空間": re.compile(r"agent-toolkit:"),
    "agent-toolkitの共有文書": re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/share/"),
    "atkコマンド": re.compile(r"(?<![\w./-])atk(?=[\s`])"),
    "atk-autoの標識": re.compile(r"atk-auto"),
    "agent-toolkitのMCPツール": re.compile(r"agents_server|mcp__plugin_agent-toolkit"),
}

# 規範の根拠となった検証記録は配布物の外に置く例外として、規範の失効を判定する場面でだけ参照する。
_AUDIT_RECORDS = "docs/development/audit-records.md"
# 対象プロジェクトのファイルとして一般的に指す名前（agent-basicsの資料ではない）。
_PROJECT_FILES = frozenset({"AGENTS.md", "CLAUDE.md", "CLAUDE.local.md"})
_PATH_SPAN = re.compile(r"`([^`\n]+?\.md)(?:#[^`\n]*)?`|\]\(([^)\s]+?\.md)(?:#[^)\s]*)?\)")


def _toolkit_dependencies(path: pathlib.Path) -> list[str]:
    """agent-toolkitを前提とする参照の行を返す。"""
    found: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        found.extend(f"{path}:{number}: {name}" for name, pattern in _TOOLKIT_DEPENDENCIES.items() if pattern.search(line))
    return found


def _unresolved_references(path: pathlib.Path, skills: pathlib.Path) -> list[str]:
    """資料を指すMarkdownのパスのうち、agent-basicsの中に実体が無いものを返す。

    「`agent-basics:bugfix`の`references/x.md`」や「同スキルの`references/x.md`」のように
    スキル名を文脈で示す参照があるため、配布物内のいずれかのスキルの同じ相対位置に実体があれば解決済みとする。
    """
    skill_dirs = [entry for entry in skills.iterdir() if entry.is_dir()]
    owner = next(parent for parent in path.parents if parent.parent == skills)
    unresolved: list[str] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for match in _PATH_SPAN.finditer(line):
            value = match[1] or match[2]
            if any(char in value for char in "<*{$ ") or value.startswith(("http://", "https://")):
                continue
            if value == _AUDIT_RECORDS or value in _PROJECT_FILES:
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
            if not any(
                candidate.resolve().is_file() and candidate.resolve().is_relative_to(skills.resolve())
                for candidate in candidates
            ):
                unresolved.append(f"{path}:{number}: {value}")
    return unresolved


def test_agent_basics_does_not_depend_on_agent_toolkit() -> None:
    """配布物の本文が、agent-toolkitの名前空間・共有文書・atkコマンド・標識・MCPツールを前提にしない。"""
    assert _DISTRIBUTED_DOCUMENTS
    found = [line for path in _DISTRIBUTED_DOCUMENTS for line in _toolkit_dependencies(path)]
    assert not found, "\n".join(found)


def test_agent_basics_references_resolve_inside_distribution() -> None:
    """配布物の本文が指す資料は、監査記録を除いてagent-basicsの中に実在する。"""
    unresolved = [line for path in _DISTRIBUTED_DOCUMENTS for line in _unresolved_references(path, _SKILLS)]
    assert not unresolved, "\n".join(unresolved)


@pytest.mark.parametrize(
    ("reference", "conditions"),
    [
        ("gitlab-ci.md", ("編集", "確認", "参照")),
        ("pytilpack.md", ("読み書き", "参照")),
    ],
)
def test_technical_materials_keep_read_only_decision_triggers(reference: str, conditions: tuple[str, ...]) -> None:
    """書込操作が無い確認・コード読取・API参照にも、判断行から技術資料へ届く。"""
    text = (_SKILLS / "writing-standards/SKILL.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("|") and f"`references/{reference}`" in line]
    assert rows
    assert any("| 判断 |" in row and all(condition in row for condition in conditions) for row in rows)
    description = text.split("---", 2)[1]
    assert all(condition in description for condition in conditions)


@pytest.mark.parametrize("skill", ["search", "secret-files"])
def test_search_skills_are_only_published_from_basics(skill: str) -> None:
    """検索の2スキルはbasicsに実在し、toolkit側に旧公開スキルを残さない。"""
    assert (_SKILLS / skill / "SKILL.md").is_file()
    assert not (_REPO_ROOT / "agent-toolkit/skills" / skill / "SKILL.md").exists()


def test_reading_tables_are_not_split_into_other_files() -> None:
    """読込表を別ファイルへ分けた`*-delivery-*.md`と接続の標識が、配布物とプロジェクトスキルに無い。"""
    roots = ["agent-basics", "agent-toolkit", ".claude/skills"]
    # 追跡済みと未追跡（ignore対象を除く）の両方を、作業ツリーに実在するものに限って調べる。
    listed = [
        name
        for name in subprocess.run(
            ["git", "-C", str(_REPO_ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "--", *roots],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if (_REPO_ROOT / name).is_file()
    ]
    assert listed
    split_files = [name for name in listed if re.search(r"-delivery-\d+\.md$", name)]
    markers = [
        f"{name}:{number}"
        for name in listed
        if name.endswith(".md")
        for number, line in enumerate((_REPO_ROOT / name).read_text(encoding="utf-8").splitlines(), 1)
        if "read-table-source" in line
    ]
    assert not split_files, "\n".join(split_files)
    assert not markers, "\n".join(markers)


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("詳細は`agent-toolkit:delegation`に従う。", "agent-toolkitの名前空間"),
        ("`${CLAUDE_PLUGIN_ROOT}/share/exec.subagent.md`を読む。", "agent-toolkitの共有文書"),
        ("`atk wi show`で取得する。", "atkコマンド"),
        ("全体を`atk-auto`要素で囲む。", "atk-autoの標識"),
        ("`agents_server`の`start`で起動する。", "agent-toolkitのMCPツール"),
    ],
)
def test_toolkit_dependency_is_detected(tmp_path: pathlib.Path, line: str, expected: str) -> None:
    """持ち込まれたagent-toolkit前提の参照を種類ごとに検出する。"""
    path = tmp_path / "SKILL.md"
    path.write_text(f"# 例\n\n{line}\n", encoding="utf-8")
    assert _toolkit_dependencies(path) == [f"{path}:3: {expected}"]


def test_reference_outside_distribution_is_detected(tmp_path: pathlib.Path) -> None:
    """agent-basicsの中に無い役割文書への参照を、参照先の不在として検出する。"""
    skills = tmp_path / "skills"
    (skills / "sample/references").mkdir(parents=True)
    (skills / "sample/references/rule.md").write_text("# 基準\n", encoding="utf-8")
    path = skills / "sample/SKILL.md"
    path.write_text(
        "# 例\n\n`references/rule.md`と`docs/development/audit-records.md`と`AGENTS.md`に従い、"
        "`share/exec.subagent.md`を読む。\n",
        encoding="utf-8",
    )
    assert _unresolved_references(path, skills) == [f"{path}:3: share/exec.subagent.md"]
