import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Literal

from agent_toolkit._git import command as git_command

from pytools._internal.common import find_dotfiles_root

_TOOLKIT = "agent-toolkit/agent_toolkit"
_HOOKS = f"{_TOOLKIT}/_hooks"
_ROLES = "agent-toolkit/skills/atk-mode/roles"
_DOC_ROOTS = ("agent-basics", "agent-toolkit", ".claude/skills")
_NAME_CHANGES = frozenset({"A", "D", "T"})
BASICS_SEARCH_SKILLS = ("search", "secret-files")
ScanMode = Literal["tree", "tracked", "tracked-and-untracked"]


@dataclass(frozen=True)
class Scan:
    roots: tuple[str, ...] = ("",)
    names: tuple[str, ...] = ("*",)
    excluded_names: tuple[str, ...] = ()
    excluded_directories: tuple[str, ...] = ()
    recursive: bool = True
    mode: ScanMode = "tree"
    names_only: bool = False
    module_entries: bool = False
    name_regex: str = ""

    def matches(self, path: str) -> bool:
        parent, _, name = path.rpartition("/")
        if not any(parent == root or (self.recursive and (not root or parent.startswith(root + "/"))) for root in self.roots):
            return False
        if self.excluded_directories and any(part in self.excluded_directories for part in parent.split("/")):
            return False
        if self.module_entries:
            # 任意の子ファイルの追加・削除で名前空間パッケージ用のディレクトリも増減しうる。 pyfltr: allow-comment
            return (name.endswith(".py") and name[:-3].isidentifier()) or any(part.isidentifier() for part in parent.split("/"))
        return (
            (not self.name_regex or re.fullmatch(self.name_regex, name) is not None)
            and any(fnmatchcase(name, pattern) for pattern in self.names)
            and not any(fnmatchcase(name, pattern) for pattern in self.excluded_names)
        )

    def files(self, repository: Path) -> list[Path]:
        if self.mode == "tree":
            candidates = (
                path
                for root in self.roots
                for name in self.names
                for path in (repository / root).glob(("**/" if self.recursive else "") + name)
            )
        else:
            args = ["ls-files", "-z"]
            if self.mode == "tracked-and-untracked":
                args.extend(("--cached", "--others", "--exclude-standard"))
            pathspecs = [
                ":(glob)" + (root + "/" if root else "") + ("**/" if self.recursive else "") + name
                for root in self.roots
                for name in self.names
            ]
            listed = git_command.run(
                [*args, "--", *pathspecs],
                repository,
                capture_output=True,
                check=True,
                recover_stale_lock=False,
            ).stdout
            candidates = (repository / os.fsdecode(name) for name in listed.split(b"\0") if name)
        return sorted({path for path in candidates if self.matches(path.relative_to(repository).as_posix()) and path.is_file()})


def _python(*roots: str, excluded_directories: tuple[str, ...] = ()) -> Scan:
    return Scan(roots, ("*.py",), ("*_test.py",), excluded_directories)


SCANS: dict[str, dict[str, Scan]] = {
    f"{_TOOLKIT}/_atk/wi/frontmatter_key_inventory_invariant_test.py": {"source": _python(f"{_TOOLKIT}/_atk/wi")},
    f"{_HOOKS}/block_hooks_invariant_test.py": {"source": _python(_TOOLKIT)},
    f"{_HOOKS}/codex_record_copy_invariant_test.py": {"source": _python(_HOOKS)},
    f"{_HOOKS}/message_language_invariant_test.py": {"source": _python(_HOOKS)},
    f"{_HOOKS}/output_contract_invariant_test.py": {
        "source": _python(_HOOKS),
        "entry": Scan((_TOOLKIT,), ("hook.py",), recursive=False),
    },
    f"{_HOOKS}/warning_escape_wording_invariant_test.py": {
        "source": Scan(
            (_HOOKS, f"{_TOOLKIT}/_agents_server", "agent-toolkit/hooks"),
            ("*.py", "*.ts", "*.tsx"),
            ("*_test.py", "*.test.ts", "*.test.tsx"),
        ),
    },
    f"{_TOOLKIT}/warning_actionability_invariant_test.py": {
        "source": _python(_TOOLKIT, "pytools", excluded_directories=("_testing",)),
    },
    "agent-toolkit/comment_file_reference_invariant_test.py": {
        "source": Scan(("agent-toolkit",), ("*.py",), mode="tracked"),
        "names": Scan(names=("*.py",), mode="tracked", names_only=True),
    },
    "agent-toolkit/delegation_contract_invariant_test.py": {
        "source": Scan(names=("*.md",), mode="tracked"),
        "launch-contracts": Scan(("agent-toolkit/skills",), ("*.md",)),
        "parents": Scan((_ROLES,), ("*.parent.md",), recursive=False),
        "recipients": Scan((_ROLES,), ("*.subagent.md",), recursive=False),
    },
    "agent-toolkit/hooks/plugin_launch_invariant_test.py": {
        "source": _python(_TOOLKIT),
        "hooks": Scan(("agent-toolkit/hooks",), ("*.ts", "*.tsx", "*.json"), ("*.test.ts", "*.test.tsx"), recursive=False),
        "scripts": Scan(("agent-toolkit/scripts",), recursive=False),
        "editing-skill": Scan((".claude/skills/agent-toolkit-edit",), ("*.md",)),
    },
    "agent-toolkit/import_cycle_invariant_test.py": {"source": Scan((_TOOLKIT,), ("*.py",), ("*_test.py", "conftest.py"))},
    "agent-toolkit/json_lines_invariant_test.py": {
        "source": Scan(("",), ("*.py",), ("*_test.py", "test_*.py", "conftest.py"), ("_testing", "tests"), mode="tracked"),
    },
    "agent-toolkit/session_environment_invariant_test.py": {
        "source": _python(_TOOLKIT, excluded_directories=("_testing",)),
    },
    "agent-toolkit/subprocess_encoding_invariant_test.py": {
        "source": _python(_TOOLKIT, "agent-toolkit/scripts", "agent-toolkit/skills", excluded_directories=("tests",)),
    },
    "agent_basics_standalone_invariant_test.py": {
        "source": Scan(_DOC_ROOTS, ("*.md",), mode="tracked-and-untracked"),
        "distributed": Scan(("agent-basics/skills",), ("*.md",)),
        "names": Scan(
            _DOC_ROOTS, ("*-delivery-*.md",), mode="tracked-and-untracked", names_only=True, name_regex=r".*-delivery-\d+\.md"
        ),
        "search-skills": Scan(tuple(f"agent-toolkit/skills/{skill}" for skill in BASICS_SEARCH_SKILLS), names_only=True),
    },
    "markdown_link_anchor_invariant_test.py": {"source": Scan(names=("*.md",), mode="tracked")},
    "pylint_suppressions_invariant_test.py": {"source": Scan(names=("*.py",), mode="tracked")},
    "pytest_fixture_registration_invariant_test.py": {
        "names": Scan(names=("*conftest.py",), mode="tracked-and-untracked", names_only=True),
        "source": Scan(("", "agent-toolkit"), ("conftest.py",), recursive=False),
    },
    "subprocess_stop_invariant_test.py": {
        "source": Scan(
            ("agent-toolkit", "pytools", "libexec", "scripts"),
            ("*.py",),
            ("*_test.py", "*_test_helpers.py"),
            ("_testing",),
            mode="tracked",
        ),
    },
    "test_import_resolution_invariant_test.py": {
        "source": Scan(names=("*_test.py", "*conftest.py"), mode="tracked"),
        "imports": Scan(names_only=True, module_entries=True),
    },
    "scripts/verification_scan_rules_test.py": {
        "source": Scan(
            names=("*_test.py", "test_*.py"), excluded_directories=("agent-toolkit-codex",), mode="tracked-and-untracked"
        ),
    },
}


def scan_files(test_file: str, kind: str = "source", *, repository: Path | None = None) -> list[Path]:
    root = find_dotfiles_root()
    if root is None:
        raise ValueError("走査範囲を使うdotfiles作業ツリーを取得できない")
    test = Path(test_file).resolve().relative_to(root).as_posix()
    return SCANS[test][kind].files(root if repository is None else repository)


def local_import_exists(directory: Path, name: str) -> bool:
    return (directory / f"{name}.py").is_file() or (directory / name).is_dir()


def required_scan_tests(changes: Mapping[str, str]) -> list[dict[str, str]]:
    # 合成結果に存在しない削除・改名前のパスも、ファイルシステムを参照せずに判定する。 pyfltr: allow-comment
    required = []
    ordered = sorted(changes.items())
    for test, scopes in sorted(SCANS.items()):
        if changes.get(test) == "D":
            continue
        for path, status in ordered:
            matched = [
                scope for scope in scopes.values() if (not scope.names_only or status in _NAME_CHANGES) and scope.matches(path)
            ]
            if matched:
                what = "内容" if any(not scope.names_only for scope in matched) else "名前・存在"
                required.append({"test": test, "reason": f"走査対象の{what}の変更: {path} ({status})"})
                break
    return required
