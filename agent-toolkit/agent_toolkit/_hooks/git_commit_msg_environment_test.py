import dataclasses
import json
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import sysconfig
import venv
from collections.abc import Callable

import pytest

from agent_toolkit._common import commit_hook_environment, inherited_venv, plugin_environment
from agent_toolkit._testing.git_repository import git_output, init_repository, run_git
from agent_toolkit._testing.isolation import CONFIG_DIRECTORY_ENVIRONMENT_NAMES, HOME_ENVIRONMENT_NAMES

pytestmark = [
    pytest.mark.windows,
    pytest.mark.cpu_limit(1.0, reason="独立した配布物と2つの仮想環境を作り、実Git hookを通して結果を比較する"),
]

_SEARCH_KEYS = ("PYTHONPATH", "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV", "PATH")


def _without_msys_path_notation(values: dict[str, str | None]) -> dict[str, str | None]:
    if os.name != "nt" or not values.get("PATH"):
        return values
    entries = []
    for entry in str(values["PATH"]).split(os.pathsep):
        normalized = os.path.normcase(os.path.normpath(entry)) if entry else ""
        entries.append(
            normalized.removesuffix("\\git\\bin") + "\\git\\usr\\bin" if normalized.endswith("\\git\\bin") else normalized
        )
    return {**values, "PATH": os.pathsep.join(entries)}


@dataclasses.dataclass
class HookEnvironment:
    repository: pathlib.Path
    distribution: pathlib.Path
    runtime: pathlib.Path
    environment: dict[str, str]
    expected: dict[str, str | None]


def _install_hooks(tmp_path: pathlib.Path, caller: str, host_environment: dict[str, str]) -> HookEnvironment:
    source = pathlib.Path(__file__).resolve().parents[2]
    distribution = tmp_path / "distribution"
    shutil.copytree(
        source / "agent_toolkit",
        distribution / "agent_toolkit",
        ignore=shutil.ignore_patterns("*_test.py", "__pycache__"),
    )
    (distribution / "scripts").mkdir()
    for name in ("launch.py", "launch.py.lock"):
        shutil.copyfile(source / "scripts" / name, distribution / "scripts" / name)
    (distribution / "worktree_api.py").write_text("old = 1\n", encoding="utf-8")
    repository = init_repository(
        tmp_path / "repository",
        files={
            "src/worktree_api.py": "def added() -> int:\n    return 7\n",
            "consumer.py": "from worktree_api import added\nvalue: int = added()\n",
            "pyproject.toml": '[tool.ty.environment]\nroot = ["src"]\n',
        },
        commit_message="基点",
    )
    venv.EnvBuilder(with_pip=False).create(repository / ".venv")
    environment = dict(host_environment)
    inherited_venv.strip_inherited_venv(environment)
    environment["PATH"] = os.pathsep.join(
        directory
        for directory in os.get_exec_path(environment)
        if not (pathlib.Path(directory).parent / "pyvenv.cfg").is_file()
    )
    for name in (*HOME_ENVIRONMENT_NAMES, *CONFIG_DIRECTORY_ENVIRONMENT_NAMES):
        environment.pop(name, None)
        if name in os.environ:
            environment[name] = os.environ[name]
    for name in (*_SEARCH_KEYS[:-1], "CODEX_THREAD_ID", commit_hook_environment.CLAUDE_ACTOR_ENV):
        environment.pop(name, None)
    environment.update(
        AGENT_TOOLKIT_RUNTIME_DATA=str(tmp_path / "runtime"),
        GIT_CONFIG_PARAMETERS="'color.ui'='never'",
        UV_PYTHON_DOWNLOADS="never",
        UV_OFFLINE="true",
        PYTHONDONTWRITEBYTECODE="1",
    )
    if caller != "unset":
        environment.update(
            PYTHONPATH="" if caller == "empty" else str(repository / "src"),
            UV_PROJECT_ENVIRONMENT="" if caller == "empty" else str(repository / ".venv"),
            VIRTUAL_ENV="" if caller == "empty" else str(repository / ".venv"),
        )
    if caller == "explicit":
        environment["PATH"] = str(repository / ".venv" / "bin") + os.pathsep + environment["PATH"]
    runtime = plugin_environment.project_environment(distribution, environment)
    builder = venv.EnvBuilder(with_pip=False)
    builder.create(runtime)
    context = builder.ensure_directories(runtime)
    (pathlib.Path(context.lib_path) / "test_dependencies.pth").write_text(
        sysconfig.get_path("purelib") + "\n", encoding="utf-8"
    )
    plugin_environment.publish_prepared_environment(distribution, environment)
    hooks = tmp_path / "fixed hooks"
    search_path = os.pathsep.join(
        directory for directory in os.get_exec_path(host_environment) if pathlib.Path(directory).name != "shims"
    )
    uv = shutil.which("uv", path=search_path)
    assert uv is not None
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", str(pathlib.Path(uv).parent) + os.pathsep + os.environ["PATH"])
        commit_hook_environment.install(hooks, distribution)
    environment[commit_hook_environment.HOOKS_ENV] = str(hooks)
    environment["GIT_CONFIG_PARAMETERS"] += " " + commit_hook_environment.config_parameter(hooks)
    return HookEnvironment(repository, distribution, runtime, environment, {key: environment.get(key) for key in _SEARCH_KEYS})


def _pre_commit(case: HookEnvironment) -> None:
    repository = case.repository
    probe = repository / "probe.py"
    probe.write_text(
        "import json, os, pathlib, subprocess, sys\n"
        f"keys = {_SEARCH_KEYS!r}\n"
        "pathlib.Path('observed.json').write_text(json.dumps({k: os.environ.get(k) for k in keys}), encoding='utf-8')\n"
        f"result = subprocess.run([{str(pathlib.Path(sys.executable).parent / ('ty.exe' if os.name == 'nt' else 'ty'))!r}, "
        "'check', 'consumer.py'], check=False)\n"
        "if result.returncode: sys.exit(result.returncode)\n"
        "destination = pathlib.Path(os.environ.get('UV_PROJECT_ENVIRONMENT') or '.venv')\n"
        "destination.mkdir(exist_ok=True)\n"
        "(destination / 'checked').write_text('passed', encoding='utf-8')\n",
        encoding="utf-8",
    )
    original = repository / "original hooks"
    original.mkdir()
    python = shlex.quote(sys.executable.replace("\\", "/"))
    hook = original / "pre-commit"
    hook.write_text(f"#!/bin/sh\nexec {python} probe.py\n", encoding="utf-8")
    hook.chmod(0o755)
    run_git(repository, "config", "core.hooksPath", "original hooks")
    directory = pathlib.Path(case.environment[commit_hook_environment.HOOKS_ENV])
    original_environment = commit_hook_environment.original_environment(directory, case.environment)
    run_git(repository, "hook", "run", "pre-commit", env=original_environment)
    observed = json.loads((repository / "observed.json").read_text(encoding="utf-8"))
    assert {key: observed[key] for key in _SEARCH_KEYS[:-1]} == {key: case.expected[key] for key in _SEARCH_KEYS[:-1]}
    case.expected = observed


@pytest.mark.parametrize("caller", ["unset", "empty", "explicit"])
def test_fixed_hook_typechecks_working_source_and_preserves_caller_environment(
    tmp_path: pathlib.Path, caller: str, host_environ: Callable[[], dict[str, str]]
) -> None:
    case = _install_hooks(tmp_path, caller, host_environ())
    _pre_commit(case)
    runtime_before = {path.relative_to(case.runtime): path.read_bytes() for path in case.runtime.rglob("*") if path.is_file()}
    old_source = (case.distribution / "worktree_api.py").read_bytes()
    result = run_git(case.repository, "commit", "--allow-empty", "-m", "検証", env=case.environment, check=False)
    assert result.returncode == 0, result.stderr
    assert git_output(case.repository, "show", "-s", "--format=%s") == "検証"
    observed = _without_msys_path_notation(json.loads((case.repository / "observed.json").read_text(encoding="utf-8")))
    case.expected = _without_msys_path_notation(case.expected)
    assert observed == case.expected, {
        key: (observed[key], value) for key, value in case.expected.items() if observed[key] != value
    }
    assert (case.repository / ".venv" / "checked").read_text(encoding="utf-8") == "passed"
    assert {
        path.relative_to(case.runtime): path.read_bytes() for path in case.runtime.rglob("*") if path.is_file()
    } == runtime_before
    assert (case.distribution / "worktree_api.py").read_bytes() == old_source

    head = git_output(case.repository, "rev-parse", "HEAD")
    (case.repository / "consumer.py").write_text("from worktree_api import added\nvalue: str = added()\n", encoding="utf-8")
    run_git(case.repository, "add", "consumer.py")
    result = run_git(case.repository, "commit", "-m", "不正な型", env=case.environment, check=False)
    assert result.returncode != 0
    assert "invalid-assignment" in result.stderr
    assert git_output(case.repository, "rev-parse", "HEAD") == head


@pytest.mark.parametrize("status", [0, 42])
def test_forwarded_hook_returns_its_exit_status_after_restoring_environment(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], status: int
) -> None:
    case = _install_hooks(tmp_path, "explicit", host_environ())
    original = case.repository / ".git" / "hooks" / "post-rewrite"
    original.write_text(f"#!/bin/sh\ncat > received\nexit {status}\n", encoding="utf-8")
    original.chmod(0o755)
    directory = pathlib.Path(case.environment[commit_hook_environment.HOOKS_ENV])
    result = subprocess.run(
        ["sh", str(directory / "post-rewrite"), "amend"],
        cwd=case.repository,
        env=case.environment,
        input="before after\n",
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=20,
    )
    assert result.returncode == status, result.stderr
    assert (case.repository / "received").read_text(encoding="utf-8") == "before after\n"
