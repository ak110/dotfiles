import pathlib
import re
import tomllib
from collections.abc import Sequence

PLUGIN_PREFIX = "agent-" + "toolkit"
SOURCE_SUFFIXES = frozenset({".json", ".md", ".py"})
_BOUNDARY = r"(?<![A-Za-z0-9_:-])"

SKILL_INVOCATION_PATTERN = re.compile(rf"{_BOUNDARY}{PLUGIN_PREFIX}:([A-Za-z0-9][A-Za-z0-9_-]*)")
SKILL_PATH_PATTERN = re.compile(rf"{_BOUNDARY}{PLUGIN_PREFIX}/skills/[A-Za-z0-9][A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*/?")
QUALIFIED_RESOURCE_PATTERN = re.compile(
    rf"{_BOUNDARY}{PLUGIN_PREFIX}:(/?[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+\.md)(?![A-Za-z0-9_./-])"
)
PYTHON_PATH_PATTERN = re.compile(
    rf"{_BOUNDARY}{PLUGIN_PREFIX}/(?:scripts|agent_toolkit)/" r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\.py"
)
QUALIFIED_RESOURCE_TARGET_PATTERN = re.compile(rf"{PLUGIN_PREFIX}/[A-Za-z0-9_-]+/[A-Za-z0-9_./-]+\.md")
_PATTERNS = (SKILL_INVOCATION_PATTERN, SKILL_PATH_PATTERN, QUALIFIED_RESOURCE_PATTERN, PYTHON_PATH_PATTERN)
_TARGET_PATTERNS = (SKILL_PATH_PATTERN, PYTHON_PATH_PATTERN, QUALIFIED_RESOURCE_TARGET_PATTERN)


def is_reference_source(path: pathlib.PurePath) -> bool:
    return path.suffix in SOURCE_SUFFIXES


def mentions_reference(text: str) -> bool:
    return any(pattern.search(text) for pattern in _PATTERNS)


def is_reference_target(path: str, pyright_paths: Sequence[tuple[str, str]] = ()) -> bool:
    target = pathlib.PurePosixPath(path)
    return any(pattern.fullmatch(path) for pattern in _TARGET_PATTERNS) or any(
        target.is_relative_to(pathlib.PurePosixPath(value)) for _, value in pyright_paths
    )


def pyright_literal_paths(pyproject_text: str) -> list[tuple[str, str]]:
    config = tomllib.loads(pyproject_text).get("tool", {}).get("pyright", {})
    paths: list[tuple[str, str]] = []
    for key in ("exclude", "ignore"):
        for value in config.get(key, []):
            path = pathlib.PurePosixPath(value)
            if path.is_absolute() or ".." in path.parts or any(mark in value for mark in "*?[]{}\\:"):
                continue
            paths.append((key, value))
    return paths
