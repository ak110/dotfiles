#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
r"""PEP 723スクリプトと`[project.scripts]`のimport解決可能性を検査する。

スクリプトを実行しない静的解析（`ast.parse`）のみで判定し、副作用を起こさない。
対象種別ごとに検査方式を分ける。

- 対象種別1: `scripts/`・`libexec/`・`agent-toolkit/scripts/`
  配下のPEP 723単独実行スクリプト（`*_test.py`を除く）。起点ごとのPEP 723依存と、
  静的に評価できる`sys.path.insert`が示す探索パスを用い、到達する内部モジュールを
  推移走査する。`ImportError`または`ModuleNotFoundError`で保護されたimportは除外する
- 対象種別2: `pyproject.toml`の`[project.scripts]`が参照する`module:function`形式。
  参照先のモジュールファイルが実在し、そのファイルに対象関数の定義（または再エクスポートによる
  束縛）があるかを`ast.parse`で確認する。プロジェクト依存の解決は`--no-project`環境では
  成立しないため対象外とする
- 対象種別3: `agent-toolkit/agent_toolkit/`配下の責務別サブパッケージを再帰走査し、
  層の順序に反する絶対importと、テスト側のモジュール（`*_test.py`・`conftest.py`・`_testing`配下）以外から
  `_testing`へのimportを検出する
- 対象種別4: `agent-toolkit/agent_toolkit/`と`agent-toolkit/skills/*/scripts/`を走査し、
  uvプロジェクトへ集約したPythonからPEP 723宣言が除去されていることを検査する
- 対象種別5: `agent-toolkit/agent_toolkit/`直下の`*.py`（`*_test.py`を除く）が、配布物の外部から
  絶対パスで解決される公開スクリプトと`__init__.py`・`conftest.py`だけであることを検査する。
  対象種別3は責務別サブパッケージだけを走査するため、層に収まらない実装モジュールを直下へ置くと
  層の検査を経ずに配置の規定から外れる
- 対象種別6: `agent-toolkit/agent_toolkit/`と`agent-toolkit/skills/*/scripts/`の非テストのPython
  （`*_test.py`と`conftest.py`を除く）を1回ずつ構文解析し、モジュールの書き方の規則を検査する。
  ファイル単位の`# ruff: noqa`による`F821`（未定義名）の抑止と、モジュールのトップレベルで
  名前空間へ書き込む処理（`vars(...)`・`globals()`への代入・`setdefault`・`update`、
  `sys.modules[...]`の`__class__`の置換）を失敗とする。どちらも使う名前を実行時に別の場所から
  注入する構造を許し、静的解析が名前を解決できなくなるため。行単位の`# noqa: F821`は前方参照など
  特定の1行だけを抑止し、注入への依存を生まないため対象から外す。
  同じ走査で、層の外から起動されるスクリプト（`agent_toolkit/`直下の公開スクリプトと`skills/*/scripts/`配下）のimportも検査する。
  起動スクリプトは全ての層をimportできるが、`_hooks`をimportできるのは`hook.py`だけとし、`_testing`はimportできない。
  hook以外の起動スクリプトが`_hooks`へ依存すると、hookの実装の層にhook以外から使う部品が残るため
  同じ走査で、`_git/`と`_testing/`の外から`subprocess`へ`["git", ...]`を渡して起動する箇所も失敗にする。
  Gitの起動は`_git/command.py`の共通関数へ集め、時間上限・終了コードの扱い・文字コードの指定を1か所で保つため。
  さらに同じ走査で、`agent-toolkit/pyproject.toml`の`[project] dependencies`に無く開発用の依存グループにだけある
  パッケージのimportを失敗にする（`_testing`配下を除く）。開発環境には開発用の依存が導入済みのためテストは成功するが、
  配布先の環境には無く、そのモジュールを読み込んだ時点で失敗するため。`_testing`配下は本番のコードから
  importできないことを対象種別3が保証するため、テスト専用の依存を使ってよい

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
_LAYER_ORDER = ("_common", "_git", "_plan", "_atk", "_agents_server", "_hooks")
# 直下へ置ける名前。公開スクリプトの集合は
# `.claude/skills/agent-toolkit-edit/references/distribution-and-hooks.md`「agent_toolkitパッケージの配置と層」が定める。
_AGENT_TOOLKIT_ROOT_MODULES = frozenset(
    {"hook.py", "atk.py", "agents_server_mcp.py", "wait_ci.py", "_managed_temp.py", "__init__.py", "conftest.py"}
)

# PEP 723インラインメタデータブロックの抽出パターン（公式仕様のリファレンス実装に準拠）。
_PEP723_BLOCK_RE = re.compile(r"(?m)^# /// (?P<type>[A-Za-z0-9-]+)$\s(?P<content>(?:^#(?:| .*)$\s)+)^# ///$")


@dataclasses.dataclass(frozen=True)
class _ImportReference:
    """importが参照するモジュールと、`from`輸入名向けのfallback。"""

    name: str
    fallback: str | None = None
    lineno: int = 0


def _script_directories() -> tuple[pathlib.Path, ...]:
    """PEP 723スクリプトの走査対象ディレクトリをパス昇順で返す。"""
    directories = [
        _REPO_ROOT / "libexec",
        _REPO_ROOT / "scripts",
        _REPO_ROOT / "agent-toolkit/scripts",
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
        self.references.extend(_ImportReference(alias.name, lineno=node.lineno) for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:  # noqa: N802
        if not node.level and node.module:
            self.references.append(_ImportReference(node.module, lineno=node.lineno))
            self.references.extend(
                _ImportReference(f"{node.module}.{alias.name}", fallback=node.module, lineno=node.lineno)
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


def _check_entry_script(entry_path: pathlib.Path, text: str, scripts_root: pathlib.Path | None = None) -> list[str]:
    """1つの起点スクリプトから到達するimportを推移的に検査する。"""
    dependencies = _read_pep723_dependencies(text)
    search_paths = list(dict.fromkeys((entry_path.parent, scripts_root))) if scripts_root is not None else [entry_path.parent]
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
        for script_path in sorted(directory.rglob("*.py")):
            if "__pycache__" in script_path.parts:
                continue
            if script_path.name.endswith("_test.py"):
                continue
            text = script_path.read_text(encoding="utf-8")
            if _is_pep723_script(text):
                problems.extend(_check_entry_script(script_path, text, directory))
    return problems


def _check_agent_toolkit_layers() -> list[str]:
    """agent-toolkitの責務層に反するimportを返す。"""
    scripts_root = _REPO_ROOT / "agent-toolkit/agent_toolkit"
    order = {name: index for index, name in enumerate(_LAYER_ORDER)}
    problems: list[str] = []
    for layer in (*_LAYER_ORDER, "_testing"):
        layer_root = scripts_root / layer
        if not layer_root.is_dir():
            continue
        for source_path in sorted(layer_root.rglob("*.py")):
            if "__pycache__" in source_path.parts:
                continue
            tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
            for reference in _extract_imports(tree):
                components = reference.name.split(".")
                imported_layer = components[1] if components[0] == "agent_toolkit" and len(components) > 1 else components[0]
                if imported_layer == "_testing" and not _is_test_support_module(source_path, scripts_root):
                    problems.append(f"{_display_path(source_path)}: 非テストモジュールから`_testing`をimportしている")
                elif imported_layer in order and layer in order and order[imported_layer] > order[layer]:
                    problems.append(f"{_display_path(source_path)}: 層の順序に反して`{imported_layer}`をimportしている")
    return problems


def _is_test_support_module(source_path: pathlib.Path, scripts_root: pathlib.Path) -> bool:
    """`_testing`をimportできるテスト側のモジュール（`*_test.py`・`conftest.py`・`_testing`配下）かを返す。"""
    if source_path.name.endswith("_test.py") or source_path.name == "conftest.py":
        return True
    return source_path.relative_to(scripts_root).parts[0] == "_testing"


_RUFF_FILE_NOQA_RE = re.compile(r"^\s*#\s*ruff\s*:\s*noqa(?:\s*:\s*(?P<codes>[^#]*))?\s*$")
_NAMESPACE_WRITE_METHODS = frozenset({"setdefault", "update", "__setitem__"})


@dataclasses.dataclass(frozen=True)
class _AgentToolkitSource:
    """書き方の規則を検査する非テストのPythonファイルと、その構文木。"""

    path: pathlib.Path
    text: str
    tree: ast.Module


def _agent_toolkit_sources() -> tuple[list[_AgentToolkitSource], list[str]]:
    """`agent_toolkit/`と`skills/*/scripts/`の非テストのPythonを構文解析し、(対象, 解析の失敗)を返す。"""
    roots = (
        _REPO_ROOT / "agent-toolkit/agent_toolkit",
        *sorted((_REPO_ROOT / "agent-toolkit/skills").glob("*/scripts")),
    )
    sources: list[_AgentToolkitSource] = []
    problems: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path.name.endswith("_test.py") or path.name == "conftest.py":
                continue
            text = path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError as exc:
                problems.append(f"{_display_path(path)}: 構文解析に失敗: {exc}")
                continue
            sources.append(_AgentToolkitSource(path, text, tree))
    return sources, problems


def _file_level_f821_noqa_problems(source: _AgentToolkitSource) -> list[str]:
    """ファイル単位の`# ruff: noqa`が`F821`を抑止している行を返す。コードを列挙しない形も全規則の抑止として扱う。"""
    problems: list[str] = []
    for lineno, line in enumerate(source.text.splitlines(), start=1):
        match = _RUFF_FILE_NOQA_RE.match(line)
        if match is None:
            continue
        codes = match.group("codes")
        if codes is None or "F821" in {code.strip() for code in codes.split(",")}:
            problems.append(
                f"{_display_path(source.path)}:{lineno}: ファイル単位の`# ruff: noqa`が`F821`（未定義名）の検出を抑止している。"
                "次の操作: 使う名前を定義元から明示的にimportし、ファイル単位の抑止から`F821`を除く"
            )
    return problems


def _is_namespace_call(node: ast.expr) -> bool:
    """`vars(...)`または`globals()`の呼び出し式かを返す。"""
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"vars", "globals"}


def _is_sys_modules_subscript(node: ast.expr) -> bool:
    """`sys.modules[...]`の添字式かを返す。"""
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "modules"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "sys"
    )


def _namespace_write_description(node: ast.AST) -> str | None:
    """1つの文の中で名前空間へ書き込む式を探し、見つかれば書き込みの形を返す。"""
    for inner in ast.walk(node):
        if isinstance(inner, ast.Assign | ast.AugAssign | ast.AnnAssign):
            targets = inner.targets if isinstance(inner, ast.Assign) else [inner.target]
            for target in targets:
                if isinstance(target, ast.Subscript) and _is_namespace_call(target.value):
                    return f"`{ast.unparse(target.value)}[...]`への代入"
                if isinstance(target, ast.Attribute) and target.attr == "__class__" and _is_sys_modules_subscript(target.value):
                    return "`sys.modules[...].__class__`の置換"
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in _NAMESPACE_WRITE_METHODS
            and _is_namespace_call(inner.func.value)
        ):
            return f"`{ast.unparse(inner.func.value)}.{inner.func.attr}(...)`"
    return None


def _top_level_statements(body: list[ast.stmt]) -> list[ast.stmt]:
    """関数とクラスの本体を除き、モジュールの読み込み時に実行される文を返す。"""
    statements: list[ast.stmt] = []
    for node in body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            continue
        if isinstance(node, ast.If | ast.For | ast.AsyncFor | ast.While | ast.With | ast.AsyncWith):
            statements.extend(_top_level_statements([*node.body, *getattr(node, "orelse", [])]))
            continue
        if isinstance(node, ast.Try):
            handlers = [statement for handler in node.handlers for statement in handler.body]
            statements.extend(_top_level_statements([*node.body, *handlers, *node.orelse, *node.finalbody]))
            continue
        statements.append(node)
    return statements


def _namespace_write_problems(source: _AgentToolkitSource) -> list[str]:
    """モジュールのトップレベルで名前空間へ書き込む文を返す。読み取り（`vars(...)[...]`の参照）は対象外とする。"""
    problems: list[str] = []
    for statement in _top_level_statements(source.tree.body):
        description = _namespace_write_description(statement)
        if description is not None:
            problems.append(
                f"{_display_path(source.path)}:{statement.lineno}: "
                f"モジュールのトップレベルで名前空間へ書き込んでいる（{description}）。"
                "次の操作: 名前を注入せず、名前を使うモジュールが定義元から明示的にimportする"
            )
    return problems


def _is_entry_source(path: pathlib.Path) -> bool:
    """層の外から起動され全ての層を使うスクリプト（`agent_toolkit/`直下の公開スクリプトと`skills/*/scripts/`配下）かを返す。"""
    if path.parent == _REPO_ROOT / "agent-toolkit/agent_toolkit":
        return path.name in _AGENT_TOOLKIT_ROOT_MODULES
    return path.is_relative_to(_REPO_ROOT / "agent-toolkit/skills")


def _entry_layer_problems(source: _AgentToolkitSource) -> list[str]:
    """起動スクリプトのimportのうち、`hook.py`以外からの`_hooks`と、`_testing`を参照するものを返す。"""
    if not _is_entry_source(source.path):
        return []
    problems: list[str] = []
    for reference in _extract_imports(source.tree):
        components = reference.name.split(".")
        if components[0] != "agent_toolkit" or len(components) < 2:
            continue
        if components[1] == "_hooks" and source.path.name != "hook.py":
            problems.append(
                f"{_display_path(source.path)}: "
                "`_hooks`をimportしている（`_hooks`をimportできる起動スクリプトは`hook.py`だけ）。"
                "次の操作: hook以外からも使う部品を前の層（`_common`など）へ移し、移動先からimportする"
            )
        elif components[1] == "_testing":
            problems.append(f"{_display_path(source.path)}: 非テストモジュールから`_testing`をimportしている")
    return sorted(set(problems))


_SUBPROCESS_LAUNCHERS = frozenset({"run", "Popen", "call", "check_call", "check_output"})


def _git_subprocess_problems(source: _AgentToolkitSource) -> list[str]:
    """`_git/`と`_testing/`の外で`subprocess`へ`["git", ...]`を渡して起動している箇所を返す。"""
    relative = source.path.relative_to(_REPO_ROOT / "agent-toolkit").parts
    if relative[:2] in {("agent_toolkit", "_git"), ("agent_toolkit", "_testing")}:
        return []
    problems: list[str] = []
    for node in ast.walk(source.tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _SUBPROCESS_LAUNCHERS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "subprocess"
            and node.args
            and isinstance(node.args[0], ast.List | ast.Tuple)
            and node.args[0].elts
            and isinstance(node.args[0].elts[0], ast.Constant)
            and node.args[0].elts[0].value == "git"
        ):
            continue
        problems.append(
            f"{_display_path(source.path)}:{node.lineno}: `git`を`subprocess`で直接起動している。"
            "次の操作: `agent_toolkit._git.command`の共通関数（`run`など）で起動する"
        )
    return problems


def _agent_toolkit_dev_only_imports() -> frozenset[str]:
    """`agent-toolkit/pyproject.toml`の開発用の依存グループにだけあるパッケージのimport名を返す。"""
    pyproject = _REPO_ROOT / "agent-toolkit/pyproject.toml"
    if not pyproject.is_file():
        return frozenset()
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    runtime = {_dependency_import_name(dep) for dep in data.get("project", {}).get("dependencies", []) if isinstance(dep, str)}
    development = {
        _dependency_import_name(dep)
        for group in data.get("dependency-groups", {}).values()
        for dep in group
        if isinstance(dep, str)
    }
    return frozenset(development - runtime)


def _dev_dependency_import_problems(source: _AgentToolkitSource, dev_only: frozenset[str]) -> list[str]:
    """`_testing`配下以外の非テストのPythonが開発用の依存だけにあるパッケージをimportしている箇所を返す。"""
    if source.path.is_relative_to(_REPO_ROOT / "agent-toolkit/agent_toolkit/_testing"):
        return []
    problems: list[str] = []
    for reference in _extract_imports(source.tree):
        top_level = reference.name.partition(".")[0]
        if top_level in dev_only and reference.fallback is None:
            problems.append(
                f"{_display_path(source.path)}:{reference.lineno}: "
                f"開発用の依存グループにだけある`{top_level}`をimportしている（配布先の環境には導入されない）。"
                "次の操作: テストだけが使うコードなら`agent-toolkit/agent_toolkit/_testing/`へ移す。"
                "本番のコードが使う場合は`agent-toolkit/pyproject.toml`の`[project] dependencies`へ加える"
            )
    return problems


def _check_agent_toolkit_sources() -> list[str]:
    """`agent_toolkit/`と`skills/*/scripts/`の非テストのPythonを1回ずつ走査し、書き方の規則に反する箇所を返す。"""
    sources, problems = _agent_toolkit_sources()
    dev_only = _agent_toolkit_dev_only_imports()
    for source in sources:
        problems.extend(_file_level_f821_noqa_problems(source))
        problems.extend(_namespace_write_problems(source))
        problems.extend(_entry_layer_problems(source))
        problems.extend(_git_subprocess_problems(source))
        problems.extend(_dev_dependency_import_problems(source, dev_only))
    return problems


def _check_agent_toolkit_root_modules() -> list[str]:
    """`agent-toolkit/agent_toolkit/`直下に置かれた公開スクリプト以外のモジュールを返す。"""
    scripts_root = _REPO_ROOT / "agent-toolkit/agent_toolkit"
    return [
        f"{_display_path(path)}: 公開スクリプト以外のモジュールが直下にある（責務別サブパッケージへ置く）"
        for path in sorted(scripts_root.glob("*.py"))
        if not path.name.endswith("_test.py") and path.name not in _AGENT_TOOLKIT_ROOT_MODULES
    ]


def _check_project_modules_have_no_pep723() -> list[str]:
    """uvプロジェクトで動くPythonモジュールにPEP 723宣言が残っていないことを検査する。"""
    roots = (
        _REPO_ROOT / "agent-toolkit/agent_toolkit",
        *sorted((_REPO_ROOT / "agent-toolkit/skills").glob("*/scripts")),
    )
    problems: list[str] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            if "# /// script" in text:
                problems.append(f"{_display_path(path)}: uvプロジェクト配下にPEP 723宣言が残っている")
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
    problems = (
        _check_script_directories()
        + _check_project_scripts()
        + _check_agent_toolkit_layers()
        + _check_agent_toolkit_root_modules()
        + _check_agent_toolkit_sources()
        + _check_project_modules_have_no_pep723()
    )
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
