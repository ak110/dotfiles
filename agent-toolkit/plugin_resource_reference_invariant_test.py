"""plugin rootから参照する資源が実在することを確かめる。

確認する対象はエージェントが実行時に読む資源とし、テストコード（`*_test.py`と`conftest.py`）を走査から除く。
テスト入力は実行時に読まれず、架空の参照を書いても配布物は成立するためである。
パスの不在は配布物を成立させないためerrorとして扱う。
"""

import pathlib
import re

import pytest

_PREFIX = "${CLAUDE_PLUGIN_ROOT}/"
_REFERENCE_PATTERN = re.compile(r"\$\{CLAUDE_PLUGIN_ROOT\}/[A-Za-z0-9_./-]*[A-Za-z0-9_/]")


def _collect_references(root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """root配下からplugin root相対参照を出現順に収集する。"""
    references: list[tuple[str, pathlib.Path]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".json", ".py"} or _is_test_code(path):
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        references.extend(
            (match.group().removeprefix(_PREFIX), path.relative_to(root)) for match in _REFERENCE_PATTERN.finditer(content)
        )
    return references


def _is_test_code(path: pathlib.Path) -> bool:
    """テストコードのファイルかを判定する。"""
    return path.name.endswith("_test.py") or path.name == "conftest.py"


def _unresolved_references(root: pathlib.Path) -> list[tuple[str, pathlib.Path]]:
    """配布物内の実体へ解決できない参照を返す。"""
    root = root.resolve()
    return [(relative, source) for relative, source in _collect_references(root) if _resource_problem(root, relative)]


def _resource_problem(root: pathlib.Path, relative: str) -> str:
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        return "配布rootの外を指す。配布物に含まれるパスへ置き換える"
    if not target.exists():
        return "参照先が存在しない。配布物の現行パスへ置き換える"
    return ""


def _format_unresolved(root: pathlib.Path, entries: list[tuple[str, pathlib.Path]]) -> str:
    """参照元、対象パスと解決しない理由を並べる。"""
    return "\n".join(f"{source}: {_PREFIX}{relative}: {_resource_problem(root, relative)}" for relative, source in entries)


@pytest.mark.cpu_limit(
    4.25,
    reason=("配布資源全体のmd/json/pyを2回走査し、実際の全参照と欠落を照合する（Linux CI CPU合計最大1.973秒）。"),
)
def test_plugin_root_references_resolve() -> None:
    """配布物のplugin root参照が全て実体へ解決する。"""
    root = pathlib.Path(__file__).resolve().parent
    assert _collect_references(root)
    unresolved = _unresolved_references(root)
    assert not unresolved, _format_unresolved(root, unresolved)


def test_unresolved_reference_is_reported(tmp_path: pathlib.Path) -> None:
    """欠損参照を相対パスと参照元パスで報告する。"""
    present = tmp_path / "skills/atk-mode/roles/present.subagent.md"
    present.parent.mkdir(parents=True)
    present.write_text("存在する\n", encoding="utf-8")
    source = tmp_path / "references.md"
    source.write_text(
        f"{_PREFIX}skills/atk-mode/roles/present.subagent.md\n{_PREFIX}skills/atk-mode/roles/missing.subagent.md\n",
        encoding="utf-8",
    )

    assert _collect_references(tmp_path) == [
        ("skills/atk-mode/roles/present.subagent.md", pathlib.Path("references.md")),
        ("skills/atk-mode/roles/missing.subagent.md", pathlib.Path("references.md")),
    ]
    unresolved = _unresolved_references(tmp_path)
    assert unresolved == [("skills/atk-mode/roles/missing.subagent.md", pathlib.Path("references.md"))]
    formatted = _format_unresolved(tmp_path, unresolved)
    assert "skills/atk-mode/roles/missing.subagent.md" in formatted
    assert "references.md" in formatted
    assert "参照先が存在しない" in formatted


def test_existing_resource_outside_plugin_is_rejected(tmp_path: pathlib.Path) -> None:
    root = tmp_path / "plugin"
    root.mkdir()
    (root / "present.md").write_text("# 入口\n", encoding="utf-8")
    (tmp_path / "outside.md").write_text("# 外部\n", encoding="utf-8")
    (root / "references.md").write_text(f"`{_PREFIX}present.md`と`{_PREFIX}../outside.md`を読む。\n", encoding="utf-8")
    unresolved = _unresolved_references(root)
    assert unresolved == [("../outside.md", pathlib.Path("references.md"))]
    assert "配布rootの外を指す" in _format_unresolved(root, unresolved)


def test_test_files_are_excluded_from_references(tmp_path: pathlib.Path) -> None:
    """テストコードの参照は収集せず、同じ参照を持つ実行時資源の参照は収集する。"""
    missing = f"{_PREFIX}skills/atk-mode/roles/missing.subagent.md\n"
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "entry_test.py").write_text(f'PROMPT = "{missing.strip()}"\n', encoding="utf-8")
    (tmp_path / "conftest.py").write_text(f'PROMPT = "{missing.strip()}"\n', encoding="utf-8")
    (tmp_path / "hooks" / "entry.py").write_text(f'PROMPT = "{missing.strip()}"\n', encoding="utf-8")
    (tmp_path / "skill.md").write_text(missing, encoding="utf-8")

    assert _collect_references(tmp_path) == [
        ("skills/atk-mode/roles/missing.subagent.md", pathlib.Path("hooks/entry.py")),
        ("skills/atk-mode/roles/missing.subagent.md", pathlib.Path("skill.md")),
    ]
