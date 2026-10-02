"""定義済みの名前の一覧（`defined-names.md`）の各行が、定義元の文書と同期していることを検査する。

一覧は名前から定義元へ至る索引であり、定義そのものは定義元だけに置く。
定義元の改名・移動や名前の改名で一覧だけが古いまま残ると、名前を引いた書き手が存在しない定義を探すか、
撤去した名前を使い続ける。一覧の各行について、定義元のファイルが実在し、そのファイルに名前が現れることを確かめる。
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.repo_invariant

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_LIST_PATH = pathlib.PurePosixPath("agent-toolkit/skills/writing-standards/references/defined-names.md")
_LIST_HEADING = "## 一覧"
_HEADER = ("名前", "指示対象", "定義元")
_SOURCE_PATH_PATTERN = re.compile(r"`([^`]+)`")


def _rows(text: str) -> list[tuple[str, str, str]]:
    """一覧の節の表から、名前・指示対象・定義元の3列の行を返す。"""
    section = text.split(f"\n{_LIST_HEADING}\n", maxsplit=1)[1].split("\n## ", maxsplit=1)[0]
    rows: list[tuple[str, str, str]] = []
    for line in section.splitlines():
        if not line.startswith("| "):
            continue
        cells = tuple(cell.strip() for cell in line.strip().strip("|").split(" | "))
        if cells == _HEADER or set(line.replace("|", "").strip()) <= {"-", " "}:
            continue
        assert len(cells) == 3, f"一覧の行は3列で書く: {line}"
        rows.append((cells[0], cells[1], cells[2]))
    return rows


def _find_violations(repo_root: pathlib.Path) -> list[str]:
    """定義元が無い行と、定義元に名前が現れない行を返す。"""
    text = (repo_root / _LIST_PATH).read_text(encoding="utf-8")
    violations: list[str] = []
    rows = _rows(text)
    if not rows:
        return [f"{_LIST_PATH}: 「{_LIST_HEADING}」の表に行が無い"]
    for name, _target, source in rows:
        match = _SOURCE_PATH_PATTERN.search(source)
        if match is None:
            violations.append(f"{name}: 定義元の列の先頭にリポジトリ相対パスが無い: {source}")
            continue
        source_path = repo_root / match.group(1)
        if not source_path.is_file():
            violations.append(f"{name}: 定義元のファイルが無い: {match.group(1)}")
            continue
        if name not in source_path.read_text(encoding="utf-8"):
            violations.append(f"{name}: 定義元に名前が現れない: {match.group(1)}")
    return violations


def test_defined_names_appear_in_their_sources() -> None:
    """一覧の各行の名前が、その行が示す定義元のファイルに現れる。"""
    violations = _find_violations(_ROOT)
    assert not violations, "\n".join(violations)


def test_reports_missing_source_and_absent_name(tmp_path: pathlib.Path) -> None:
    """定義元のファイルが無い行と、定義元に名前が現れない行を名前つきで報告する。"""
    (tmp_path / _LIST_PATH).parent.mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/source.md").write_text("計画担当は計画工程を担う主体を指す。\n", encoding="utf-8")
    (tmp_path / _LIST_PATH).write_text(
        "# 一覧\n\n## 一覧\n\n"
        "| 名前 | 指示対象 | 定義元 |\n| --- | --- | --- |\n"
        "| 計画担当 | 計画工程を担う主体 | `docs/source.md`冒頭 |\n"
        "| 実装担当 | 実行工程を担う主体 | `docs/source.md`冒頭 |\n"
        "| 全体検証 | 全体の検証 | `docs/missing.md` |\n",
        encoding="utf-8",
    )

    assert _find_violations(tmp_path) == [
        "実装担当: 定義元に名前が現れない: docs/source.md",
        "全体検証: 定義元のファイルが無い: docs/missing.md",
    ]
