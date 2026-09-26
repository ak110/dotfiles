"""plugin root参照の実在を検査する。

検査の対象はエージェントが実行時に読む資源とし、テストコード（`*_test.py`と`conftest.py`）を走査から除く。
テスト入力は実行時に読まれる参照ではなく、架空の参照を書いても配布物は成立するためである。
パスの不在は配布物を成立させないためerrorとして扱う。
"""

import pathlib
import re

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
    return [(relative, source) for relative, source in _collect_references(root) if not (root / relative).exists()]


def _format_unresolved(entries: list[tuple[str, pathlib.Path]]) -> str:
    """未解決参照を参照元と対にして整形する。"""
    return "\n".join(f"{relative} <- {source}" for relative, source in entries)


def test_plugin_root_references_resolve() -> None:
    """配布物のplugin root参照が全て実体へ解決する。"""
    root = pathlib.Path(__file__).resolve().parents[1]
    assert _collect_references(root)
    unresolved = _unresolved_references(root)
    assert not unresolved, _format_unresolved(unresolved)


def test_unresolved_reference_is_reported(tmp_path: pathlib.Path) -> None:
    """欠損参照を相対パスと参照元パスで報告する。"""
    present = tmp_path / "share/present.subagent.md"
    present.parent.mkdir()
    present.write_text("存在する\n", encoding="utf-8")
    source = tmp_path / "references.md"
    source.write_text(f"{_PREFIX}share/present.subagent.md\n{_PREFIX}share/missing.subagent.md\n", encoding="utf-8")

    assert _collect_references(tmp_path) == [
        ("share/present.subagent.md", pathlib.Path("references.md")),
        ("share/missing.subagent.md", pathlib.Path("references.md")),
    ]
    unresolved = _unresolved_references(tmp_path)
    assert unresolved == [("share/missing.subagent.md", pathlib.Path("references.md"))]
    formatted = _format_unresolved(unresolved)
    assert "share/missing.subagent.md" in formatted
    assert "references.md" in formatted


def test_test_files_are_excluded_from_references(tmp_path: pathlib.Path) -> None:
    """テストコードの参照は収集せず、同じ参照を持つ実行時資源の参照は収集する。"""
    missing = f"{_PREFIX}share/missing.subagent.md\n"
    (tmp_path / "hooks").mkdir()
    (tmp_path / "hooks" / "entry_test.py").write_text(f'PROMPT = "{missing.strip()}"\n', encoding="utf-8")
    (tmp_path / "conftest.py").write_text(f'PROMPT = "{missing.strip()}"\n', encoding="utf-8")
    (tmp_path / "hooks" / "entry.py").write_text(f'PROMPT = "{missing.strip()}"\n', encoding="utf-8")
    (tmp_path / "skill.md").write_text(missing, encoding="utf-8")

    assert _collect_references(tmp_path) == [
        ("share/missing.subagent.md", pathlib.Path("hooks/entry.py")),
        ("share/missing.subagent.md", pathlib.Path("skill.md")),
    ]
