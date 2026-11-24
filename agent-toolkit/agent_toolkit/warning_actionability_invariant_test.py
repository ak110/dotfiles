from __future__ import annotations

import ast
import pathlib
import re

import pytest

_DECLARATIVE_ACTION = re.compile(
    r"(?:確認する|取得する|再読する|実行する|戻る|進む|保存する|報告する|再試行する)(?=[。.!！]|$)"
)


def _reference(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_reference(node.value)}.{node.attr}"
    return ""


def _may_be_warn(node: ast.AST, constants: dict[str, ast.AST], seen: frozenset[str] = frozenset()) -> bool | None:
    if isinstance(node, ast.IfExp):
        branches = (_may_be_warn(node.body, constants, seen), _may_be_warn(node.orelse, constants, seen))
        if True in branches:
            return True
        return False if all(branch is False for branch in branches) else None
    if isinstance(node, (ast.Name, ast.Attribute)):
        reference = _reference(node)
        if reference in constants and reference not in seen:
            return _may_be_warn(constants[reference], constants, seen | {reference})
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value == "warn"
    return None


def _strings(node: ast.AST, constants: dict[str, ast.AST], seen: frozenset[str] = frozenset()) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, (ast.Name, ast.Attribute)):
        reference = _reference(node)
        if reference in constants and reference not in seen:
            return _strings(constants[reference], constants, seen | {reference})
        return ""
    return "".join(_strings(child, constants, seen) for child in ast.iter_child_nodes(node))


def _definitions(tree: ast.AST) -> dict[str, ast.AST]:
    return {
        target.id: node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _violations(source: str, modules: dict[str, dict[str, str]] | None = None) -> list[int]:
    tree = ast.parse(source)
    constants: dict[str, ast.AST] = {}
    if modules is not None:
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                for alias in node.names:
                    local = alias.asname or alias.name
                    value = modules.get(node.module, {}).get(alias.name)
                    if value is not None:
                        constants[local] = ast.Constant(value=value)
                    for key, text in modules.get(f"{node.module}.{alias.name}", {}).items():
                        constants[f"{local}.{key}"] = ast.Constant(value=text)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    for key, text in modules.get(alias.name, {}).items():
                        constants[f"{alias.asname or alias.name}.{key}"] = ast.Constant(value=text)
    constants.update(_definitions(tree))
    factories = {
        alias.asname or alias.name: alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "agent_toolkit._hooks.notice"
        for alias in node.names
    }
    warning_names = {"report_warning", "report_persistent_warning", "report", "warning"}
    for name, value in _definitions(tree).items():
        if not isinstance(value, ast.Call):
            continue
        factory = factories.get(_reference(value.func), _reference(value.func))
        default_warn = any(
            keyword.arg == "default_tag" and _strings(keyword.value, constants) == "warn" for keyword in value.keywords
        )
        if factory == "warning_formatter" or factory == "formatter" and default_warn:
            warning_names.add(name)
    violations: list[int] = []
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = (
            call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id if isinstance(call.func, ast.Name) else ""
        )
        actions = [keyword.value for keyword in call.keywords if keyword.arg in {"next_action", "fix"}]
        tag_keywords = [keyword.value for keyword in call.keywords if keyword.arg == "tag"]
        if tag_keywords:
            resolved_tags = [_may_be_warn(value, constants) for value in tag_keywords]
            if True in resolved_tags:
                is_warning = True
            elif all(tag is False for tag in resolved_tags):
                is_warning = False
            else:
                is_warning = name in warning_names
        else:
            is_warning = name in warning_names
        if is_warning and any(_DECLARATIVE_ACTION.search(_strings(action, constants)) for action in actions):
            violations.append(call.lineno)
            continue
        if not is_warning:
            continue
        if name == "report" and (not call.args or not _strings(call.args[0], constants).startswith("警告: ")):
            continue
        text = "".join(_strings(action, constants) for action in actions) if actions else _strings(call, constants)
        if any(word in text for word in ("対応不要", "対処不要", "操作不要")) and not any(
            word in text for word in ("場合", "なら", "確認", "再発", "繰り返", "解消")
        ):
            violations.append(call.lineno)
    return violations


@pytest.mark.cpu_limit(
    13.75,
    reason=(
        "本体とpytools全量を2回のAST解析で照合し、任意ファイルの警告から具体次操作への接続を検証する（Linux CI CPU合"
        "計最大6.737秒）。"
    ),
)
def test_warning_actionability_invariant() -> None:
    """本体とpytoolsの警告を、参照先定数の本文も含めて走査する。"""
    repository = pathlib.Path(__file__).resolve().parents[2]
    violations: list[str] = []
    sources = {}
    modules = {}
    for root in (repository / "agent-toolkit" / "agent_toolkit", repository / "pytools"):
        for path in root.rglob("*.py"):
            if path.name.endswith("_test.py") or "_testing" in path.parts:
                continue
            source = path.read_text(encoding="utf-8-sig")
            sources[path] = source
            relative = path.relative_to(repository).with_suffix("").parts
            if relative[0] == "agent-toolkit":
                relative = relative[1:]
            if relative[-1] == "__init__":
                relative = relative[:-1]
            definitions = _definitions(ast.parse(source))
            modules[".".join(relative)] = {key: _strings(value, definitions) for key, value in definitions.items()}
    for path, source in sources.items():
        violations.extend(f"{path}:{line}" for line in _violations(source, modules))
    assert not violations, "\n".join(violations)


def test_warning_actionability_resolves_constants() -> None:
    assert _violations('ACTION = "対応不要（処理は継続した）"\noutcome.report_warning("結果", next_action=ACTION)') == [2]
    assert not _violations('outcome.report_warning("保持", next_action="使用中だった場合は確認してください")')
    assert _violations(
        'from example import ACTION\nreport_warning("結果", next_action=ACTION)', {"example": {"ACTION": "対応不要"}}
    ) == [2]


@pytest.mark.parametrize(
    "verb", ["確認する", "取得する", "再読する", "実行する", "戻る", "進む", "保存する", "報告する", "再試行する"]
)
@pytest.mark.parametrize("argument", ["fix", "next_action"])
def test_action_sentences_reject_declarative(verb: str, argument: str) -> None:
    assert _violations(f'formatter("理由", tag="warn", {argument}="対象を{verb}。復旧後に続けてください。")') == [1]


@pytest.mark.parametrize(
    "text",
    [
        "対象は記録済みである。",
        "記録を取得できない。",
        "生成元が処理した。",
        "確認する場合は対象を保存してください。",
    ],
)
def test_fact_sentences_and_polite_instructions_are_allowed(text: str) -> None:
    assert not _violations(f'formatter("理由", tag="warn", fix="{text}")')


@pytest.mark.parametrize(
    "definition",
    [
        'from agent_toolkit._hooks.notice import warning_formatter as factory\nnotice = factory("hook")',
        'from agent_toolkit._hooks.notice import formatter as factory\nnotice = factory("hook", default_tag="warn")',
    ],
)
def test_default_warning_formatters_respect_explicit_tag(definition: str) -> None:
    assert _violations(definition + '\nnotice("理由", fix="資料を取得する。")') == [3]


def test_formatter_explicit_non_warn_tag_skips_warning_checks() -> None:
    definition = 'from agent_toolkit._hooks.notice import formatter as factory\nnotice = factory("hook", default_tag="warn")'
    assert _violations(definition + '\nnotice("理由", tag=dynamic_tag, fix="資料を取得する。")') == [3]
    assert not _violations(definition + '\nnotice("理由", tag="", fix="対応不要")')
    assert not _violations(definition + '\nnotice("理由", tag="notice", fix="資料を取得する。")')
    plain = 'from agent_toolkit._hooks.notice import formatter as factory\nnotice = factory("hook")'
    assert _violations(plain + '\nnotice("理由", tag="warn" if problem else "", fix="対応不要")') == [3]
    assert _violations(plain + '\ntag = "warn" if problem else ""\nnotice("理由", tag=tag, fix="対応不要")') == [4]
    assert not _violations(plain + '\nnotice("理由", tag="notice" if problem else "", fix="対応不要")')


def test_report_without_warning_prefix_rejects_declarative() -> None:
    """警告接頭辞のない次操作reportも対象にし、失敗専用APIの集合は広げない。"""
    assert _violations('next_action.report("読取不能", next_action="原本を再読する。")') == [1]
    assert not _violations('outcome.report_failure("読取不能", next_action="原本を再読する。")')
    assert not _violations('formatter("生成元が原本を再読する。", tag="warn", fix="入力を確認してください。")')
