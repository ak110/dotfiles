"""直接importと探索候補を区別し、根拠付きの利用試験範囲を照合する。"""

import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
from typing import Any

from agent_toolkit._git import command as git_command

from pytools._internal.common import format_cli_error


def consumer_tests(root: pathlib.Path, files: list[str]) -> dict[str, Any]:
    """直接importは必須、文字列参照と同接頭辞は意味判定の候補にする。

    中間importや動的・設定契約は担当の探索と意味判定で補う。
    静的importで確定できる範囲と、命名・本文から得た探索候補を分け、網羅性は保証しない。
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
    candidates: list[dict[str, Any]] = []
    for path in sorted(paths):
        if not (path.stem.endswith("_test") or path.name.startswith("test_")):
            continue
        body = path.read_text(encoding="utf-8")
        if not changed:
            continue
        tree = ast.parse(body, filename=str(path))
        imports = _imported_modules(tree, path)
        for source in sorted(changed):
            module = _module_name(source)
            relative_parts = source.relative_to(root).with_suffix("").parts
            namespace_module = ".".join(relative_parts) if all(part.isidentifier() for part in relative_parts) else module
            direct = module in imports or namespace_module in imports
            named = path.parent == source.parent and path.stem.startswith(source.stem + "_")
            reasons = []
            if direct:
                reasons.append("direct-import")
            if named:
                reasons.append("test-name")
            if re.search(rf"(?<!\w){re.escape(source.stem)}(?!\w)", body):
                reasons.append("text-reference")
            if source.relative_to(root).as_posix() in body:
                reasons.append("path-reference")
            if reasons:
                candidates.append(
                    {
                        "source": source.relative_to(root).as_posix(),
                        "test": path.relative_to(root).as_posix(),
                        "required": direct,
                        "reasons": reasons,
                    }
                )
    head = git_command.run(["-C", str(root), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, timeout=60)
    if head.returncode:
        raise ValueError(f"HEADを取得できない: {format_cli_error(head)}")
    return {
        "head": head.stdout.strip(),
        "sources": sorted(source.relative_to(root).as_posix() for source in changed),
        "candidates": candidates,
        "source_hashes": {
            source.relative_to(root).as_posix(): hashlib.sha256(source.read_bytes()).hexdigest() for source in sorted(changed)
        },
        "test_hashes": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted({item["test"] for item in candidates})
        },
    }


def _module_name(path: pathlib.Path) -> str:
    """連続するpackageの外側をimport rootとし、同名の別packageを区別する。"""
    parts = [] if path.stem == "__init__" else [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    return ".".join(parts)


def _imported_modules(tree: ast.AST, path: pathlib.Path) -> set[str]:
    """別名はimport対象を変えない。相対importは試験のpackageから解決する。"""
    modules: set[str] = set()
    package = _module_name(path).split(".")[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = package[: len(package) - node.level + 1] if node.level else []
            base = ".".join([*prefix, *([node.module] if node.module else [])])
            if base:
                modules.add(base)
            modules.update(".".join(filter(None, (base, alias.name))) for alias in node.names)
    return modules


def required_tests(snapshot: dict[str, Any], review: object = None) -> list[str]:
    """曖昧候補の判断を現行スナップショットへ照合し、確定必須範囲を返す。"""
    candidates = snapshot["candidates"]
    assert isinstance(candidates, list)
    ambiguous = {(item["source"], item["test"]) for item in candidates if not item["required"]}
    decisions: dict[tuple[str, str], bool] = {}
    if review is not None:
        if not isinstance(review, dict) or any(
            json.dumps(review.get(key), sort_keys=True) != json.dumps(value, sort_keys=True) for key, value in snapshot.items()
        ):
            raise ValueError("利用試験の意味判定が現在のHEAD・ソース・候補集合と一致しない")
        rows = review.get("decisions")
        if not isinstance(rows, list):
            raise ValueError("意味判定decisionsは配列で指定する")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("source"), str) or not isinstance(row.get("test"), str):
                raise ValueError("意味判定のsourceとtestは文字列で指定する")
            key = (row["source"], row["test"])
            if key in decisions or key not in ambiguous:
                raise ValueError("意味判定に重複または余剰の候補がある")
            if not isinstance(row.get("include"), bool) or not isinstance(row.get("reason"), str) or not row["reason"].strip():
                raise ValueError("意味判定にはincludeの真偽値と非空のreasonが必要")
            decisions[key] = row["include"]
    if decisions.keys() != ambiguous:
        raise ValueError("利用試験の曖昧候補に意味判定が不足している。候補JSONへ根拠付きdecisionsを記録する")
    return sorted(
        {item["test"] for item in candidates if item["required"] or decisions.get((item["source"], item["test"]), False)}
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
