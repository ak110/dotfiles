import ast
import re
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import pytest
from agent_toolkit._testing.git_repository import commit_all, init_repository

from scripts.verification_scan_rules import SCANS, required_scan_tests, scan_files

_ROOT = Path(__file__).resolve().parents[1]
_TOOLKIT = "agent-toolkit/agent_toolkit"
_HOOKS = f"{_TOOLKIT}/_hooks"
_SKILLS = "agent-toolkit/skills"
_SCAN_METHODS = frozenset({"glob", "rglob", "iterdir", "walk", "scandir"})
_PROCESS_METHODS = frozenset({"run", "check_output", "check_call", "Popen", "run_git", "git_output"})
_SCAN_MARKERS = re.compile(r"\b(?:r?glob|iterdir|os\.(?:walk|scandir))\s*\(|ls-files")
_ROOT_LOOKUP = "find_dotfiles_root"
_COMMON_MODULE = "pytools._internal.common"
_SCOPE_DEFINITIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
_SCOPE_SCAN_NODES = frozenset({ast.Assign, ast.Call, ast.Import, ast.ImportFrom, ast.Name, *_SCOPE_DEFINITIONS})
_TEMPORARY_FIXTURES = {"tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory"}

# 初期移行の範囲外の既存呼び出しだけを除外し、同じファイルへの別の走査の追加は検出する。 pyfltr: allow-comment
_LEGACY_SCANS: dict[str, tuple[str, ...]] = {
    "agent-toolkit/agent_toolkit/_agents_server/codex_plugin_root_mcp_test.py": (
        "subprocess.run(['git', '-C', str(plugin), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], "
        "capture_output=True, check=True)",
    ),
    "agent-toolkit/agent_toolkit/_agents_server/mcp_transport_test.py": ("registry_dir.glob('*.stderr.log')",),
    "agent-toolkit/agent_toolkit/_agents_server/service_forward_test.py": (
        "root.rglob('*')",
        "(root / 'agent_toolkit').rglob('*.py')",
    ),
    "agent-toolkit/agent_toolkit/_atk/git_sync_property_test.py": ("root.rglob('*_test.py')",),
    "agent-toolkit/agent_toolkit/_atk/run_script_invariant_test.py": ("root.rglob('*.md')",),
    "agent-toolkit/agent_toolkit/script_prefix_invariant_test.py": ("scripts_dir.glob('*.py')",),
    "agent-toolkit/delegation_contract_lanes_invariant_test.py": ("plugin_root.rglob('*.md')",),
    "agent-toolkit/hooks/delegated_session_env_invariant_test.py": ("hooks_dir.rglob('*')",),
    "agent-toolkit/hooks/security_registration_invariant_test.py": ("pathlib.Path(__file__).parent.glob('*.ts*')",),
    # _sample_dependenciesの呼出し元はすべてtmp_path配下の試料だけを渡す。 pyfltr: allow-comment
    "agent-toolkit/import_cycle_invariant_test.py": ("root.rglob('*.py')",),
    "agent-toolkit/norm_placement_invariant_test.py": ("_SHARE_DIR.rglob('*.md')",),
    "agent-toolkit/plugin_resource_reference_invariant_test.py": ("root.rglob('*')",),
    "agent-toolkit/skills/atk-mode/scripts/multi_turn_trials_test.py": ("public.iterdir()",),
    "agent-toolkit/skills/skill_interfaces_invariant_test.py": (
        "Path(__file__).resolve().parent.glob('*/agents/openai.yaml')",
    ),
    "agent-toolkit/task_documents_invariant_test.py": ("_ROLES_DIR.glob('*.md')",),
    "agent_basics_standalone_invariant_test.py": ("skills.iterdir()",),
    "agent_toolkit_reference_audit_invariant_test.py": ("(root / _AUDIT_RECORDS.parent).glob('audit-records*.md')",),
    "agent_toolkit_reference_readers_invariant_test.py": ("path.glob(node.args[0].value)",),
    "install_sh_test.py": (
        "subprocess.run(['git', '-C', str(src), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], "
        "check=True, capture_output=True)",
    ),
    "libexec/update_dotfiles_upgrade_test.py": ("(source / directory).glob('*.py')",),
    "official_agent_commands_invariant_test.py": ("(_ROOT / 'bin').iterdir()", "(_ROOT / directory).rglob('*')"),
    "private_directory_invariant_test.py": ("base.rglob('*')",),
    "pytools/_internal/codex_manifest_stability_test.py": ("(root / distribution / 'skills').glob('*/SKILL.md')",),
    "retired_terms_invariant_test.py": ("(_ROOT / 'docs/guide').rglob('*.md')",),
    "scripts/check_agent_doc_tone_config_test.py": ("(repository / 'bin').iterdir()",),
}


def _tests(changes: dict[str, str]) -> set[str]:
    result = required_scan_tests(changes)
    assert len(result) == len({item["test"] for item in result})
    assert all(set(item) == {"test", "reason"} and item["reason"] for item in result)
    return {item["test"].rsplit("/", 1)[-1] for item in result}


@pytest.mark.parametrize(
    ("path", "test", "matches"),
    [
        (f"{_TOOLKIT}/_atk/agents_exit_session.py", "warning_actionability", True),
        (f"{_TOOLKIT}/_testing/example.py", "warning_actionability", False),
        (f"{_HOOKS}/new_test.py", "warning_actionability", False),
        ("pytools/_internal/new.py", "warning_actionability", True),
        ("pytools_extra/new.py", "warning_actionability", False),
        (f"{_HOOKS}/new.py", "message_language", True),
        (f"{_HOOKS}_extra/new.py", "message_language", False),
        (f"{_TOOLKIT}/hook.py", "output_contract", True),
        (f"{_TOOLKIT}/nested/hook.py", "output_contract", False),
        ("agent-toolkit/hooks/new.ts", "plugin_launch", True),
        ("agent-toolkit/hooks/nested/new.ts", "plugin_launch", False),
        ("agent-toolkit/hooks/nested/new.tsx", "warning_escape_wording", True),
        ("agent-toolkit/hooks/nested/new.test.tsx", "warning_escape_wording", False),
        ("agent-toolkit/scripts/atk", "plugin_launch", True),
        ("agent-toolkit/scripts/nested/atk", "plugin_launch", False),
        (".claude/skills/agent-toolkit-edit/references/new.md", "plugin_launch", True),
        (".claude/skills/other/references/new.md", "plugin_launch", False),
        ("libexec/test_new.py", "json_lines", False),
        ("scripts/new_test_helpers.py", "subprocess_stop", False),
        ("scripts/new.py", "subprocess_stop", True),
        (f"{_SKILLS}/tests/new.py", "subprocess_encoding", False),
        (f"{_TOOLKIT}/conftest.py", "import_cycle", False),
        ("docs/new.md", "markdown_link_anchor", True),
        ("docs/new.md", "delegation_contract", True),
        ("other-conftest.py", "test_import_resolution", True),
    ],
)
def test_scan_selection_respects_content_boundaries(path: str, test: str, matches: bool) -> None:
    assert (f"{test}_invariant_test.py" in _tests({path: "M"})) == matches


@pytest.mark.parametrize("status", ["A", "D", "T", "M"])
def test_name_and_content_changes_are_distinct(status: str) -> None:
    python_tests = _tests({"scripts/moved.py": status})
    assert ("comment_file_reference_invariant_test.py" in python_tests) == (status != "M")
    assert ("test_import_resolution_invariant_test.py" in python_tests) == (status != "M")
    assert "pylint_suppressions_invariant_test.py" in python_tests
    fixture_tests = _tests({"scripts/unexpected_conftest.py": status})
    assert ("pytest_fixture_registration_invariant_test.py" in fixture_tests) == (status != "M")
    assert "pytest_fixture_registration_invariant_test.py" in _tests({"agent-toolkit/conftest.py": status})


def test_renames_include_both_old_and_current_path() -> None:
    assert "warning_actionability_invariant_test.py" in _tests({f"{_HOOKS}/removed.py": "D", "docs/removed.py": "A"})
    assert "warning_actionability_invariant_test.py" in _tests({"docs/new.py": "D", f"{_HOOKS}/new.py": "A"})
    assert not _tests({"README.unrelated": "M"})
    assert not _tests({})


def test_deleted_test_is_not_requested_for_execution() -> None:
    name = "warning_actionability_invariant_test.py"
    assert name not in _tests({f"{_TOOLKIT}/{name}": "D", f"{_HOOKS}/change.py": "M"})


def test_changed_tests_also_select_the_scan_guard() -> None:
    assert "verification_scan_rules_test.py" in _tests({"scripts/test_new.py": "A"})
    assert "verification_scan_rules_test.py" not in _tests({"agent-toolkit-codex/agent_toolkit/new_test.py": "A"})


def test_shared_enumeration_reads_the_declared_files(tmp_path: Path) -> None:
    hook_paths = {name: "" for name in (f"{_HOOKS}/in.py", f"{_HOOKS}/sub/in.py", f"{_HOOKS}/sub/in_test.py")}
    repository = init_repository(
        tmp_path / "repo",
        files={
            **hook_paths,
            f"{_SKILLS}/tracked.md": "",
            f"{_SKILLS}/removed.md": "",
            f"{_SKILLS}/declared-delivery-1.md": "",
            f"{_SKILLS}/not-delivery-1a.md": "",
            "scripts/external.py": "",
            "docs/example.md": "",
        },
    )
    commit_all(repository, "追跡する走査対象")
    (repository / _SKILLS / "removed.md").unlink()
    (repository / _SKILLS / "untracked.md").write_text("", encoding="utf-8")
    (repository / f"{_HOOKS}/untracked.py").write_text("", encoding="utf-8")

    def files(test: str, kind: str = "source") -> set[str]:
        return {path.relative_to(repository).as_posix() for path in scan_files(str(_ROOT / test), kind, repository=repository)}

    assert files(f"{_HOOKS}/message_language_invariant_test.py") == {
        f"{_HOOKS}/in.py",
        f"{_HOOKS}/sub/in.py",
        f"{_HOOKS}/untracked.py",
    }
    assert files("agent-toolkit/comment_file_reference_invariant_test.py", "names") == {*hook_paths, "scripts/external.py"}
    assert files("agent_basics_standalone_invariant_test.py") == {
        f"{_SKILLS}/tracked.md",
        f"{_SKILLS}/untracked.md",
        f"{_SKILLS}/declared-delivery-1.md",
        f"{_SKILLS}/not-delivery-1a.md",
    }
    assert files("agent_basics_standalone_invariant_test.py", "names") == {f"{_SKILLS}/declared-delivery-1.md"}
    assert files("markdown_link_anchor_invariant_test.py") == {
        "docs/example.md",
        f"{_SKILLS}/tracked.md",
        f"{_SKILLS}/declared-delivery-1.md",
        f"{_SKILLS}/not-delivery-1a.md",
    }


def _root_lookups(nodes: Sequence[ast.AST]) -> set[str]:
    lookups = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.level == 0:
            for alias in node.names:
                if node.module == _COMMON_MODULE and alias.name == _ROOT_LOOKUP:
                    lookups.add(alias.asname or alias.name)
                elif node.module == "pytools._internal" and alias.name == "common":
                    lookups.add(f"{alias.asname or alias.name}.{_ROOT_LOOKUP}")
        elif isinstance(node, ast.Import):
            lookups.update(
                f"{alias.asname or alias.name}.{_ROOT_LOOKUP}" for alias in node.names if alias.name == _COMMON_MODULE
            )
    return lookups


def _path_inputs(node: ast.AST, lookups: set[str]) -> set[str]:
    inputs = set()
    for part in ast.walk(node):
        if isinstance(part, ast.Name):
            inputs.add(part.id)
        elif isinstance(part, ast.Call) and (
            (isinstance(part.func, ast.Name) and part.func.id in lookups)
            or (isinstance(part.func, ast.Attribute) and part.func.attr == _ROOT_LOOKUP and ast.unparse(part.func) in lookups)
        ):
            inputs.add(_COMMON_MODULE)
    return inputs


def _aliases(bindings: Sequence[tuple[str, set[str]]], names: set[str]) -> set[str]:
    while new := {name for name, inputs in bindings if inputs & names} - names:
        names |= new
    return names


def _scope_nodes(body: Sequence[ast.stmt]) -> list[ast.AST]:
    nodes: list[ast.AST] = []
    pending: list[ast.AST] = list(reversed(body))
    while pending:
        node = pending.pop()
        if type(node) in _SCOPE_SCAN_NODES and not (isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)):
            nodes.append(node)
        for field in reversed(node._fields):
            if field == "body" and isinstance(node, _SCOPE_DEFINITIONS):
                continue
            value = getattr(node, field, None)
            if isinstance(value, list):
                pending.extend(child for child in reversed(value) if isinstance(child, ast.AST))
            elif isinstance(value, ast.AST):
                pending.append(value)
    return nodes


def _scope_inputs(
    nodes: Sequence[ast.AST], repository: set[str], temporary: set[str], lookups: set[str], parameters: set[str]
) -> tuple[set[str], set[str], set[str]]:
    shadowed = parameters | {node.id for node in nodes if isinstance(node, ast.Name) and not isinstance(node.ctx, ast.Load)}
    shadowed.update(node.name for node in nodes if isinstance(node, _SCOPE_DEFINITIONS))
    shadowed.update(
        alias.asname or alias.name.partition(".")[0]
        for node in nodes
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    )
    lookups = {lookup for lookup in lookups if lookup.partition(".")[0] not in shadowed} | _root_lookups(nodes)
    bindings = [
        (target.id, _path_inputs(node.value, lookups))
        for node in nodes
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    ]
    return (
        _aliases(bindings, repository - shadowed),
        _aliases(bindings, (temporary - shadowed) | (parameters & _TEMPORARY_FIXTURES)),
        lookups,
    )


def _scan_calls(
    nodes: Sequence[ast.AST], repository: set[str], temporary: set[str], lookups: set[str], *, invariant: bool
) -> list[str]:
    raw = []
    for node in nodes:
        if not isinstance(node, ast.Call):
            continue
        call = node.func
        name = call.attr if isinstance(call, ast.Attribute) else call.id if isinstance(call, ast.Name) else ""
        git_listing = name in _PROCESS_METHODS and any(
            isinstance(arg, ast.List | ast.Tuple)
            and any(isinstance(part, ast.Constant) and part.value == "ls-files" for part in arg.elts)
            for arg in node.args
        )
        if isinstance(call, ast.Attribute) and name in _SCAN_METHODS:
            inputs = _path_inputs(call.value, lookups)
            if name in {"walk", "scandir"} and "os" not in inputs:
                continue
            if name in {"walk", "scandir", "glob"} and node.args:
                inputs |= _path_inputs(node.args[0], lookups)
        elif git_listing:
            inputs = _path_inputs(node, lookups)
        else:
            continue
        if inputs & repository or ((invariant or git_listing) and not inputs & temporary):
            raw.append(ast.unparse(node))
    return raw


def _raw_scans(tree: ast.Module, *, invariant: bool) -> list[str]:
    raw = []
    pending = [(tree.body, {"__file__", _COMMON_MODULE}, set[str](), set[str](), set[str](), False)]
    while pending:
        body, parent_repo, parent_temp, parent_lookups, parameters, class_scope = pending.pop()
        nodes = _scope_nodes(body)
        repository, temporary, lookups = _scope_inputs(nodes, parent_repo, parent_temp, parent_lookups, parameters)
        raw.extend(_scan_calls(nodes, repository, temporary, lookups, invariant=invariant))
        # メソッドが継承するのはクラス本体の束縛でなく、その外側のスコープである。 pyfltr: allow-comment
        outer = (parent_repo, parent_temp, parent_lookups) if class_scope else (repository, temporary, lookups)
        for node in nodes:
            if isinstance(node, ast.ClassDef):
                pending.append((node.body, *outer, set(), True))
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                args = {arg.arg for arg in ast.iter_child_nodes(node.args) if isinstance(arg, ast.arg)}
                pending.append((node.body, *outer, args, False))
    return raw


def test_scan_guard_distinguishes_repository_and_temporary_scans() -> None:
    source = """
from pathlib import Path

ROOT = Path(__file__).resolve().parent

def test_repository():
    root = ROOT / "docs"
    return list(root.rglob("*.md"))

def test_tracked():
    return git.run(["ls-files"])

def test_fixture(tmp_path):
    sample = tmp_path / "sample"
    return list(sample.rglob("*.py")), git.run(["ls-files"], cwd=sample)
"""
    expected = {"root.rglob('*.md')", "git.run(['ls-files'])"}
    tree = ast.parse(source)
    assert set(_raw_scans(tree, invariant=True)) == expected
    assert set(_raw_scans(tree, invariant=False)) == expected


def _has_raw_markers(source: str, *, invariant: bool) -> bool:
    return bool(
        (invariant or "__file__" in source or "ls-files" in source or _ROOT_LOOKUP in source) and _SCAN_MARKERS.search(source)
    )


@pytest.mark.parametrize(
    ("imports", "lookup", "reports"),
    [
        ("from pytools._internal.common import find_dotfiles_root", "find_dotfiles_root", True),
        ("from pytools._internal.common import find_dotfiles_root as base", "base", True),
        ("from pytools._internal import common as base", "base.find_dotfiles_root", True),
        ("import pytools._internal.common", "pytools._internal.common.find_dotfiles_root", True),
        ("import pytools._internal.common as base", "base.find_dotfiles_root", True),
        ("from fixture_example import find_dotfiles_root", "find_dotfiles_root", False),
    ],
)
def test_scan_guard_recognizes_shared_repository_root(imports: str, lookup: str, reports: bool) -> None:
    source = f"""
{imports}

def test_lookup(tmp_path):
    base_path = {lookup}()
    docs = base_path / "docs"
    docs.rglob("*.md")
    tmp_path.rglob("*.md")
"""
    scans = _raw_scans(ast.parse(source), invariant=False) if _has_raw_markers(source, invariant=False) else []
    assert scans == (["docs.rglob('*.md')"] if reports else [])


@pytest.mark.parametrize("fixture_name", ["root", "sample"])
def test_scan_guard_keeps_fixture_bindings_local(fixture_name: str) -> None:
    source = f"""
from pytools._internal.common import find_dotfiles_root

def test_known_file():
    root = find_dotfiles_root()
    assert (root / "README.md").is_file()

def test_fixture(tmp_path):
    {fixture_name} = tmp_path / "sample"
    assert not list({fixture_name}.rglob("*.md"))
"""
    assert _has_raw_markers(source, invariant=False)
    assert not _raw_scans(ast.parse(source), invariant=False)
    positive = source.replace('(root / "README.md").is_file()', 'list(root.rglob("*.md"))')
    assert _raw_scans(ast.parse(positive), invariant=False) == ["root.rglob('*.md')"]


def test_scan_guard_inherits_only_unshadowed_root_bindings() -> None:
    source = """
from pytools._internal.common import find_dotfiles_root

root = find_dotfiles_root()

def test_repo():
    return list(root.rglob("*.md"))

def test_fixture(tmp_path):
    root = tmp_path / "sample"
    return list(root.rglob("*.py"))

def test_helper(find_dotfiles_root):
    sample = find_dotfiles_root()
    return list(sample.rglob("*.rst"))
"""
    assert _raw_scans(ast.parse(source), invariant=False) == ["root.rglob('*.md')"]


@pytest.mark.cpu_limit(2.0, reason="追跡試験と未追跡の新規試験を読み、リポジトリ走査と宣言のずれを検出する")
def test_repository_scans_use_shared_declarations() -> None:
    violations = []
    registered = set()
    for path in scan_files(__file__):
        name = path.relative_to(_ROOT).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        invariant = path.name.endswith("_invariant_test.py")
        has_raw_markers = _has_raw_markers(source, invariant=invariant)
        has_shared_marker = "scan_files" in source
        if not (has_raw_markers or has_shared_marker):
            continue
        tree = ast.parse(source)
        raw = Counter(_raw_scans(tree, invariant=invariant)) if has_raw_markers else Counter()
        for expression, count in (raw - Counter(_LEGACY_SCANS.get(name, ()))).items():
            violations.append(f"{name}: {expression} ({count})")
        if has_shared_marker:
            calls = {
                str(node.args[1].value) if len(node.args) > 1 and isinstance(node.args[1], ast.Constant) else "source"
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "scan_files"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "__file__"
            }
            if calls:
                registered.add(name)
                for kind in sorted(calls - SCANS.get(name, {}).keys()):
                    violations.append(f"{name}: 宣言がない走査: {kind}")
    assert not violations, "走査範囲の宣言を通らない列挙:\n" + "\n".join(violations)
    assert registered == SCANS.keys()
