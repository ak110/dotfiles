import ast
import hashlib
import json
import os
import pathlib
import re
import subprocess
from typing import Any

from agent_toolkit._common.markdown_headings import h2_section_bodies
from agent_toolkit._git import command as git_command

from pytools._internal import repository_references
from pytools._internal.common import format_cli_error

_HELP_TEST = "agent-toolkit/agent_toolkit/atk_help_test.py"
_LINK_TEST = "markdown_link_anchor_invariant_test.py"
_TERM_TEST = "retired_terms_invariant_test.py"
_STOP_TEST = "subprocess_stop_invariant_test.py"
_REFERENCE_TEST = "agent_toolkit_reference_invariant_test.py"
_HOOK_BUDGET_TEST = "agent-toolkit/agent_toolkit/hook_budget_test.py"
_ROLE_DOCUMENT_TEST = "agent-toolkit/task_documents_invariant_test.py"
_TOOLKIT = "agent-toolkit/agent_toolkit/"
_SERVER = _TOOLKIT + "_agents_server/"
_CODEX_LAUNCH_TESTS = tuple(_SERVER + name for name in ("codex_auto_reset_backend_test.py", "codex_resume_test.py"))
_CODEX_LAUNCH_SOURCES = frozenset(
    _SERVER + name for name in ("launch_requests.py", "manager.py", "manager_start.py", "codex_start.py")
)
_ROLE_GENERATORS = {
    "scripts/sync_generated_files.py": {"sync_input_contracts", "sync_return_contracts", "sync_quality_principles"},
    _TOOLKIT + "_common/task_documents.py": {"require_role_document", "launch_contract_documents", "input_contract_body"},
}
_REFERENCE_DEFINITIONS = frozenset({"_test_helpers.py", "pytools/_internal/repository_references.py", "pyproject.toml"})
_HELP_CHANGE = re.compile(r"\b(add_parser|add_subparsers|register_subcommand|help_text)\b|\bhelp\s*=|--help")
_STOP_CHANGE = re.compile(r"\.(?:kill|terminate|stop_owned_process|reclaim_processes|reclaim_unreaped)\b|\btimeout\s*=")


def _diff(root: pathlib.Path, base: str, *args: str) -> bytes:
    result = git_command.run(
        ["-C", str(root), "diff", "--no-ext-diff", "--no-renames", *args, base, "--"],
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        diagnostic = subprocess.CompletedProcess(
            result.args, result.returncode, os.fsdecode(result.stdout), os.fsdecode(result.stderr)
        )
        raise ValueError(f"統合候補の差分を取得できない: {format_cli_error(diagnostic)}")
    return result.stdout


def _role_generation_python(source: str, entries: set[str]) -> dict[str, str]:
    definitions: dict[str, ast.AST] = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef | ast.ClassDef):
            definitions[node.name] = node
        elif isinstance(node, ast.Assign | ast.AnnAssign | ast.TypeAlias):
            targets = (
                node.targets
                if isinstance(node, ast.Assign)
                else [node.name if isinstance(node, ast.TypeAlias) else node.target]
            )
            definitions.update((target.id, node) for target in targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.Import | ast.ImportFrom):
            definitions.update((alias.asname or alias.name.split(".")[0], node) for alias in node.names)
    result: dict[str, str] = {}
    pending = entries & definitions.keys()
    while pending:
        name = pending.pop()
        definition = definitions[name]
        result[name] = ast.dump(definition)
        pending.update(
            node.id
            for node in ast.walk(definition)
            if isinstance(node, ast.Name) and node.id in definitions and node.id not in result
        )
    return result


def _role_generation_startup(source: str, entries: set[str]) -> list[str]:
    tree = ast.parse(source)
    result: list[str] = []
    for statement in tree.body:
        if isinstance(statement, ast.FunctionDef) and statement.name == "main":
            last_sync = next(
                (
                    index
                    for index, child in reversed(list(enumerate(statement.body)))
                    if any(isinstance(node, ast.Name) and node.id in entries for node in ast.walk(child))
                ),
                -1,
            )
            if last_sync >= 0:
                first = 1 if ast.get_docstring(statement) is not None else 0
                result.extend(
                    ast.dump(_RoleStartupDiagnostics().visit(child)) for child in statement.body[first : last_sync + 1]
                )
        elif any(isinstance(node, ast.Name) and node.id == "main" for node in ast.walk(statement)):
            result.append(ast.dump(statement))
    return result


class _RoleStartupDiagnostics(ast.NodeTransformer):
    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        if isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name) and node.value.func.id == "print":
            return ast.Expr(value=ast.Name(id="print", ctx=ast.Load()))
        return node


def _role_generation_source(name: str, source: str) -> object:
    if name in _ROLE_GENERATORS:
        entries = _ROLE_GENERATORS[name]
        return _role_generation_python(source, entries), _role_generation_startup(source, entries)
    if name.endswith(".subagent.md"):
        header = source.split("\n---\n", 1)[0] if source.startswith("---\n") else ""
        return header, [body for title, body in h2_section_bodies(source) if title in {"入力", "出力"}]
    if name.endswith("/quality-principles-facts.md"):
        return [body for title, body in h2_section_bodies(source) if title == "結果の返却"]
    return [
        line for line in source.splitlines() if line.startswith(("起動対象: ", "<!-- input-contract:", "<!-- return-contract:"))
    ]


def _changes_role_generation(root: pathlib.Path, base: str, name: str, change: str) -> bool:
    if name not in _ROLE_GENERATORS and not (
        name.startswith("agent-toolkit/skills/atk-mode/roles/")
        and name.endswith(".subagent.md")
        or name == "agent-basics/skills/writing-standards/references/quality-principles-facts.md"
    ):
        return (
            name.startswith("agent-toolkit/skills/")
            and name.endswith(".md")
            and any(
                line[1:].startswith(("起動対象: ", "<!-- input-contract:", "<!-- return-contract:"))
                for line in change.splitlines()
                if line.startswith(("+", "-"))
            )
        )
    path = root / name
    after = path.read_text(encoding="utf-8") if path.is_file() else ""
    if "\nnew file mode " in change:
        before = ""
    else:
        result = git_command.run(["-C", str(root), "show", f"{base}:{name}"], capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise ValueError(f"役割文書の生成元の基点を取得できない: {format_cli_error(result)}")
        before = result.stdout
    return _role_generation_source(name, before) != _role_generation_source(name, after)


def cross_contracts(root: pathlib.Path, base: str) -> list[dict[str, str]]:
    if re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", base) is None:
        raise ValueError("統合先の基点には完全なcommit OIDを指定する")
    names = [os.fsdecode(name) for name in _diff(root, base, "--name-only", "-z").split(b"\0") if name]
    patch = _diff(root, base, "--unified=0").decode("utf-8", errors="replace")
    changes = re.split(r"^diff --git ", patch, flags=re.MULTILINE)[1:]
    if len(names) != len(changes):
        raise ValueError("統合候補の変更ファイルと差分の対応を確定できない")
    pyproject = root / "pyproject.toml"
    pyright_paths = (
        repository_references.pyright_literal_paths(pyproject.read_text(encoding="utf-8")) if pyproject.is_file() else []
    )
    found: set[tuple[str, str]] = set()
    for name, change in zip(names, changes, strict=True):
        path = pathlib.PurePosixPath(name)
        _, hunk_start, hunks = change.partition("\n@@")
        text = "\n".join(line[1:] for line in (hunk_start + hunks).splitlines() if line.startswith(("+", "-")))
        if path.suffix == ".md":
            found.add((_LINK_TEST, "markdown-links"))
            found.add((_TERM_TEST, "tracked-terminology"))
        if (
            name.startswith("agent-toolkit/agent_toolkit/")
            and path.suffix == ".py"
            and not _is_test(path)
            and (name.endswith(("/atk.py", "/run_script.py", "/help_text.py")) or _HELP_CHANGE.search(text))
        ):
            found.add((_HELP_TEST, "cli-registration-help"))
            found.add((_LINK_TEST, "cli-help-links"))
        if path.suffix == ".py" and not _is_test(path) and _STOP_CHANGE.search(text):
            found.add((_STOP_TEST, "subprocess-stop"))
        if name in {
            _LINK_TEST,
            _TERM_TEST,
            _STOP_TEST,
            _HELP_TEST,
            _REFERENCE_TEST,
            _HOOK_BUDGET_TEST,
            _ROLE_DOCUMENT_TEST,
        }:
            found.add((name, "cross-contract-definition"))
        if name.startswith(_TOOLKIT) and path.suffix == ".py" and not _is_test(path):
            found.add((_HOOK_BUDGET_TEST, "hook-import-budget"))
        if name in _CODEX_LAUNCH_SOURCES:
            found.update((test, "shared-codex-launch") for test in _CODEX_LAUNCH_TESTS)
        if _changes_role_generation(root, base, name, change):
            found.add((_ROLE_DOCUMENT_TEST, "role-document-generation"))
        if name in _REFERENCE_DEFINITIONS:
            found.add((_REFERENCE_TEST, "reference-check-definition"))
        if repository_references.is_reference_source(path) and repository_references.mentions_reference(text):
            found.add((_REFERENCE_TEST, "repository-references"))
        if re.search(r"^deleted file mode ", change, flags=re.MULTILINE) and repository_references.is_reference_target(
            name, pyright_paths
        ):
            found.add((_REFERENCE_TEST, "reference-target-removed"))
        if path.suffix in {".py", ".js", ".ts", ".ps1", ".sh"} and re.search(r"[^\x00-\x7f]", text):
            found.add((_TERM_TEST, "tracked-terminology"))
    return [{"test": test, "reason": reason} for test, reason in sorted(found)]


def consumer_tests(root: pathlib.Path, files: list[str], *, base: str | None = None) -> dict[str, Any]:
    """直接importは必須、文字列参照と同接頭辞は意味判定の候補にする。

    中間importや動的・設定契約は担当の探索と意味判定で補う。
    静的importで確定できる範囲と、命名・本文から得た探索候補を分け、網羅性は保証しない。
    """
    root = root.resolve()
    changed: set[pathlib.Path] = set()
    relative_files: list[str] = []
    for name in files:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(f"リポジトリ内のファイルでない: {name}")
        relative_files.append(path.relative_to(root).as_posix())
        if not _is_test(path):
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
    support = _support_references(paths, root, changed)
    hook_entries = _hook_entry_references(paths, root, changed)
    candidates: list[dict[str, Any]] = []
    for path in sorted(paths):
        if not _is_test(path):
            continue

        if not path.is_file():
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
            python = source.suffix == ".py"
            direct = python and (module in imports or namespace_module in imports)
            named = python and path.parent == source.parent and path.stem.startswith(source.stem + "_")
            reasons = []
            if direct:
                reasons.append("direct-import")
            if named:
                reasons.append("test-name")
            if python and re.search(rf"(?<!\w){re.escape(source.stem)}(?!\w)", body):
                reasons.append("text-reference")
            if source.relative_to(root).as_posix() in body:
                reasons.append("path-reference")
            elif not python and _mentions_filename(body, source.name):
                reasons.append("filename-reference")
            if imports & support.get(source, set()):
                reasons.append("test-support-reference")
            if imports & hook_entries.get(source, set()):
                reasons.append("hook-entry-reference")
            if reasons:
                candidates.append(
                    {
                        "source": source.relative_to(root).as_posix(),
                        "test": path.relative_to(root).as_posix(),
                        "required": direct,
                        "reasons": reasons,
                    }
                )
    reference_contracts = _reference_contracts(root, relative_files)
    head = git_command.run(["-C", str(root), "rev-parse", "--verify", "HEAD"], capture_output=True, text=True, timeout=60)
    if head.returncode:
        raise ValueError(f"HEADを取得できない: {format_cli_error(head)}")
    head_oid = head.stdout.strip()
    selected_cross = cross_contracts(root, base or head_oid)
    if base is None:
        selected_cross = [item for item in selected_cross if item["reason"] == "role-document-generation"]
    return {
        "head": head_oid,
        **({"base": base} if base is not None else {}),
        **({"cross_contracts": selected_cross} if base is not None or selected_cross else {}),
        "sources": sorted(source.relative_to(root).as_posix() for source in changed),
        "reference_contracts": reference_contracts,
        "candidates": candidates,
        "source_hashes": {
            source.relative_to(root).as_posix(): hashlib.sha256(source.read_bytes()).hexdigest() for source in sorted(changed)
        },
        "test_hashes": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in sorted({item["test"] for item in candidates})
        },
    }


def _reference_contracts(root: pathlib.Path, files: list[str]) -> list[dict[str, str]]:
    sources = [
        name
        for name in sorted(set(files))
        if name in _REFERENCE_DEFINITIONS
        or name == _REFERENCE_TEST
        or (
            repository_references.is_reference_source(pathlib.PurePosixPath(name))
            and repository_references.mentions_reference((root / name).read_text(encoding="utf-8"))
        )
    ]
    return [{"test": _REFERENCE_TEST, "reason": "repository-references", "source": name} for name in sources]


def _is_test(path: pathlib.PurePath) -> bool:
    return path.suffix == ".py" and (path.stem.endswith("_test") or path.name.startswith("test_"))


def _mentions_filename(body: str, name: str) -> bool:
    return re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w.-])", body) is not None


def _support_references(
    paths: set[pathlib.Path], root: pathlib.Path, changed: set[pathlib.Path]
) -> dict[pathlib.Path, set[str]]:
    sources = {source for source in changed if source.suffix != ".py"}
    if not sources:
        return {}
    result: dict[pathlib.Path, set[str]] = {source: set() for source in sources}
    for path in paths:
        if "_testing" not in path.relative_to(root).parts[:-1] or _is_test(path) or not path.is_file():
            continue
        body = path.read_text(encoding="utf-8")
        for source in sources:
            if source.relative_to(root).as_posix() in body or _mentions_filename(body, source.name):
                result[source].add(_module_name(path))
    return result


def _hook_entry_references(
    paths: set[pathlib.Path], root: pathlib.Path, changed: set[pathlib.Path]
) -> dict[pathlib.Path, set[str]]:
    hooks = root / "agent-toolkit/agent_toolkit/_hooks"
    sources = {source for source in changed if source.suffix == ".py" and source.is_relative_to(hooks)}
    result: dict[pathlib.Path, set[str]] = {source: set() for source in sources}
    for entry in (hooks / "stop.py", hooks / "pretooluse/dispatch.py"):
        if not sources or entry not in paths or not entry.is_file():
            continue
        tree = ast.parse(entry.read_text(encoding="utf-8"))
        imports = _imported_modules(tree, entry)
        if entry.name == "stop.py":
            for node in tree.body:
                if (
                    isinstance(node, ast.Assign)
                    and any(
                        isinstance(target, ast.Name) and target.id in {"CHECK_MODULE_NAMES", "CODEX_CHECK_MODULE_NAMES"}
                        for target in node.targets
                    )
                    and isinstance(node.value, ast.Tuple)
                ):
                    imports.update(
                        f"agent_toolkit._hooks.{item.value}"
                        for item in node.value.elts
                        if isinstance(item, ast.Constant) and isinstance(item.value, str)
                    )
        for source in sources:
            if _module_name(source) in imports:
                result[source].add(_module_name(entry))
    return result


def _module_name(path: pathlib.Path) -> str:
    parts = [] if path.stem == "__init__" else [path.stem]
    parent = path.parent
    while (parent / "__init__.py").is_file():
        parts.insert(0, parent.name)
        parent = parent.parent
    return ".".join(parts)


def _imported_modules(tree: ast.AST, path: pathlib.Path) -> set[str]:
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
    candidates = snapshot["candidates"]
    assert isinstance(candidates, list)
    contracts = {item["test"] for item in snapshot.get("reference_contracts", [])}
    ambiguous = {
        (item["source"], item["test"]) for item in candidates if not item["required"] and item["test"] not in contracts
    }
    decisions: dict[tuple[str, str], bool] = {}
    if review is not None:
        # 参照契約は検証対象に加えた試験からも生じるため、判定の対象と一致の比較から外す。  pyfltr: allow-comment
        if not isinstance(review, dict) or any(
            json.dumps(review.get(key), sort_keys=True) != json.dumps(value, sort_keys=True)
            for key, value in snapshot.items()
            if key != "reference_contracts"
        ):
            raise ValueError("利用試験の意味判定が現在のHEAD・ソース・候補集合と一致しない")
        rows = review.get("decisions")
        if not isinstance(rows, list):
            raise ValueError("意味判定decisionsは配列で指定する")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("source"), str) or not isinstance(row.get("test"), str):
                raise ValueError("意味判定のsourceとtestは文字列で指定する")
            key = (row["source"], row["test"])
            if row["test"] in contracts:
                continue
            if key in decisions or key not in ambiguous:
                raise ValueError("意味判定に重複または余剰の候補がある")
            if not isinstance(row.get("include"), bool) or not isinstance(row.get("reason"), str) or not row["reason"].strip():
                raise ValueError("意味判定にはincludeの真偽値と非空のreasonが必要")
            decisions[key] = row["include"]
    if decisions.keys() != ambiguous:
        raise ValueError("利用試験の曖昧候補に意味判定が不足している。候補JSONへ根拠付きdecisionsを記録する")
    return sorted(
        {item["test"] for item in candidates if item["required"] or decisions.get((item["source"], item["test"]), False)}
        | {item["test"] for item in snapshot.get("cross_contracts", [])}
        | contracts
    )


def uncovered_tests(root: pathlib.Path, tests: list[str], targets: list[str]) -> list[str]:
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
