"""試験を実行せず、文言・定数の写しだけの関数を構造的な候補として返す。

PythonはASTで固定値、ソース読取、help/説明/通知、定数属性を追跡する。
代入、for、parametrize、同じファイルと作業ツリー内で静的に解決できる補助も追跡する。
リテラルだけの比較は写しの出自を示さず、候補にしない。TS/TSXとRustは
コメント・文字列を分離した括弧構造からtest/it、#[test]とassertを抽出する。
TS/Rustの解析は全言語文法の検証ではなく、対応する宣言・比較式の部分解析である。
括弧や文字列の不整合、Python構文エラー、読取失敗は終了2とする。
公開hook通知フィールドadditionalContextの本文も既知のテキストとして扱い、
検査全体が固定断片の包含・先頭/末尾・回数・regexだけの形を候補にする。
一般のHTML変換・状態ログなど、出自不明の文字列比較は候補にしない。
未知の比較や検査式内の呼出し、入力変換・保存・拒否等を検査する関数は候補に断定しない。
動的生成、解決不能なimport、複雑な別名・分岐・マクロには見逃しがある。
候補は独立契約の不存在を証明せず、自動削除もしない。終了1は人による
削除・結果検査への置換・理由付き例外の判断を要求するためのエラーである。

JSON v1: root, files[{path,language,status,functions[{id,line,status,kinds,
reason,checks}]}], diagnostics[{path,line,kind,message}], schema_version。
関数idはPythonの修飾名、TSはtest/itの名前と行、Rustは関数名と行。
statusは関数がcandidate/exempt/clear/unclassified、ファイルが
analyzed/out_of_scope/error。通常成功は無出力、--jsonは全対象を出力する。
"""

import argparse
import ast
import dataclasses
import functools
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import tokenize

from agent_toolkit._git import command as git_command

_MARKER = "test-copy-contract:"
_COPY = {"literal", "constant", "text"}
_TRANSPARENT_METHODS = {
    "strip",
    "lstrip",
    "rstrip",
    "split",
    "splitlines",
    "lower",
    "upper",
    "decode",
    "keys",
    "values",
    "items",
    "resolve",
    "absolute",
    "joinpath",
    "with_name",
    "replace",
    "removeprefix",
    "removesuffix",
    "startswith",
    "endswith",
    "find",
    "count",
    "issubset",
    "issuperset",
    "intersection",
    "union",
    "difference",
    "join",
    "get",
}


@dataclasses.dataclass
class _Function:
    id: str
    line: int
    start: int
    end: int
    body_start: int
    checks: list[str] = dataclasses.field(default_factory=list)
    reason: str | None = None


def _combine(values: list[str]) -> str:
    if not values or "unknown" in values:
        return "unknown"
    if "behavior" in values:
        return "behavior"
    if "text" in values:
        return "text"
    if "constant" in values:
        return "constant"
    return "literal" if all(value == "literal" for value in values) else "unknown"


@functools.lru_cache(maxsize=16384)
def _name(node: ast.AST) -> str:
    return ast.unparse(node)


class _PythonAnalysis:
    def __init__(
        self,
        tree: ast.Module,
        path: pathlib.Path | None = None,
        root: pathlib.Path | None = None,
        loaded: dict[pathlib.Path, _PythonAnalysis] | None = None,
    ) -> None:
        self.path, self.root = path, root
        self.tree = tree
        self.loaded = loaded if loaded is not None else {}
        self.imports: dict[str, tuple[str, int]] = {}
        self.import_cache: dict[str, tuple[_PythonAnalysis, str] | None] = {}
        if path is not None:
            self.loaded[path] = self
        self.helpers = {node.name: node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.globals: dict[str, str] = {}
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                for alias in node.names:
                    name = alias.asname or (alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name)
                    self.globals[name] = "constant" if isinstance(node, ast.ImportFrom) and alias.name.isupper() else "module"
                    module = (
                        (f"{node.module}.{alias.name}" if node.module else alias.name)
                        if isinstance(node, ast.ImportFrom)
                        else (alias.name if alias.asname else name)
                    )
                    self.imports[name] = (module, node.level if isinstance(node, ast.ImportFrom) else 0)
            elif isinstance(node, ast.ClassDef):
                self.globals[node.name] = "module"
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.assign(node, self.globals)
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isupper() and self.globals.get(target.id) in _COPY:
                        self.globals[target.id] = "constant"

    def imported_helper(self, name: str) -> tuple[_PythonAnalysis, str] | None:
        if name not in self.import_cache:
            self.import_cache[name] = self._imported_helper(name)
        return self.import_cache[name]

    def _imported_helper(self, name: str) -> tuple[_PythonAnalysis, str] | None:
        if self.path is None or self.root is None or name.split(".")[0] not in self.imports:
            return None
        alias, *tail = name.split(".")
        module, level = self.imports[alias]
        parts = (module + ("." + ".".join(tail) if tail else "")).split(".")
        if len(parts) < 2:
            return None
        bases = [self.path.parents[level - 1]] if level else [self.path.parent, *self.path.parents]
        for base in bases:
            if not base.is_relative_to(self.root):
                continue
            module_path = base.joinpath(*parts[:-1])
            for candidate in (module_path.with_suffix(".py"), module_path / "__init__.py"):
                if not candidate.is_file():
                    continue
                try:
                    if candidate not in self.loaded:
                        _PythonAnalysis(ast.parse(candidate.read_text(encoding="utf-8")), candidate, self.root, self.loaded)
                    analysis = self.loaded[candidate]
                    if parts[-1] in analysis.helpers:
                        return analysis, parts[-1]
                except OSError, UnicodeError, SyntaxError:
                    # 補助の静的な解決に失敗した場合は出自不明のままに保つ。
                    return None
        return None

    def value(self, node: ast.AST | None, env: dict[str, str], stack: tuple[str, ...] = ()) -> str:
        if node is None:
            return "unknown"
        if isinstance(node, ast.Constant):
            return "literal"
        if isinstance(node, ast.Name):
            if node.id == "__file__":
                return "literal"
            return env.get(node.id, "unknown")
        if isinstance(node, ast.Attribute):
            if _name(node) in env:
                return env[_name(node)]
            if node.attr.isupper():
                return "constant" if self.value(node.value, env, stack) == "module" else "unknown"
            if node.attr in {"stdout", "stderr"}:
                return env.get(_name(node), "unknown")
            return self.value(node.value, env, stack)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return _combine([self.value(item, env, stack) for item in node.elts]) if node.elts else "literal"
        if isinstance(node, ast.Dict):
            return (
                _combine([self.value(item, env, stack) for item in [*node.keys, *node.values] if item])
                if node.values
                else "literal"
            )
        if isinstance(node, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            local = dict(env)
            for generator in node.generators:
                self.assign(ast.Assign(targets=[generator.target], value=generator.iter), local, stack)
            return self.value(node.elt, local, stack)
        if isinstance(node, ast.Subscript):
            if isinstance(node.slice, ast.Constant) and node.slice.value == "additionalContext":
                return "text"
            return self.value(node.value, env, stack)
        if isinstance(node, ast.JoinedStr):
            return _combine(
                [self.value(item.value if isinstance(item, ast.FormattedValue) else item, env, stack) for item in node.values]
            )
        if isinstance(node, ast.Await):
            return self.value(node.value, env, stack)
        if isinstance(node, ast.Call):
            name = _name(node.func)
            leaf = name.rsplit(".", 1)[-1]
            if (
                leaf == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "additionalContext"
            ):
                return "text"
            if leaf in {"read_text", "read_bytes"} and isinstance(node.func, ast.Attribute):
                return "text" if self.value(node.func.value, env, stack) in _COPY else "unknown"
            if leaf in {"getsource", "getdoc", "format_help", "format_usage"}:
                return "text"
            if leaf == "readouterr" and env.get("__help_output__") == "text":
                return "text"
            if leaf in {"run", "check_output", "invoke"}:
                help_call = any(isinstance(item, ast.Constant) and item.value in {"--help", "-h"} for item in ast.walk(node))
                return "text" if help_call else "behavior"
            if leaf in {"Path", "PurePath", "str", "set", "frozenset", "list", "tuple", "dict", "sorted", "len", "all", "any"}:
                return _combine([self.value(item, env, stack) for item in node.args])
            if isinstance(node.func, ast.Attribute) and leaf in _TRANSPARENT_METHODS:
                return _combine(
                    [self.value(node.func.value, env, stack), *[self.value(argument, env, stack) for argument in node.args]]
                )
            if name in self.helpers and name not in stack:
                helper = self.helpers[name]
                local = dict(self.globals)
                local.update(
                    {arg.arg: self.value(value, env, stack) for arg, value in zip(helper.args.args, node.args, strict=False)}
                )
                returns = [item.value for item in ast.walk(helper) if isinstance(item, ast.Return)]
                for item in helper.body:
                    if isinstance(item, (ast.Assign, ast.AnnAssign)):
                        self.assign(item, local, (*stack, name))
                result = _combine([self.value(item, local, (*stack, name)) for item in returns])
                # 補助の定数戻り値は計算結果なので、静的定数自体の比較として扱わない。
                return result if result == "text" else "unknown"
            imported = self.imported_helper(name)
            if imported is not None and name not in stack:
                analysis, helper_name = imported
                arguments = {
                    f"__copy_arg_{index}": self.value(argument, env, stack) for index, argument in enumerate(node.args)
                }
                call = ast.Call(func=ast.Name(id=helper_name), args=[ast.Name(id=key) for key in arguments], keywords=[])
                return analysis.value(call, arguments, (*stack, name + ":import"))
            return "unknown"
        if isinstance(node, (ast.BoolOp, ast.BinOp, ast.UnaryOp, ast.Compare)):
            return _combine(
                [self.value(child, env, stack) for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr)]
            )
        return "unknown"

    def assign(self, node: ast.Assign | ast.AnnAssign, env: dict[str, str], stack: tuple[str, ...] = ()) -> None:
        kind = self.value(node.value, env, stack)
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            # 部分更新は状態全体の固定出自を無効にし、添字の変数へ右辺を伝播させない。
            if isinstance(target, (ast.Subscript, ast.Attribute)):
                owner = target.value
                while isinstance(owner, (ast.Subscript, ast.Attribute)):
                    owner = owner.value
                if isinstance(owner, ast.Name):
                    env.update({owner.id: "unknown", "__fragment:" + owner.id: "no", "__source:" + owner.id: "no"})
            for item in ast.walk(target):
                if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Store):
                    env[item.id] = kind
                    env["__fragment:" + item.id] = "yes" if self.fixed_text(node.value, env) else "no"
                    env["__source:" + item.id] = (
                        "yes"
                        if kind in _COPY
                        and any(
                            isinstance(child, ast.Constant)
                            and isinstance(child.value, str)
                            and child.value.endswith((".py", ".md", ".ts", ".tsx", ".rs"))
                            for child in ast.walk(node.value or ast.Constant(value=None))
                        )
                        else "no"
                    )
                    if kind == "text":
                        env[f"{item.id}.stdout"] = "text"
                        env[f"{item.id}.stderr"] = "text"

    def fixed_text(self, node: ast.AST | None, env: dict[str, str]) -> bool:
        if isinstance(node, ast.Constant):
            return isinstance(node.value, (str, bytes))
        if isinstance(node, ast.Name):
            return env.get("__fragment:" + node.id) == "yes"
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            return bool(node.elts) and all(self.fixed_text(item, env) for item in node.elts)
        if isinstance(node, ast.JoinedStr):
            return self.value(node, env) in _COPY
        if isinstance(node, ast.BinOp):
            return self.fixed_text(node.left, env) and self.fixed_text(node.right, env)
        if isinstance(node, ast.IfExp):
            return self.fixed_text(node.body, env) and self.fixed_text(node.orelse, env)
        if isinstance(node, ast.Call) and _name(node.func).rsplit(".", 1)[-1] in {"str", "quote", "escape", "replace"}:
            receiver_fixed = (
                not isinstance(node.func, ast.Attribute)
                or node.func.attr != "replace"
                or self.value(node.func.value, env) in _COPY
            )
            return receiver_fixed and all(self.value(argument, env) in _COPY for argument in node.args)
        return False

    def text_shape(self, node: ast.AST, env: dict[str, str]) -> str | None:
        if isinstance(node, ast.BoolOp):
            kinds = [self.text_shape(value, env) for value in node.values]
            return "text-only" if all(kind == "text-only" for kind in kinds) else None
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return self.text_shape(node.operand, env)
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            right = node.comparators[0]
            if isinstance(node.ops[0], (ast.In, ast.NotIn)) and self.fixed_text(node.left, env):
                target = right
            elif isinstance(node.left, ast.Call) and isinstance(right, ast.Constant):
                if _name(node.left.func) == "len" and node.left.args:
                    return "text-support" if env.get("__text-target:" + _name(node.left.args[0])) == "yes" else None
                return self.text_shape(node.left, env)
            else:
                return None
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            leaf = node.func.attr
            if leaf in {"startswith", "endswith", "count"} and node.args and self.fixed_text(node.args[0], env):
                target = node.func.value
            elif leaf in {"search", "match", "fullmatch"} and len(node.args) >= 2 and self.fixed_text(node.args[0], env):
                target = node.args[1]
            elif leaf in {"is_file", "exists"} and env.get("__source:" + _name(node.func.value)) == "yes":
                return "text-support"
            else:
                return None
        else:
            return None
        if (
            self.value(target, env) != "text"
            or isinstance(target, ast.Subscript)
            or any(isinstance(child, ast.Call) and self.value(child, env) == "unknown" for child in ast.walk(target))
        ):
            return None
        env["__text-target:" + _name(target)] = "yes"
        return "text-only"

    def check(self, node: ast.AST, env: dict[str, str]) -> str:
        # 起動成功だけでは、同居する文言検査の独立契約を保証しない。
        if (
            isinstance(node, ast.Compare)
            and len(node.ops) == 1
            and isinstance(node.ops[0], ast.Eq)
            and isinstance(node.left, ast.Attribute)
            and node.left.attr == "returncode"
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == 0
        ):
            return "startup"
        if (
            isinstance(node, ast.Compare)
            and self.value(node.left, env) == "startup"
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Constant)
            and node.comparators[0].value == 0
        ):
            return "startup"
        kind = self.value(node, env)
        shape = self.text_shape(node, env)
        if shape is not None and kind not in {"text", "constant", "behavior"}:
            return shape
        return (
            "text-only"
            if kind == "text"
            else "constant-only"
            if kind == "constant"
            else "independent"
            if kind == "behavior"
            else "unknown"
        )

    def statements(self, statements: list[ast.stmt], env: dict[str, str], stack: tuple[str, ...] = ()) -> list[str]:
        checks: list[str] = []
        for node in statements:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                self.assign(node, env, stack)
            elif isinstance(node, ast.Assert):
                checks.append(self.check(node.test, env))
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                local = dict(env)
                self.assign(ast.Assign(targets=[node.target], value=node.iter), local, stack)
                checks.extend(self.statements(node.body, local, stack))
                checks.extend(self.statements(node.orelse, dict(env), stack))
            elif isinstance(node, (ast.With, ast.AsyncWith)):
                if any("raises" in _name(item.context_expr) for item in node.items):
                    help_exit = any(
                        isinstance(child, ast.Name) and child.id == "SystemExit"
                        for item in node.items
                        for child in ast.walk(item.context_expr)
                    ) and any(
                        isinstance(child, ast.Constant) and child.value == "--help"
                        for statement in node.body
                        for child in ast.walk(statement)
                    )
                    checks.append("startup" if help_exit else "independent")
                    if help_exit:
                        env["__help_output__"] = "text"
                        for item in node.items:
                            if isinstance(item.optional_vars, ast.Name):
                                env[item.optional_vars.id + ".value.code"] = "startup"
                checks.extend(self.statements(node.body, dict(env), stack))
            elif isinstance(node, (ast.If, ast.While)):
                checks.extend(self.statements(node.body, dict(env), stack))
                checks.extend(self.statements(node.orelse, dict(env), stack))
            elif isinstance(node, ast.Match):
                for case in node.cases:
                    checks.extend(self.statements(case.body, dict(env), stack))
            elif isinstance(node, (ast.Try, ast.TryStar)):
                checks.extend(self.statements(node.body + node.orelse + node.finalbody, dict(env), stack))
                for handler in node.handlers:
                    checks.extend(self.statements(handler.body, dict(env), stack))
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                name = _name(call.func)
                if isinstance(call.func, ast.Attribute) and call.func.attr in {
                    "append",
                    "extend",
                    "insert",
                    "update",
                    "add",
                    "discard",
                    "remove",
                    "pop",
                    "clear",
                    "setdefault",
                }:
                    env[_name(call.func.value)] = "behavior"
                if name not in self.helpers and not name.rsplit(".", 1)[-1].startswith(("assert", "check", "expect")):
                    for argument in [*call.args, *[keyword.value for keyword in call.keywords]]:
                        for child in ast.walk(argument):
                            if isinstance(child, ast.Name) and env.get(child.id) in {"literal", "constant"}:
                                env[child.id] = "unknown"
                if name in self.helpers and name not in stack:
                    helper = self.helpers[name]
                    local = dict(self.globals)
                    local.update(
                        {arg.arg: self.value(value, env) for arg, value in zip(helper.args.args, call.args, strict=False)}
                    )
                    for arg, value in zip(helper.args.args, call.args, strict=False):
                        local["__fragment:" + arg.arg] = "yes" if self.fixed_text(value, env) else "no"
                    mutating_callbacks = {
                        item.name
                        for item in helper.body
                        if isinstance(item, ast.FunctionDef)
                        and any(
                            isinstance(child, ast.Assign)
                            and any(isinstance(target, (ast.Subscript, ast.Attribute)) for target in child.targets)
                            for child in ast.walk(item)
                        )
                    }
                    # 更新callbackの成功確認だけを持つhelperは、関数の結果検査でなく状態準備として扱う。
                    if mutating_callbacks and all(
                        isinstance(item.test, ast.Call)
                        and any(isinstance(child, ast.Name) and child.id in mutating_callbacks for child in ast.walk(item.test))
                        for item in helper.body
                        if isinstance(item, ast.Assert)
                    ):
                        continue
                    checks.extend(self.statements(helper.body, local, (*stack, name)))
                elif (imported := self.imported_helper(name)) is not None and name not in stack:
                    analysis, helper_name = imported
                    helper = analysis.helpers[helper_name]
                    local = dict(analysis.globals)
                    for arg, value in zip(helper.args.args, call.args, strict=False):
                        local[arg.arg] = self.value(value, env)
                        local["__fragment:" + arg.arg] = "yes" if self.fixed_text(value, env) else "no"
                    checks.extend(analysis.statements(helper.body, local, (*stack, name)))
                elif name.rsplit(".", 1)[-1].startswith("assert"):
                    if "Raises" in name or "Warns" in name or "assert_called" in name or "assert_not_called" in name:
                        checks.append("independent")
                    else:
                        kind = _combine([self.value(argument, env) for argument in call.args])
                        checks.append("text-only" if kind == "text" else "constant-only" if kind == "constant" else "unknown")
                elif name.rsplit(".", 1)[-1].startswith(("check", "expect")):
                    checks.append("unknown")
        return checks


def _python(
    source: str, path: pathlib.Path, root: pathlib.Path, loaded: dict[pathlib.Path, _PythonAnalysis]
) -> tuple[list[_Function], dict[int, str]]:
    analysis = loaded.get(path)
    if analysis is None:
        analysis = _PythonAnalysis(ast.parse(source), path, root, loaded)
    functions: list[_Function] = []

    def visit(body: list[ast.stmt], prefix: str = "") -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                visit(node.body, prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
                env = dict(analysis.globals)
                env.update(
                    {argument.arg: "unknown" for argument in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
                )
                for decorator in node.decorator_list:
                    if (
                        isinstance(decorator, ast.Call)
                        and _name(decorator.func).endswith("parametrize")
                        and len(decorator.args) >= 2
                    ):
                        names = decorator.args[0]
                        if isinstance(names, ast.Constant) and isinstance(names.value, str):
                            for name in names.value.split(","):
                                env[name.strip()] = analysis.value(decorator.args[1], env)
                                env["__fragment:" + name.strip()] = (
                                    "yes" if analysis.fixed_text(decorator.args[1], env) else "no"
                                )
                start = min([node.lineno, *[item.lineno for item in node.decorator_list]])
                functions.append(
                    _Function(
                        prefix + node.name,
                        node.lineno,
                        start,
                        node.end_lineno or node.lineno,
                        node.body[0].lineno,
                        analysis.statements(node.body, env),
                    )
                )

    visit(analysis.tree.body)
    comments = {
        token.start[0]: token.string
        for token in tokenize.generate_tokens(io.StringIO(source).readline)
        if token.type == tokenize.COMMENT
    }
    return functions, comments


@dataclasses.dataclass
class _Token:
    value: str
    line: int
    literal: bool = False


def _tokens(source: str, rust: bool) -> tuple[list[_Token], dict[int, str]]:
    """文字列中の擬似宣言・括弧を構造から除き、位置を保つ。"""
    quoted = (
        r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\\n])\'' if rust else r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\\n])*\'|`(?:\\.|[^`\\])*`'
    )
    regex = r"(?P<regex>/(?:\\.|[^/\\\n\[]|\[(?:\\.|[^\]\\])*\])+/[a-z]*)|" if not rust else ""
    pattern = re.compile(
        r"(?P<space>\s+)|(?P<comment>//[^\n]*|/\*[\s\S]*?\*/)|"
        r'(?P<raw>r(?P<hash>\#*)"[\s\S]*?"(?P=hash))|'
        f"(?P<string>{quoted})|" + regex + r"(?P<word>[\w$]+)|(?P<operator>=>|===|!==|==|!=|::|&&|\|\||.)",
        re.UNICODE,
    )
    tokens: list[_Token] = []
    comments: dict[int, str] = {}
    line = 1
    for match in pattern.finditer(source):
        value = match.group()
        group = match.lastgroup
        if group == "comment":
            for offset, part in enumerate(value.splitlines()):
                comments[line + offset] = part
        elif group != "space":
            if value == "/" and source[match.end() : match.end() + 1] == "*":
                raise ValueError(f"{line}行: コメントが閉じていない")
            if value in {'"', "`"} or (value == "'" and not rust):
                raise ValueError(f"{line}行: 閉じていない文字列")
            tokens.append(_Token(value, line, group in {"string", "raw", "regex"} or value.isdecimal()))
        line += value.count("\n")
    return tokens, comments


def _pairs(tokens: list[_Token]) -> dict[int, int]:
    pairs: dict[int, int] = {}
    stack: list[int] = []
    for index, token in enumerate(tokens):
        if token.literal:
            continue
        if token.value in {"(", "{", "["}:
            stack.append(index)
        elif token.value in {")", "}", "]"}:
            if not stack or tokens[stack[-1]].value != {")": "(", "}": "{", "]": "["}[token.value]:
                raise ValueError(f"{token.line}行: 括弧が対応していない")
            opening = stack.pop()
            pairs[opening] = index
    if stack:
        raise ValueError(f"{tokens[stack[-1]].line}行: 括弧が閉じていない")
    return pairs


def _foreign_value(tokens: list[_Token], env: dict[str, str]) -> str:
    transparent = {"contains", "includes", "startsWith", "endsWith", "trim", "to_string", "as_str", "keys", "len", "unwrap"}
    readers = {"read_to_string", "readFileSync", "readFile", "include_str", "include_bytes"}
    for index, token in enumerate(tokens[:-1]):
        following = index + 1 + (tokens[index + 1].value == "!")
        if (
            following < len(tokens)
            and tokens[following].value == "("
            and token.value.isidentifier()
            and token.value not in transparent | readers | {"vec"}
        ):
            return "unknown"
    significant = [token for token in tokens if token.value not in {"(", ")", "[", "]", "{", "}", ",", ";", "&", "!"}]
    if not significant:
        return "unknown"
    # 各要素が既知の値かを確認し、未知の関数呼出しを定数へ誤分類しない。
    kinds = []
    for index, token in enumerate(significant):
        if token.literal or token.value in {"true", "false", "null", "None", "vec"}:
            kinds.append("literal")
        elif token.value in readers:
            kinds.append("text")
        elif env.get(token.value) == "module" and index + 1 < len(significant) and significant[index + 1].value in {".", "::"}:
            continue
        elif token.value in env:
            kinds.append(env[token.value])
        elif token.value.isidentifier() and token.value.isupper():
            root = index - 2
            while root >= 2 and significant[root - 1].value in {".", "::"}:
                root -= 2
            static = (
                index > 0 and significant[index - 1].value == "::" or root >= 0 and env.get(significant[root].value) == "module"
            )
            kinds.append("constant" if static else "unknown")
        elif (
            token.value
            in {
                ".",
                "::",
                "==",
                "===",
                "!=",
                "!==",
                "in",
                ":",
                "as",
                "str",
                "contains",
                "includes",
                "startsWith",
                "endsWith",
                "trim",
                "to_string",
                "as_str",
                "keys",
                "len",
                "unwrap",
                "stdout",
                "stderr",
            }
            or index + 1 < len(significant)
            and significant[index + 1].value in {".", "::"}
        ):
            continue
        else:
            kinds.append("unknown")
    return _combine(kinds)


def _foreign_checks(
    tokens: list[_Token],
    pairs: dict[int, int],
    start: int,
    end: int,
    helpers: dict[str, tuple[list[str], int, int]],
    bindings: dict[str, str] | None = None,
    stack: tuple[str, ...] = (),
) -> list[str]:
    env = dict(bindings or {})
    checks: list[str] = []
    index = start
    while index < end:
        token = tokens[index]
        if token.value in {"let", "const", "var"} and index + 2 < end:
            name_index = index + 1 + (tokens[index + 1].value == "mut")
            stop = next((position for position in range(name_index + 1, end) if tokens[position].value == ";"), end)
            equal = next((position for position in range(name_index + 1, stop) if tokens[position].value == "="), None)
            if equal is not None:
                env[tokens[name_index].value] = _foreign_value(tokens[equal + 1 : stop], env)
        if token.value == "for" and index + 3 < end:
            stop = next((position for position in range(index + 1, end) if tokens[position].value == "{"), end)
            relation = next((position for position in range(index + 1, stop) if tokens[position].value in {"in", "of"}), None)
            if relation is not None:
                name_index = index + 1 + (tokens[index + 1].value == "(")
                if tokens[name_index].value in {"const", "let"}:
                    name_index += 1
                env[tokens[name_index].value] = _foreign_value(tokens[relation + 1 : stop], env)
        if token.value in helpers and token.value not in stack and index + 1 in pairs:
            params, helper_start, helper_end = helpers[token.value]
            close = pairs[index + 1]
            arguments: list[list[_Token]] = [[]]
            position = index + 2
            while position < close:
                if tokens[position].value == ",":
                    arguments.append([])
                elif position in pairs:
                    arguments[-1].extend(tokens[position : pairs[position] + 1])
                    position = pairs[position]
                else:
                    arguments[-1].append(tokens[position])
                position += 1
            local = {name: _foreign_value(argument, env) for name, argument in zip(params, arguments, strict=False)}
            checks.extend(_foreign_checks(tokens, pairs, helper_start, helper_end, helpers, local, (*stack, token.value)))
            index = close
        elif token.value in {
            "assert",
            "assert_eq",
            "assert_ne",
            "debug_assert",
            "debug_assert_eq",
            "strictEqual",
            "deepStrictEqual",
            "deepEqual",
            "equal",
        }:
            opening = index + 1 + (index + 1 < end and tokens[index + 1].value == "!")
            if opening in pairs:
                content = tokens[opening + 1 : pairs[opening]]
                kind = _foreign_value(content, env)
                checks.append("text-only" if kind == "text" else "constant-only" if kind == "constant" else "unknown")
                index = pairs[opening]
        elif token.value == "expect" and index + 1 in pairs:
            opening = index + 1
            close = pairs[opening]
            content = tokens[opening + 1 : close]
            matcher_end = next((position for position in range(close + 1, end) if tokens[position].value == ";"), end)
            tail = tokens[close + 1 : matcher_end]
            kind = _foreign_value(content, env)
            if any(item.value in {"toThrow", "toHaveBeenCalled", "toHaveBeenCalledWith", "toHaveProperty"} for item in tail):
                checks.append("independent")
            elif any(item.value in {"returncode", "status"} for item in content) and any(item.value == "0" for item in tail):
                checks.append("startup")
            else:
                matcher = next((position for position in range(close + 1, matcher_end) if position in pairs), None)
                if matcher is not None:
                    expected = tokens[matcher + 1 : pairs[matcher]]
                    kind = _combine([kind, _foreign_value(expected, env)])
                checks.append("text-only" if kind == "text" else "constant-only" if kind == "constant" else "unknown")
            index = close
        elif token.value.startswith("assert") and index + 1 < end:
            checks.append("unknown")
        index += 1
    return checks


def _foreign(source: str, rust: bool) -> tuple[list[_Function], dict[int, str]]:
    tokens, comments = _tokens(source, rust)
    pairs = _pairs(tokens)
    globals_: dict[str, str] = {}
    for index, token in enumerate(tokens):
        if token.value in {"import", "use"}:
            end = next((position for position in range(index + 1, len(tokens)) if tokens[position].value == ";"), len(tokens))
            for item in tokens[index + 1 : end]:
                if item.value.isidentifier() and item.value not in {"as", "from", "type"}:
                    globals_[item.value] = "constant" if item.value.isupper() else "module"
    helpers: dict[str, tuple[list[str], int, int]] = {}
    for index, token in enumerate(tokens):
        if token.value not in {"fn", "function"} or index + 2 not in pairs:
            continue
        params_end = pairs[index + 2]
        opening = next(
            (position for position in range(params_end + 1, len(tokens)) if tokens[position].value in {"{", ";"}), None
        )
        if opening is not None and opening in pairs and tokens[opening].value == "{":
            params = [
                tokens[position].value
                for position in range(index + 3, params_end)
                if tokens[position].value.isidentifier() and (position == index + 3 or tokens[position - 1].value == ",")
            ]
            helpers[tokens[index + 1].value] = (params, opening + 1, pairs[opening])
    depth = 0
    for index, token in enumerate(tokens):
        if token.value == "{" and not token.literal:
            depth += 1
        elif token.value == "}" and not token.literal:
            depth -= 1
        elif depth == 0 and token.value == "const" and index + 2 < len(tokens):
            end = next((position for position in range(index + 2, len(tokens)) if tokens[position].value == ";"), len(tokens))
            equal = next((position for position in range(index + 2, end) if tokens[position].value == "="), None)
            name = tokens[index + 1].value
            if equal is not None and name.isupper():
                kind = _foreign_value(tokens[equal + 1 : end], globals_)
                globals_[name] = "constant" if kind in _COPY else "unknown"
    functions: list[_Function] = []
    for index, token in enumerate(tokens):
        if rust:
            if token.value != "#" or [item.value for item in tokens[index + 1 : index + 4]] != ["[", "test", "]"]:
                continue
            fn = next((position for position in range(index + 4, len(tokens)) if tokens[position].value == "fn"), None)
            if fn is None or fn + 1 >= len(tokens):
                raise ValueError(f"{token.line}行: #[test]に関数がない")
            opening = next((position for position in range(fn + 2, len(tokens)) if tokens[position].value == "{"), None)
            name = tokens[fn + 1].value
            line = tokens[fn].line
        else:
            if token.value not in {"test", "it"} or (index and tokens[index - 1].value == "."):
                continue
            call = index + 1
            while call + 1 < len(tokens) and tokens[call].value == ".":
                call += 2
            if call >= len(tokens) or tokens[call].value != "(" or call not in pairs:
                continue
            close = pairs[call]
            callback = next(
                (position for position in range(call + 1, close) if tokens[position].value in {"=>", "function"}), None
            )
            if callback is None:
                continue
            opening = (
                callback + 1
                if tokens[callback].value == "=>"
                else next((position for position in range(callback + 1, close) if tokens[position].value == "{"), None)
            )
            name = tokens[call + 1].value.strip("\"'`")
            line = token.line
        if opening is None or opening not in pairs or tokens[opening].value != "{":
            raise ValueError(f"{token.line}行: 試験本文を解析できない")
        end = pairs[opening]
        functions.append(
            _Function(
                f"{name}@{line}",
                line,
                token.line,
                tokens[end].line,
                tokens[min(opening + 1, end)].line,
                _foreign_checks(tokens, pairs, opening + 1, end, helpers, globals_),
            )
        )
    return functions, comments


def _exceptions(functions: list[_Function], comments: dict[int, str]) -> list[tuple[int, str]]:
    errors = []
    used: set[int] = set()
    for function in functions:
        locations = [function.start - 1, *range(function.start, min(function.body_start, function.end) + 1)]
        markers = [(line, comments[line]) for line in locations if line in comments and _MARKER in comments[line]]
        for line, comment in markers:
            if line in used:
                errors.append((line, "1つの例外を複数関数へ適用できない"))
            used.add(line)
            reason = comment.split(_MARKER, 1)[1].strip()
            if not reason or function.reason is not None:
                errors.append((line, "例外理由が空または重複している"))
            else:
                function.reason = reason
    for line, comment in comments.items():
        if _MARKER in comment and line not in used:
            errors.append((line, "例外は1関数の直前または本文先頭に置く"))
    return errors


def _git(root: pathlib.Path, *args: str) -> bytes:
    result = git_command.run(["-C", str(root), *args], capture_output=True, check=False, timeout=60)
    if result.returncode:
        raise ValueError(f"Git照会失敗: {result.stderr.decode('utf-8', 'replace').strip()} (exit {result.returncode})")
    return result.stdout


def _language(path: pathlib.Path) -> str | None:
    if path.name.endswith(("_test.py", "_spec.py")):
        return "python"
    if path.suffix in {".ts", ".tsx"}:
        return "typescript"
    if path.suffix == ".rs":
        return "rust"
    return None


def main(argv: list[str] | None = None) -> int:
    """Git追跡試験または明示された合成ファイルを静的に検査する。"""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=pathlib.Path, help="root相対または絶対パス。未追跡ファイルも可")
    parser.add_argument("--root", type=pathlib.Path, default=pathlib.Path.cwd(), help="既定の追跡母集団を得るGit作業ツリー")
    parser.add_argument("--json", action="store_true", help="対象全パス・関数・例外・診断をJSONで出力")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    files: list[dict[str, object]] = []
    diagnostics: list[dict[str, object]] = []
    loaded: dict[pathlib.Path, _PythonAnalysis] = {}
    try:
        if not root.is_dir():
            raise ValueError(f"rootがディレクトリではない: {root}")
        paths = args.paths
        if not paths:
            root = pathlib.Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel").rstrip(b"\n")))
            paths = [pathlib.Path(os.fsdecode(name)) for name in _git(root, "ls-files", "-z").split(b"\0") if name]
            paths = [path for path in paths if _language(path)]
        paths = sorted({path if path.is_absolute() else root / path for path in paths})
        for path in paths:
            display = path.relative_to(root).as_posix() if path.is_relative_to(root) else path.as_posix()
            language = _language(path)
            entry: dict[str, object] = {"path": display, "language": language, "status": "out_of_scope", "functions": []}
            files.append(entry)
            try:
                source = path.read_text(encoding="utf-8")
                if language is None:
                    continue
                functions, comments = (
                    _python(source, path, root, loaded) if language == "python" else _foreign(source, language == "rust")
                )
                entry["status"] = "analyzed" if functions else "out_of_scope"
                for line, message in _exceptions(functions, comments):
                    diagnostics.append({"path": display, "line": line, "kind": "invalid-exception", "message": message})
                results = []
                for function in functions:
                    kinds = sorted(set(function.checks) & {"text-only", "constant-only"})
                    candidate = bool(kinds) and all(
                        check in {"text-only", "constant-only", "startup", "text-support"} for check in function.checks
                    )
                    status = (
                        "exempt"
                        if function.reason
                        else "candidate"
                        if candidate
                        else "unclassified"
                        if "unknown" in function.checks or not function.checks
                        else "clear"
                    )
                    results.append(
                        {
                            "id": function.id,
                            "line": function.line,
                            "status": status,
                            "kinds": kinds,
                            "reason": function.reason,
                            "checks": function.checks,
                        }
                    )
                    if status == "candidate":
                        diagnostics.append(
                            {
                                "path": display,
                                "line": function.line,
                                "kind": "+".join(kinds),
                                "message": f"{function.id}: 文言・定数の比較だけの写し候補",
                            }
                        )
                entry["functions"] = results
            except (OSError, UnicodeError, SyntaxError, ValueError, tokenize.TokenError) as error:
                entry["status"] = "error"
                diagnostics.append(
                    {"path": display, "line": getattr(error, "lineno", None) or 1, "kind": "input-error", "message": str(error)}
                )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        diagnostics.append({"path": str(root), "line": 1, "kind": "input-error", "message": str(error)})
    if args.json:
        print(
            json.dumps(
                {"schema_version": 1, "root": str(root), "files": files, "diagnostics": diagnostics},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for diagnostic in diagnostics:
            print(
                f"{diagnostic['path']}:{diagnostic['line']}: error: {diagnostic['kind']}: {diagnostic['message']}",
                file=sys.stderr,
            )
        if diagnostics:
            print(
                "次の操作: 写しだけの関数を削除するか、独立した結果の検査へ置き換える。正当な契約は関数直前か本文先頭に "
                "# test-copy-contract: <保証する独立契約の理由> を置く。入力不良は対象の構文・パス・読取権限を修正する。",
                file=sys.stderr,
            )
    if any(item["kind"] in {"input-error", "invalid-exception"} for item in diagnostics):
        return 2
    return 1 if diagnostics else 0


if __name__ == "__main__":
    raise SystemExit(main())
