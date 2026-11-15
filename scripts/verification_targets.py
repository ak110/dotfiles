"""import参照と命名による利用試験候補を列挙する。意味上の網羅性は保証しない。"""

import os
import pathlib
import re
import subprocess

from agent_toolkit._git import command as git_command

from pytools._internal.common import format_cli_error


def consumer_tests(root: pathlib.Path, files: list[str]) -> list[str]:
    """追跡試験のうち変更モジュールを直接参照するものと同接頭辞のものを候補にする。

    中間importや動的・設定契約は担当の探索で補う。自動依存解析の維持費用を避けるため、
    モジュールのimport文と親パッケージ付きの参照だけを補助し、意味上の網羅性は保証しない。
    stemの部分一致は`cli`などの短い名前で無関係な試験を大量に拾うため使わない。
    """
    root = root.resolve()
    changed: set[pathlib.Path] = set()
    for name in files:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"リポジトリ内のファイルでない: {name}")
        if path.suffix == ".py" and not path.stem.endswith("_test") and not path.name.startswith("test_"):
            changed.add(path)
    listed = git_command.run(["-C", str(root), "ls-files", "-z", "--", "*.py"], capture_output=True, timeout=60)
    if listed.returncode:
        diagnostic = subprocess.CompletedProcess(
            listed.args, listed.returncode, os.fsdecode(listed.stdout), os.fsdecode(listed.stderr)
        )
        raise ValueError(f"追跡ファイルを取得できない: {format_cli_error(diagnostic)}")
    paths = {(root / os.fsdecode(name)).resolve() for name in listed.stdout.split(b"\0") if name}
    if any(not path.is_relative_to(root) for path in paths):
        raise ValueError("追跡ファイルがリポジトリ外を参照している")
    patterns = [_reference_pattern(root, source) for source in changed]
    affected = {
        path
        for path in paths
        if (path.stem.endswith("_test") or path.name.startswith("test_"))
        and any(pattern.search(path.read_text(encoding="utf-8")) for pattern in patterns)
    }
    return sorted(
        path.relative_to(root).as_posix()
        for path in paths
        if (path.stem.endswith("_test") or path.name.startswith("test_"))
        and (
            path in affected
            or any(path.parent == source.parent and path.stem.startswith(source.stem + "_") for source in changed)
        )
    )


def _reference_pattern(root: pathlib.Path, source: pathlib.Path) -> re.Pattern[str]:
    """`import x`・`from x import`・`from <親> import x`・`<親>.x`の参照に一致する。"""
    stem = re.escape(source.stem)
    alternatives = [rf"^\s*import\s+{stem}\b", rf"^\s*from\s+{stem}\s+import\b"]
    if source.parent != root:
        parent = re.escape(source.parent.name)
        alternatives += [rf"\b{parent}\.{stem}\b", rf"\bfrom\s+[\w.]*\b{parent}\s+import\s+[\w\s,()]*\b{stem}\b"]
    return re.compile("|".join(alternatives), re.MULTILINE)


def uncovered_tests(root: pathlib.Path, tests: list[str], targets: list[str]) -> list[str]:
    """対象省略は全体、ディレクトリ指定は配下を被覆する。"""
    if not targets:
        return []
    root = root.resolve()
    paths = [(root / target).resolve() for target in targets]
    if any(not path.is_relative_to(root) for path in paths):
        raise ValueError("検証対象がリポジトリ外を参照している")
    return [
        test
        for test in tests
        if not any(root / test == path or (path.is_dir() and (root / test).is_relative_to(path)) for path in paths)
    ]
