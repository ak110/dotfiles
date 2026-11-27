import ast
import os
import pathlib
import re
import subprocess
import symtable
from collections import Counter
from collections.abc import Iterable, Iterator
from functools import cached_property
from importlib.util import resolve_name

from agent_toolkit._git import command as git_command

from pytools._internal.common import format_cli_error

_PLUGIN = "agent-toolkit"
_RUN_SCRIPT = "agent_toolkit._atk.run_script"
_REGISTRY = f"{_PLUGIN}/agent_toolkit/_atk/run_script.py"
_FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef)
_SCOPES = (*_FUNCTIONS, ast.ClassDef, ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)


def _names(target: ast.AST) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, ast.Tuple | ast.List):
        for element in target.elts:
            yield from _names(element)
    elif isinstance(target, ast.Starred):
        yield from _names(target.value)


def _scope_nodes(body: Iterable[ast.AST]) -> Iterator[ast.AST]:
    pending = list(body)
    while pending:
        node = pending.pop()
        yield node
        if not isinstance(node, _SCOPES):
            pending.extend(ast.iter_child_nodes(node))


def _bindings(node: ast.AST) -> Iterator[str]:
    if isinstance(node, ast.Name):
        if not isinstance(node.ctx, ast.Load):
            yield node.id
    elif isinstance(node, ast.Import | ast.ImportFrom):
        yield from (alias.asname or alias.name.partition(".")[0] for alias in node.names)
    elif isinstance(node, (*_FUNCTIONS, ast.ClassDef, ast.ExceptHandler, ast.MatchAs, ast.MatchStar)):
        if node.name is not None:
            yield node.name
    elif isinstance(node, ast.MatchMapping) and node.rest:
        yield node.rest
    elif isinstance(node, ast.Global | ast.Nonlocal):
        yield from node.names


def _binding_counts(nodes: Iterable[ast.AST]) -> Counter[str]:
    return Counter(name for node in nodes for name in _bindings(node))


def _imports(body: Iterable[ast.stmt], package: str) -> Iterator[tuple[str, str]]:
    for node in body:
        if isinstance(node, ast.Import):
            yield from (
                (alias.asname or alias.name.partition(".")[0], alias.name if alias.asname else alias.name.partition(".")[0])
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                try:
                    module = resolve_name("." * node.level + module, package)
                except ImportError:
                    continue
            yield from ((alias.asname or alias.name, f"{module}.{alias.name}") for alias in node.names if alias.name != "*")


def _qualified(node: ast.expr, imports: dict[str, str]) -> str:
    if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
        return imports.get(node.id, "")
    if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and (prefix := _qualified(node.value, imports)):
        return f"{prefix}.{node.attr}"
    return ""


def _dispatch_argument(node: ast.Call, imports: dict[str, str]) -> ast.expr | None:
    if _qualified(node.func, imports) != f"{_RUN_SCRIPT}.dispatch":
        return None
    if len(node.args) == 1 and not node.keywords:
        return node.args[0]
    if not node.args and len(node.keywords) == 1 and node.keywords[0].arg == "args":
        return node.keywords[0].value
    return None


def _namespace_script(node: ast.expr, imports: dict[str, str]) -> str | None:
    if not isinstance(node, ast.Call) or _qualified(node.func, imports) != "argparse.Namespace":
        return None
    for keyword in node.keywords:
        if keyword.arg == "script_name" and isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            return keyword.value.value
    return None


def _local_dispatches(
    body: list[ast.stmt], nodes: list[ast.AST], imports: dict[str, str], bound: Counter[str], arguments: list[ast.expr]
) -> Iterator[str]:
    local_arguments = [arg for arg in arguments if isinstance(arg, ast.Name)]
    if not local_arguments:
        return
    local_names = {arg.id for arg in local_arguments}
    # script_argsの代入はコマンド名を変えない。他の利用では書き換えや別名への流出を否定できない。 pyfltr: allow-comment
    allowed = set(local_arguments) | {
        node.value
        for node in nodes
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and node.attr == "script_args"
    }
    for statement in body:
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node not in allowed:
                local_names.discard(node.id)
    for statement in body:
        if (
            isinstance(statement, ast.Assign | ast.AnnAssign)
            and statement.value is not None
            and (targets := statement.targets if isinstance(statement, ast.Assign) else [statement.target])
            and len(targets) == 1
            and isinstance(target := targets[0], ast.Name)
            and target.id in local_names
            and bound[target.id] == 1
            and (script := _namespace_script(statement.value, imports)) is not None
            and any(arg.id == target.id and arg.lineno > (statement.end_lineno or statement.lineno) for arg in local_arguments)
        ):
            yield script


def _uses(
    body: list[ast.stmt],
    package: str,
    outer: dict[str, str],
    *,
    parameters: frozenset[str] = frozenset(),
    class_scope: bool = False,
    function_scope: bool = False,
) -> Iterator[tuple[str, str]]:
    nodes = list(_scope_nodes(body))
    bound = _binding_counts(nodes)
    inherited = {name: value for name, value in outer.items() if name not in bound and name not in parameters}
    imports = inherited | {
        name: value for name, value in _imports(body, package) if bound[name] == 1 and name not in parameters
    }
    arguments = []
    for node in nodes:
        if isinstance(node, ast.Name | ast.Attribute) and (reference := _qualified(node, imports)):
            yield "import", reference
        elif isinstance(node, ast.Call):
            if (arg := _dispatch_argument(node, imports)) is not None:
                arguments.append(arg)
                if (script := _namespace_script(arg, imports)) is not None:
                    yield "dispatch", script
        elif isinstance(node, _FUNCTIONS):
            yield from _uses(
                node.body,
                package,
                outer if class_scope else imports,
                parameters=frozenset(child.arg for child in ast.iter_child_nodes(node.args) if isinstance(child, ast.arg)),
                function_scope=True,
            )
        elif isinstance(node, ast.ClassDef):
            yield from _uses(node.body, package, outer if class_scope else imports, class_scope=True)
    if function_scope:
        yield from (("dispatch", name) for name in _local_dispatches(body, nodes, imports, bound, arguments))


def _package(path: str) -> str:
    parts = pathlib.PurePosixPath(path).parent.parts
    if parts and parts[0] == _PLUGIN:
        parts = parts[1:]
    return ".".join(parts) if all(part.isidentifier() for part in parts) else ""


def _module_stems(module: str, caller: str) -> Iterator[str]:
    stem = module.replace(".", "/")
    toolkit = module == "agent_toolkit" or module.startswith("agent_toolkit.")
    if toolkit:
        yield f"{_PLUGIN}/{stem}"
    yield stem
    if not toolkit and (parent := caller.rpartition("/")[0]):
        yield f"{parent}/{stem}"


def _assignments(body: Iterable[ast.stmt]) -> Iterator[tuple[str, ast.stmt, ast.expr]]:
    for node in body:
        if isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                for name in _names(target):
                    yield name, node, node.value


def _global_reads(node: ast.stmt) -> Iterator[str]:
    pending = [symtable.symtable(ast.unparse(node), "<定義>", "exec")]
    while pending:
        table = pending.pop()
        yield from (symbol.get_name() for symbol in table.get_symbols() if symbol.is_global() and symbol.is_referenced())
        pending.extend(table.get_children())


class _Module:
    def __init__(self, name: str, source: str) -> None:
        self.body = ast.parse(source, filename=name).body
        self.package = _package(name)

    @cached_property
    def bindings(self) -> Counter[str]:
        return _binding_counts(_scope_nodes(self.body))

    @cached_property
    def imports(self) -> dict[str, str]:
        return {name: value for name, value in _imports(self.body, self.package) if self.bindings[name] == 1}

    def assigned_value(self, name: str) -> ast.expr | None:
        if self.bindings[name] == 1:
            for statement in self.body:
                if isinstance(statement, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id == name for target in statement.targets
                ):
                    return statement.value
                if (
                    isinstance(statement, ast.AnnAssign)
                    and isinstance(statement.target, ast.Name)
                    and statement.target.id == name
                ):
                    return statement.value
        return None

    @cached_property
    def reexports(self) -> dict[str, str]:
        if isinstance(value := self.assigned_value("__all__"), ast.List | ast.Tuple):
            return {
                element.value: self.imports[element.value]
                for element in value.elts
                if isinstance(element, ast.Constant) and isinstance(element.value, str) and element.value in self.imports
            }
        return {}

    @cached_property
    def definitions(self) -> dict[str, ast.stmt]:
        definitions = {name: node for name, node, _ in _assignments(self.body)}
        definitions.update((node.name, node) for node in self.body if isinstance(node, (*_FUNCTIONS, ast.ClassDef)))
        definitions.update((node.name.id, node) for node in self.body if isinstance(node, ast.TypeAlias))
        return definitions

    def fingerprint(self, symbol: str) -> tuple[tuple[str, str], ...]:
        definitions: dict[str, str] = {}
        pending = {symbol}
        while pending:
            name = pending.pop()
            if name in definitions:
                continue
            if node := self.definitions.get(name):
                definitions[name] = ast.dump(node)
                pending.update(_global_reads(node))
            elif name in self.imports:
                definitions[name] = self.imports[name]
        return tuple(sorted(definitions.items()))


class _Sources:
    def __init__(self, root: pathlib.Path, base: str, changed: set[str]) -> None:
        self.root = root
        self.base = base
        self.changed = changed
        self._texts: dict[tuple[str, bool], str] = {}
        self._modules: dict[tuple[str, bool], _Module] = {}
        self._files: dict[str, bool] = {}
        self._resolved: dict[tuple[str, str], bool] = {}

    def _git(self, *args: str) -> str:
        result = git_command.run(
            ["-C", str(self.root), *args], capture_output=True, text=True, timeout=60, recover_stale_lock=False
        )
        if result.returncode:
            raise ValueError(f"スクリプト利用関係の基点を取得できない: {format_cli_error(result)}")
        return result.stdout

    @cached_property
    def facades(self) -> set[str]:
        result = git_command.run(
            ["-C", str(self.root), "grep", "--no-recurse-submodules", "--untracked", "-lzF", "-e", "__all__", "--", "*.py"],
            capture_output=True,
            timeout=60,
            recover_stale_lock=False,
        )
        if result.returncode != 0 and not (result.returncode == 1 and not result.stdout and not result.stderr):
            diagnostic = subprocess.CompletedProcess(
                result.args, result.returncode, os.fsdecode(result.stdout), os.fsdecode(result.stderr)
            )
            raise ValueError(f"再公開の候補を取得できない: {format_cli_error(diagnostic)}")
        return self.changed | {os.fsdecode(name) for name in result.stdout.split(b"\0") if name}

    def read(self, path: str, *, old: bool = False) -> str:
        key = path, old and path in self.changed
        if key not in self._texts:
            if key[1]:
                listed = self._git("--literal-pathspecs", "ls-tree", "-z", self.base, "--", path)
                source = self._git("show", f"{self.base}:{path}") if listed else ""
            else:
                local = self.root / path
                source = (
                    local.read_text(encoding="utf-8") if local.is_file() and local.resolve().is_relative_to(self.root) else ""
                )
            self._texts[key] = source
        return self._texts[key]

    def module(self, path: str, *, old: bool = False) -> _Module:
        key = path, old and path in self.changed
        if key not in self._modules:
            self._modules[key] = _Module(path, self.read(path, old=old))
        return self._modules[key]

    def registry(self, *, old: bool = False) -> dict[str, str]:
        module = self.module(_REGISTRY, old=old)
        if isinstance(value := module.assigned_value("SCRIPT_PATHS"), ast.Dict):
            return dict(_registered_paths(value, module.imports))
        return {}

    def _existing(self, path: str, *, old: bool) -> bool:
        if old and path in self.changed:
            return bool(self.read(path, old=True))
        if path not in self._files:
            self._files[path] = (self.root / path).is_file()
        return self._files[path]

    def _symbol(self, reference: str, caller: str, *, old: bool) -> tuple[str, str, list[str]] | None:
        parts = reference.split(".")
        for length in range(len(parts), 0, -1):
            for stem in _module_stems(".".join(parts[:length]), caller):
                for candidate in (f"{stem}/__init__.py", f"{stem}.py"):
                    if self._existing(candidate, old=old):
                        if length == len(parts):
                            return candidate, "", []
                        return candidate, parts[length], parts[length + 1 :]
        return None

    def reexport_chain(self, reference: str, caller: str, *, old: bool) -> tuple[tuple[str, str], ...]:
        chain: dict[tuple[str, str], None] = {}
        while resolved := self._symbol(reference, caller, old=old):
            caller, name, tail = resolved
            if (caller, name) in chain:
                return ()
            chain[caller, name] = None
            if not name or caller not in self.facades or "__all__" not in self.read(caller, old=old):
                break
            if not (reference := self.module(caller, old=old).reexports.get(name, "")):
                break
            reference = ".".join([reference, *tail])
        return tuple(chain)

    def reexport_changed(self, reference: str, caller: str) -> bool:
        key = reference, caller.rpartition("/")[0]
        if key not in self._resolved:
            self._resolved[key] = self._reexport_changed(reference, caller)
        return self._resolved[key]

    def _reexport_changed(self, reference: str, caller: str) -> bool:
        after = self.reexport_chain(reference, caller, old=False)
        before = self.reexport_chain(reference, caller, old=True)
        if max(len(after), len(before)) < 2:
            return False
        if before != after:
            return True
        path, name = after[-1]
        return bool(name) and (
            path in self.changed and self.module(path, old=True).fingerprint(name) != self.module(path).fingerprint(name)
        )

    def candidate_facades(self) -> Iterator[str]:
        for path in sorted(self.facades):
            if path in self.changed:
                exports = self.module(path).reexports or self.module(path, old=True).reexports
                if exports:
                    yield path
            else:
                for reference in self.module(path).reexports.values():
                    if any(self._possible_origin(reference, path, old=old) for old in (True, False)):
                        yield path
                        break

    def _possible_origin(self, reference: str, path: str, *, old: bool) -> bool:
        chain = self.reexport_chain(reference, path, old=old)
        if any(name in self.changed or (not symbol and name in self.facades) for name, symbol in chain):
            return True
        prefixes = tuple(f"{stem}/" for stem in _module_stems(reference, path))
        if chain:
            last, symbol = chain[-1]
            if not symbol and last.endswith("/__init__.py"):
                prefixes += (last.removesuffix("__init__.py"),)
        # モジュールや名前空間パッケージは、利用側の属性が指す下位モジュールまで調べる。 pyfltr: allow-comment
        return any(candidate.startswith(prefixes) for candidate in self.facades)


def _registered_paths(value: ast.Dict, imports: dict[str, str]) -> Iterator[tuple[str, str]]:
    for key, node in zip(value.keys, value.values, strict=True):
        if (
            isinstance(key, ast.Constant)
            and isinstance(key.value, str)
            and isinstance(node, ast.Call)
            and _qualified(node.func, imports) == "pathlib.Path"
            and len(node.args) == 1
            and not node.keywords
            and isinstance(arg := node.args[0], ast.Constant)
            and isinstance(arg.value, str)
        ):
            path = pathlib.PurePosixPath(arg.value)
            if path.suffix == ".py" and not path.is_absolute() and ".." not in path.parts and not set("\\:\0") & set(arg.value):
                yield key.value, (_PLUGIN / path).as_posix()


def required_script_tests(
    root: pathlib.Path, test_paths: Iterable[pathlib.Path], changed_paths: Iterable[str], *, base: str
) -> list[dict[str, str]]:
    """削除を含むリポジトリ相対の差分から、静的な利用が確認できる試験だけを返す。  pyfltr: allow-comment"""
    changed = {name for name in changed_paths if name.endswith(".py")}
    if not changed:
        return []
    if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", base) is None:
        raise ValueError("スクリプト利用関係の基点には完全なcommit OIDを指定する")
    root = root.resolve()
    sources = _Sources(root, base, changed)
    current, previous = sources.registry(), sources.registry(old=True)
    affected = {
        command
        for command in current.keys() | previous.keys()
        if current.get(command) != previous.get(command)
        or (entry := current[command]) in changed
        or any(
            kind == "import" and sources.reexport_changed(reference, entry)
            for kind, reference in _uses(sources.module(entry).body, _package(entry), {})
        )
    }
    facades = set(map(pathlib.PurePosixPath, sources.candidate_facades()))
    packages = {path.parent for path in facades if path.stem == "__init__"}
    module_names = {path.stem for path in facades if path.stem != "__init__"} | {path.name for path in packages}
    if affected:
        module_names.add("script_name")
    if not module_names:
        return []
    hints = re.compile(r"\b(?:" + "|".join(re.escape(name) for name in sorted(module_names)) + r")\b")
    found: set[tuple[str, str]] = set()
    for path in sorted(set(test_paths)):
        name = path.relative_to(root).as_posix()
        # 相対importで使うパッケージの再公開名には、利用側にパッケージ名が現れない。 pyfltr: allow-comment
        if not hints.search(body := sources.read(name)) and not packages.intersection(pathlib.PurePosixPath(name).parents):
            continue
        for kind, reference in set(_uses(ast.parse(body, filename=name).body, _package(name), {})):
            if kind == "dispatch" and reference in affected:
                found.add((name, f"registered-script:{reference}"))
            elif kind == "import" and sources.reexport_changed(reference, name):
                found.add((name, f"static-reexport:{reference}"))
    return [{"test": test, "reason": reason} for test, reason in sorted(found)]
