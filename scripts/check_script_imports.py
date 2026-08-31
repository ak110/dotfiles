#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""PEP 723スクリプトと`[project.scripts]`のimport解決可能性を検査する。

スクリプトを実行しない静的解析（`ast.parse`）のみで判定し、副作用を起こさない。
対象種別ごとに検査方式を分ける。

- 対象種別1: `scripts/`・`agent-toolkit/scripts/`・`agent-toolkit/skills/*/scripts/`
  直下のPEP 723単独実行スクリプト（`*_test.py`を除く）。起点ごとのPEP 723依存と、
  静的に評価できる`sys.path.insert`が示す探索パスを用い、到達する内部モジュールを
  推移走査する。`ImportError`または`ModuleNotFoundError`で保護されたimportは除外する
- 対象種別2: `pyproject.toml`の`[project.scripts]`が参照する`module:function`形式。
  参照先モジュールファイルが実在するか、当該ファイル内に対象関数の定義（または再エクスポートによる
  束縛）が存在するかを`ast.parse`で確認する。プロジェクト依存の解決は`--no-project`環境では
  成立しないため対象外とする

スクリプトをimportまたは実行する方式は採らない。生成処理・ファイル書き込みなどの副作用を
実行し得るうえ、`--help`への対応も保証されていないため。

対象一覧は対象ディレクトリの走査と`pyproject.toml`から機械的に取得し、本スクリプト側に
重複した一覧を持たない。

注記: `agent-toolkit/hooks/hooks.json`の登録コマンドは`hook.py`単一エントリポイント
経由のため、hookスクリプト個別名を登録定義から機械取得できない。「自動処理の登録定義から
一覧を機械取得する」設計は現構造では成立しないため、対象ディレクトリの走査で代替する。

error・warning区分: 本スクリプトが報告する全項目は`error`区分（exit code 1）とする。
importが解決できない状態は起動時に確実に失敗する致命的な問題であり、警告に留める余地が無いため。
"""

from __future__ import annotations

import ast
import dataclasses
import pathlib
import re
import sys
import tomllib

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"

_SHEBANG = "#!/usr/bin/env -S uv run --script"
_DEPENDENCY_IMPORT_NAMES = {"markdown-it-py": "markdown_it", "pyyaml": "yaml"}

# PEP 723インラインメタデータブロックの抽出パターン（公式仕様のリファレンス実装に準拠）。
_PEP723_BLOCK_RE = re.compile(r"(?m)^# /// (?P<type>[A-Za-z0-9-]+)$\s(?P<content>(?:^#(?:| .*)$\s)+)^# ///$")


@dataclasses.dataclass(frozen=True)
class _ImportReference:
    """importが参照するモジュールと、`from`輸入名向けのfallback。"""

    name: str
    fallback: str | None = None


def _script_directories() -> tuple[pathlib.Path, ...]:
    """PEP 723スクリプトの走査対象ディレクトリをパス昇順で返す。"""
    directories = [
        _REPO_ROOT / "scripts",
        _REPO_ROOT / "agent-toolkit/scripts",
        *sorted((_REPO_ROOT / "agent-toolkit/skills").glob("*/scripts")),
    ]
    return tuple(path for path in directories if path.is_dir())


def _is_pep723_script(text: str) -> bool:
    """本文の先頭行がPEP 723単独実行スクリプトのshebangかを判定する。"""
    first_line = text.splitlines()[0] if text else ""
    return first_line.startswith(_SHEBANG)


def _dependency_import_name(requirement: str) -> str:
    """依存指定文字列（`requests>=2`等）からトップレベルimport名を抽出する。"""
    distribution = re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip().lower()
    return _DEPENDENCY_IMPORT_NAMES.get(distribution, distribution.replace("-", "_"))


def _read_pep723_dependencies(text: str) -> set[str]:
    """PEP 723インラインメタデータの`dependencies`が示すimport名集合を返す。"""
    for match in _PEP723_BLOCK_RE.finditer(text):
        if match.group("type") != "script":
            continue
        content = "".join(
            line[2:] if line.startswith("# ") else line[1:] for line in match.group("content").splitlines(keepends=True)
        )
        metadata = tomllib.loads(content)
        deps = metadata.get("dependencies", [])
        return {_dependency_import_name(dep) for dep in deps if isinstance(dep, str)}
    return set()


def _catches_optional_import(handler: ast.ExceptHandler) -> bool:
    """例外ハンドラーが任意import用の例外を捕捉するかを返す。"""
    exception = handler.type
    if isinstance(exception, ast.Tuple):
        names = tuple(exception.elts)
    elif exception is not None:
        names = (exception,)
    else:
        names = ()
    return any(isinstance(name, ast.Name) and name.id in {"ImportError", "ModuleNotFoundError"} for name in names)


class _ImportVisitor(ast.NodeVisitor):
    """保護されたimportを除き、ドット付きモジュール参照を収集する。"""

    def __init__(self) -> None:
        self.references: list[_ImportReference] = []

    def visit_Import(self, node: ast.Import) -> None:  # noqa: N802
        self.references.extend(_ImportReference(alias.name) for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if not node.level and node.module:
            self.references.append(_ImportReference(node.module))
            self.references.extend(
                _ImportReference(f"{node.module}.{alias.name}", fallback=node.module)
                for alias in node.names
                if alias.name != "*"
            )

    def visit_Try(self, node: ast.Try) -> None:  # noqa: N802
        if not any(_catches_optional_import(handler) for handler in node.handlers):
            for statement in node.body:
                self.visit(statement)
        for handler in node.handlers:
            self.visit(handler)
        for statement in (*node.orelse, *node.finalbody):
            self.visit(statement)


def _extract_imports(tree: ast.Module) -> tuple[_ImportReference, ...]:
    """構文木から判定対象のimport参照を抽出する。"""
    visitor = _ImportVisitor()
    visitor.visit(tree)
    return tuple(visitor.references)


def _top_level_assignments(tree: ast.Module) -> dict[str, ast.expr]:
    """モジュール直下の単純名への代入式を返す。"""
    assignments: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    assignments[target.id] = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            assignments[node.target.id] = node.value
    return assignments


def _evaluate_path(
    expression: ast.expr,
    *,
    source_path: pathlib.Path,
    assignments: dict[str, ast.expr],
    evaluating: frozenset[str] = frozenset(),
) -> pathlib.Path | None:
    """許可された静的なPath式を評価する。"""
    if isinstance(expression, ast.Name) and expression.id in assignments and expression.id not in evaluating:
        return _evaluate_path(
            assignments[expression.id],
            source_path=source_path,
            assignments=assignments,
            evaluating=evaluating | {expression.id},
        )
    if isinstance(expression, ast.Call) and len(expression.args) == 1 and not expression.keywords:
        if isinstance(expression.func, ast.Name) and expression.func.id == "str":
            return _evaluate_path(expression.args[0], source_path=source_path, assignments=assignments, evaluating=evaluating)
        is_path = isinstance(expression.func, ast.Name) and expression.func.id == "Path"
        is_pathlib_path = (
            isinstance(expression.func, ast.Attribute)
            and isinstance(expression.func.value, ast.Name)
            and expression.func.value.id == "pathlib"
            and expression.func.attr == "Path"
        )
        if (is_path or is_pathlib_path) and isinstance(expression.args[0], ast.Name) and expression.args[0].id == "__file__":
            return source_path
    if (
        isinstance(expression, ast.Call)
        and not expression.args
        and not expression.keywords
        and isinstance(expression.func, ast.Attribute)
        and expression.func.attr == "resolve"
    ):
        value = _evaluate_path(expression.func.value, source_path=source_path, assignments=assignments, evaluating=evaluating)
        return value.resolve() if value is not None else None
    if isinstance(expression, ast.Attribute) and expression.attr == "parent":
        value = _evaluate_path(expression.value, source_path=source_path, assignments=assignments, evaluating=evaluating)
        return value.parent if value is not None else None
    if (
        isinstance(expression, ast.Subscript)
        and isinstance(expression.value, ast.Attribute)
        and expression.value.attr == "parents"
        and isinstance(expression.slice, ast.Constant)
        and isinstance(expression.slice.value, int)
    ):
        value = _evaluate_path(expression.value.value, source_path=source_path, assignments=assignments, evaluating=evaluating)
        if value is None or expression.slice.value < 0:
            return None
        try:
            return value.parents[expression.slice.value]
        except IndexError:
            return None
    if (
        isinstance(expression, ast.BinOp)
        and isinstance(expression.op, ast.Div)
        and isinstance(expression.right, ast.Constant)
        and isinstance(expression.right.value, str)
    ):
        value = _evaluate_path(expression.left, source_path=source_path, assignments=assignments, evaluating=evaluating)
        return value / expression.right.value if value is not None else None
    return None


def _inserted_search_paths(tree: ast.Module, source_path: pathlib.Path) -> tuple[pathlib.Path, ...]:
    """静的に評価できる`sys.path.insert`の追加先を返す。"""
    assignments = _top_level_assignments(tree)
    paths: list[pathlib.Path] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and len(node.args) >= 2
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
        ):
            continue
        path = _evaluate_path(node.args[1], source_path=source_path, assignments=assignments)
        if path is not None and path not in paths:
            paths.append(path)
    return tuple(paths)


def _resolve_internal_module(name: str, search_paths: list[pathlib.Path]) -> tuple[pathlib.Path, ...] | None:
    """探索パス上のモジュールと実在する中間`__init__.py`を返す。"""
    parts = name.split(".")
    for search_path in search_paths:
        base = search_path.joinpath(*parts)
        candidates = (base.with_suffix(".py"), base / "__init__.py")
        module_path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if module_path is None:
            continue
        package_initializers = tuple(
            initializer
            for index in range(1, len(parts) + 1)
            if (initializer := search_path.joinpath(*parts[:index], "__init__.py")).is_file()
        )
        return tuple(dict.fromkeys((*package_initializers, module_path)))
    return None


def _resolved_files(reference: _ImportReference, search_paths: list[pathlib.Path]) -> tuple[pathlib.Path, ...] | None:
    """import参照を内部ファイルへ解決し、`from`輸入名では親モジュールへfallbackする。"""
    resolved = _resolve_internal_module(reference.name, search_paths)
    if resolved is None and reference.fallback is not None:
        resolved = _resolve_internal_module(reference.fallback, search_paths)
    return resolved


def _display_path(path: pathlib.Path) -> pathlib.Path:
    """リポジトリ配下なら相対パス、それ以外なら絶対パスを返す。"""
    try:
        return path.relative_to(_REPO_ROOT)
    except ValueError:
        return path


def _problem(entry_path: pathlib.Path, source_path: pathlib.Path, detail: str) -> str:
    """起点と検出元を含む問題文を組み立てる。"""
    entry = _display_path(entry_path)
    if source_path == entry_path:
        return f"{entry}: {detail}"
    return f"{entry}: {_display_path(source_path)}経由で{detail}"


def _check_entry_script(entry_path: pathlib.Path, text: str) -> list[str]:
    """1つの起点スクリプトから到達するimportを推移的に検査する。"""
    dependencies = _read_pep723_dependencies(text)
    search_paths = [entry_path.parent]
    sources = {entry_path: text}
    imports: dict[pathlib.Path, tuple[_ImportReference, ...]] = {}
    queued = [entry_path]
    scheduled = {entry_path}
    problems: list[str] = []

    while queued:
        source_path = queued.pop(0)
        source = sources.pop(source_path, None)
        if source is None:
            source = source_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(source_path))
        except SyntaxError as exc:
            problems.append(_problem(entry_path, source_path, f"構文解析に失敗: {exc}"))
            continue
        imports[source_path] = _extract_imports(tree)
        for path in _inserted_search_paths(tree, source_path):
            if path not in search_paths:
                search_paths.append(path)

        for references in imports.values():
            for reference in references:
                top_level = reference.name.partition(".")[0]
                if top_level in sys.stdlib_module_names or top_level in dependencies:
                    continue
                resolved = _resolved_files(reference, search_paths)
                if resolved is None:
                    continue
                for module_path in resolved:
                    if module_path not in scheduled:
                        scheduled.add(module_path)
                        queued.append(module_path)

    unresolved: set[tuple[pathlib.Path, str]] = set()
    for source_path, references in imports.items():
        for reference in references:
            top_level = reference.name.partition(".")[0]
            if top_level in sys.stdlib_module_names or top_level in dependencies:
                continue
            if _resolved_files(reference, search_paths) is None:
                unresolved.add((source_path, top_level))
    problems.extend(
        _problem(entry_path, source_path, f"解決不能なimport `{top_level}`")
        for source_path, top_level in sorted(unresolved, key=lambda item: (str(item[0]), item[1]))
    )
    return problems


def _check_script_directories() -> list[str]:
    """全対象ディレクトリのPEP 723スクリプトを検査する。"""
    problems: list[str] = []
    for directory in _script_directories():
        for script_path in sorted(directory.glob("*.py")):
            if script_path.name.endswith("_test.py"):
                continue
            text = script_path.read_text(encoding="utf-8")
            if _is_pep723_script(text):
                problems.extend(_check_entry_script(script_path, text))
    return problems


def _resolve_module_file(module: str) -> pathlib.Path | None:
    """`pytools.foo`形式のモジュール名から実体ファイルを返す（単一ファイル・サブパッケージ双方に対応）。"""
    base = _REPO_ROOT / pathlib.Path(*module.split("."))
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _top_level_bound_names(tree: ast.Module) -> set[str]:
    """モジュールのトップレベルで束縛される名前一覧を返す（定義・代入・importの再エクスポートを含む）。"""
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Import):
            names.update((alias.asname or alias.name).split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _check_project_scripts() -> list[str]:
    """`pyproject.toml`の`[project.scripts]`が参照するモジュール・関数の実在を検査する。"""
    problems: list[str] = []
    if not _PYPROJECT_PATH.is_file():
        return problems
    data = tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))
    scripts = data.get("project", {}).get("scripts", {})
    for name, target in sorted(scripts.items()):
        if not isinstance(target, str) or ":" not in target:
            problems.append(f"[project.scripts] {name}: `module:function`形式でない: {target}")
            continue
        module, _sep, function = target.partition(":")
        module_file = _resolve_module_file(module)
        if module_file is None:
            problems.append(f"[project.scripts] {name}: モジュールファイルが実在しない: {module}")
            continue
        try:
            tree = ast.parse(module_file.read_text(encoding="utf-8"), filename=str(module_file))
        except SyntaxError as exc:
            problems.append(f"[project.scripts] {name}: {module_file}の構文解析に失敗: {exc}")
            continue
        if function not in _top_level_bound_names(tree):
            problems.append(f"[project.scripts] {name}: {module_file}に`{function}`の定義が見当たらない")
    return problems


def main() -> int:
    """PEP 723スクリプトと`[project.scripts]`のimport解決可能性を検査する。"""
    problems = _check_script_directories() + _check_project_scripts()
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
