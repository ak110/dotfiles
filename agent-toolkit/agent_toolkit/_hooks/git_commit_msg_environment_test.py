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

from agent_toolkit._common import commit_hook_environment, host_homes, inherited_venv, plugin_environment
from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer
from agent_toolkit._hooks.pretooluse import commit_identity, dispatch
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


def _copy_distribution(distribution: pathlib.Path) -> None:
    source = pathlib.Path(__file__).resolve().parents[2]
    shutil.copytree(
        source / "agent_toolkit",
        distribution / "agent_toolkit",
        ignore=shutil.ignore_patterns("*_test.py", "__pycache__"),
    )
    (distribution / "scripts").mkdir()
    for name in ("launch.py", "launch.py.lock"):
        shutil.copyfile(source / "scripts" / name, distribution / "scripts" / name)


def _install_hooks(tmp_path: pathlib.Path, caller: str, host_environment: dict[str, str]) -> HookEnvironment:
    distribution = tmp_path / "distribution"
    _copy_distribution(distribution)
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
    environment = _hook_environment(tmp_path, host_environment)
    if caller != "unset":
        environment.update(
            PYTHONPATH="" if caller == "empty" else str(repository / "src"),
            UV_PROJECT_ENVIRONMENT="" if caller == "empty" else str(repository / ".venv"),
            VIRTUAL_ENV="" if caller == "empty" else str(repository / ".venv"),
        )
    if caller == "explicit":
        environment["PATH"] = str(repository / ".venv" / "bin") + os.pathsep + environment["PATH"]
    runtime = _prepare_runtime(distribution, environment)
    hooks = tmp_path / "fixed hooks"
    uv = _uv_path(host_environment)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", str(uv.parent) + os.pathsep + os.environ["PATH"])
        commit_hook_environment.install(hooks, distribution)
    environment[commit_hook_environment.HOOKS_ENV] = str(hooks)
    environment["GIT_CONFIG_PARAMETERS"] += " " + commit_hook_environment.config_parameter(hooks)
    return HookEnvironment(repository, distribution, runtime, environment, {key: environment.get(key) for key in _SEARCH_KEYS})


def _hook_environment(tmp_path: pathlib.Path, host_environment: dict[str, str]) -> dict[str, str]:
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
    return environment


def _uv_path(environment: dict[str, str]) -> pathlib.Path:
    search_path = os.pathsep.join(
        directory for directory in os.get_exec_path(environment) if pathlib.Path(directory).name != "shims"
    )
    uv = shutil.which("uv", path=search_path)
    assert uv is not None
    return pathlib.Path(uv)


def _prepare_runtime(distribution: pathlib.Path, environment: dict[str, str]) -> pathlib.Path:
    runtime = plugin_environment.project_environment(distribution, environment)
    builder = venv.EnvBuilder(with_pip=False)
    builder.create(runtime)
    context = builder.ensure_directories(runtime)
    (pathlib.Path(context.lib_path) / "test_dependencies.pth").write_text(
        sysconfig.get_path("purelib") + "\n", encoding="utf-8"
    )
    plugin_environment.publish_prepared_environment(distribution, environment)
    return runtime


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


def _start_claude_session(root: pathlib.Path, env_file: pathlib.Path, environment: dict[str, str], *, actor: str) -> None:
    target = root / "agent_toolkit/_hooks/git_commit_msg.py"
    text = target.read_text(encoding="utf-8")
    entrypoint = 'if __name__ == "__main__":\n'
    probe_source = f"    import sys\n    if sys.argv[2] == 'commit-msg':\n        print({root.name!r}, file=sys.stderr)\n"
    target.write_text(text.replace(entrypoint, entrypoint + probe_source), encoding="utf-8")
    probe = root / "session_start.py"
    probe.write_text(
        "import json, sys\n"
        "from agent_toolkit._hooks.pretooluse.commit_identity import persist_claude_commit_actor\n"
        "persist_claude_commit_actor(json.load(sys.stdin))\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(root / "scripts/launch.py"), "--persist-claude-env", probe.name],
        cwd=env_file.parent,
        env={**environment, "CLAUDE_ENV_FILE": str(env_file)},
        input=json.dumps({"session_id": actor, "model": "claude-opus-5-5", "effort": "low"}),
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


def _session_git(
    repository: pathlib.Path,
    env_file: pathlib.Path,
    environment: dict[str, str],
    *arguments: str,
    command: str = 'exec git "$@"',
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", "-c", '. "$1"; shift; ' + command, "sh", env_file.as_posix(), *arguments],
        cwd=repository,
        env=environment,
        capture_output=True,
        encoding="utf-8",
        check=False,
        timeout=30,
    )


@dataclasses.dataclass(frozen=True)
class ClaudeSession:
    repository: pathlib.Path
    distribution: pathlib.Path
    env_file: pathlib.Path
    environment: dict[str, str]
    commit: tuple[str, ...]
    trailer: str


@pytest.fixture(name="claude_session")
def _claude_session(
    tmp_path: pathlib.Path, host_environ: Callable[[], dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> ClaudeSession:
    host = host_environ()
    environment = _hook_environment(tmp_path, host)
    environment["PATH"] = str(_uv_path(host).parent) + os.pathsep + environment["PATH"]
    repo_id = "example.test/team/git-hook-regression"
    repository = init_repository(tmp_path / "repo", origin=f"https://{repo_id}", commit_message="基点")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps({repo_id: {"edit": "allow"}}))
    original = repository / "local hooks"
    original.mkdir()
    run_git(repository, "config", "core.hooksPath", "local hooks")
    hook = original / "pre-commit"
    hook.write_text(
        "#!/bin/sh\n"
        '[ "$(git config --get core.hooksPath)" = "local hooks" ] && [ "$(git config --get color.ui)" = never ] || exit 42\n'
        '[ -z "${AGENT_TOOLKIT_COMMIT_CLAUDE_ROOT+x}${AGENT_TOOLKIT_COMMIT_CLAUDE_UV+x}" ] || exit 43\n'
        "if [ -f reject ]; then echo local-hook-rejected >&2; exit 41; fi\n"
        "echo local-hook >&2\n",
        encoding="utf-8",
        newline="\n",
    )
    hook.chmod(0o755)
    assert not commit_identity.commit_hook_installed(is_codex=False)

    stable = tmp_path / "stable root"
    _copy_distribution(stable)
    _prepare_runtime(stable, environment)
    stable_env = tmp_path / "stable.env"
    _start_claude_session(stable, stable_env, environment, actor="stable")
    assert commit_identity.commit_hook_installed(is_codex=False)
    trailer = co_author_trailer(RuntimeIdentity("claude", "claude-opus-5-5", "low", "observed"))
    return ClaudeSession(
        repository, stable, stable_env, environment, ("commit", "--allow-empty", "-m", "試験", "-m", trailer), trailer
    )


def _install_legacy_hosts(session: ClaudeSession) -> tuple[pathlib.Path, pathlib.Path]:
    home = pathlib.Path.home()
    legacy = host_homes.commit_hook_directory(home, "claude")
    codex = host_homes.commit_hook_directory(home, "codex")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", session.environment["PATH"])
        for directory in (legacy, codex):
            commit_hook_environment.install(directory, session.distribution)
    return legacy, codex


def _start_temporary_session(tmp_path: pathlib.Path, session: ClaudeSession) -> tuple[pathlib.Path, pathlib.Path]:
    temporary = tmp_path / "temporary root's"
    temporary_env = tmp_path / "temporary.env"
    _copy_distribution(temporary)
    _start_claude_session(temporary, temporary_env, session.environment, actor="temporary")
    return temporary, temporary_env


def _assert_session_commit(result: subprocess.CompletedProcess[str], root: pathlib.Path) -> None:
    assert result.returncode == 0, result.stderr
    output = result.stderr.splitlines()
    assert output.count("local-hook") == 1
    assert output.count(root.name) == 1
    assert output.index("local-hook") < output.index(root.name)


def test_temporary_claude_distribution_does_not_replace_other_sessions_hooks(
    tmp_path: pathlib.Path, claude_session: ClaudeSession
) -> None:
    session = claude_session
    before = _session_git(session.repository, session.env_file, session.environment, *session.commit)
    _assert_session_commit(before, session.distribution)

    legacy, _ = _install_legacy_hosts(session)
    legacy_before = {path.name: path.read_bytes() for path in legacy.iterdir() if path.is_file()}

    temporary, temporary_env = _start_temporary_session(tmp_path, session)
    during = _session_git(session.repository, session.env_file, session.environment, *session.commit)
    _assert_session_commit(during, session.distribution)
    temp_commit = _session_git(session.repository, temporary_env, session.environment, *session.commit)
    _assert_session_commit(temp_commit, temporary)

    shutil.rmtree(temporary)
    assert not temporary.exists()
    after = _session_git(session.repository, session.env_file, session.environment, *session.commit)
    _assert_session_commit(after, session.distribution)
    assert {path.name: path.read_bytes() for path in legacy.iterdir() if path.is_file()} == legacy_before

    head = git_output(session.repository, "rev-parse", "HEAD")
    quoted_temporary = shlex.quote(temporary.as_posix())
    missing_root = _session_git(
        session.repository,
        session.env_file,
        session.environment,
        *session.commit,
        command=f'export AGENT_TOOLKIT_COMMIT_CLAUDE_ROOT={quoted_temporary}; exec git "$@"',
    )
    assert missing_root.returncode != 0 and "local-hook" not in missing_root.stdout + missing_root.stderr
    assert git_output(session.repository, "rev-parse", "HEAD") == head


def test_claude_session_hooks_preserve_original_and_legacy_hooks(tmp_path: pathlib.Path, claude_session: ClaudeSession) -> None:
    session = claude_session
    legacy, codex = _install_legacy_hosts(session)
    temporary, temporary_env = _start_temporary_session(tmp_path, session)

    head = git_output(session.repository, "rev-parse", "HEAD")
    bad_commit = ("commit", "--allow-empty", "-m", "帰属がない")
    blocked = _session_git(session.repository, temporary_env, session.environment, *bad_commit)
    assert blocked.returncode != 0 and session.trailer in blocked.stderr
    assert git_output(session.repository, "rev-parse", "HEAD") == head

    shutil.rmtree(temporary)

    old_env = {
        **session.environment,
        commit_hook_environment.CLAUDE_ACTOR_ENV: "stable",
        commit_hook_environment.HOOKS_ENV: legacy.as_posix(),
        "GIT_CONFIG_PARAMETERS": session.environment["GIT_CONFIG_PARAMETERS"]
        + " "
        + commit_hook_environment.config_parameter(legacy),
    }
    old_commit = run_git(session.repository, *session.commit, env=old_env, check=False)
    _assert_session_commit(old_commit, session.distribution)

    inherited = {
        **session.environment,
        "GIT_CONFIG_PARAMETERS": " ".join(
            (
                session.environment["GIT_CONFIG_PARAMETERS"],
                *(commit_hook_environment.config_parameter(d) for d in (legacy, codex)),
            )
        ),
    }
    nested = _session_git(session.repository, session.env_file, inherited, *session.commit)
    _assert_session_commit(nested, session.distribution)
    assert git_output(session.repository, "config", "--get", "core.hooksPath") == "local hooks"

    head = git_output(session.repository, "rev-parse", "HEAD")
    reject = session.repository / "reject"
    reject.touch()
    rejected = _session_git(session.repository, session.env_file, session.environment, *session.commit)
    assert rejected.returncode != 0 and "local-hook-rejected" in rejected.stderr
    assert session.distribution.name not in (rejected.stdout + rejected.stderr).splitlines()
    assert git_output(session.repository, "rev-parse", "HEAD") == head


def test_claude_session_hooks_require_environment_and_match_child_attribution(
    claude_session: ClaudeSession, capsys: pytest.CaptureFixture[str]
) -> None:
    session = claude_session
    head = git_output(session.repository, "rev-parse", "HEAD")
    bad_commit = ("commit", "--allow-empty", "-m", "帰属がない")
    blocked = _session_git(session.repository, session.env_file, session.environment, *bad_commit)
    assert blocked.returncode != 0 and session.trailer in blocked.stderr
    assert git_output(session.repository, "rev-parse", "HEAD") == head

    for setup in (
        "unset AGENT_TOOLKIT_COMMIT_CLAUDE_ROOT",
        "export AGENT_TOOLKIT_COMMIT_CLAUDE_ROOT=''",
        "unset AGENT_TOOLKIT_COMMIT_CLAUDE_UV",
    ):
        broken = _session_git(
            session.repository, session.env_file, session.environment, *session.commit, command=f'{setup}; exec git "$@"'
        )
        assert broken.returncode != 0 and "local-hook" not in broken.stdout + broken.stderr
    assert git_output(session.repository, "rev-parse", "HEAD") == head

    child_trailer = co_author_trailer(RuntimeIdentity("claude", "claude-opus-5-5", "high", "observed"))
    record = {
        "tool_name": "Bash",
        "tool_input": {"command": f"git commit --allow-empty -m 試験 -m {shlex.quote(child_trailer)}"},
        "cwd": str(session.repository),
        "session_id": "stable",
        "agent_id": "child",
        "model": "claude-opus-5-5",
        "effort": "high",
    }
    assert dispatch.main(json.dumps(record)) == 0
    hook_output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert "permissionDecision" not in hook_output, hook_output.get("permissionDecisionReason")
    child_command = hook_output["updatedInput"]["command"]
    assert child_command.startswith("export AGENT_TOOLKIT_COMMIT_CLAUDE_ACTOR=stable.child\n")
    child_commit = _session_git(session.repository, session.env_file, session.environment, command=child_command)
    _assert_session_commit(child_commit, session.distribution)
    assert child_trailer in git_output(session.repository, "show", "-s", "--format=%B")
