import os
import pathlib
import shlex
import subprocess
import sys

import pytest

from agent_toolkit._common import commit_hook_environment
from agent_toolkit._common.runtime_identity import RuntimeIdentity, co_author_trailer
from agent_toolkit._git import command as git_command
from agent_toolkit._hooks import git_commit_msg
from agent_toolkit._hooks.pretooluse import commit_identity
from agent_toolkit._hooks.pretooluse.shell_checks_git import commit_input_unchanged_by_options
from agent_toolkit._testing.git_repository import init_repository

pytestmark = pytest.mark.windows

_BAD = "試験\n\n20261126-123456-001.md"
_ID = RuntimeIdentity("codex", "gpt-6.2-sol", "medium", "observed")
_TRAILER = co_author_trailer(_ID)
_GOOD = f"試験\n\n{_TRAILER}"


@pytest.fixture(name="repo")
def _repo_fixture(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    checkout = init_repository(tmp_path / "repo", commit_message="基点")
    hooks = tmp_path / "hook entry's"
    hooks.mkdir()
    wrapper = hooks / "commit-msg"
    package = pathlib.Path(git_commit_msg.__file__).resolve().parents[2]
    runner = shlex.quote(sys.executable.replace("\\", "/"))
    module = shlex.quote(str(pathlib.Path(git_commit_msg.__file__).resolve().as_posix()))
    directory = shlex.quote(hooks.as_posix())
    wrapper.write_text(f'#!/bin/sh\nexec {runner} {module} {directory} commit-msg "$PWD" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    commit_identity.record_commit_observation(
        {"session_id": "session-1", "model": _ID.model, "effort": _ID.effort}, is_codex=True
    )
    monkeypatch.setenv("CODEX_THREAD_ID", "session-1")
    monkeypatch.setenv(commit_hook_environment.HOOKS_ENV, str(hooks))
    monkeypatch.setenv("PYTHONPATH", str(package))
    monkeypatch.setenv("GIT_CONFIG_PARAMETERS", "'color.ui'='never' " + commit_hook_environment.config_parameter(hooks))
    return checkout


def _git(repo: pathlib.Path, *arguments: str, stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *arguments], cwd=repo, input=stdin, capture_output=True, text=True, check=False, timeout=20)


@pytest.mark.parametrize("kind", ["direct", "function", "file", "stdin"])
def test_passes_or_rejects_same_git_body_in_every_input(repo: pathlib.Path, kind: str) -> None:
    head = _git(repo, "rev-parse", "HEAD").stdout

    def attempt(message: str) -> subprocess.CompletedProcess[str]:
        if kind == "direct":
            return _git(repo, "commit", "--allow-empty", "-m", message)
        if kind == "stdin":
            return _git(repo, "commit", "--allow-empty", "-F", "-", stdin=message)
        if kind == "file":
            file = repo / "message.txt"
            file.write_text(message, encoding="utf-8")
            return _git(repo, "commit", "--allow-empty", "-F", str(file))
        return subprocess.run(
            ["sh", "-c", 'assemble() { message=$1; git commit --allow-empty -m "$message"; }; assemble "$TEST_COMMIT_MESSAGE"'],
            cwd=repo,
            env={**os.environ, "TEST_COMMIT_MESSAGE": message},
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )

    result = attempt(_BAD)
    assert result.returncode
    assert "違反が2件" in result.stderr
    assert "前段を繰り返さず" in result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout == head
    result = attempt(_GOOD)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "show", "-s", "--format=%B").stdout.rstrip() == _GOOD


@pytest.mark.parametrize("status", [0, 42])
def test_existing_commit_msg_uses_original_config_and_can_rewrite(repo: pathlib.Path, status: int) -> None:
    original = repo / "my-hooks"
    original.mkdir()
    assert _git(repo, "config", "--local", "core.hooksPath", "my-hooks").returncode == 0
    hook = original / "commit-msg"
    hook.write_text(
        "#!/bin/sh\n"
        '[ -n "$1" ] && [ "$(git config --get color.ui)" = never ] || exit 43\n'
        f"printf '%s\\n' {shlex.quote(_GOOD)} > \"$1\"\n"
        f"exit {status}\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    before = (repo / ".git" / "config").read_bytes()
    result = _git(repo, "commit", "--allow-empty", "-F", "-", stdin=_BAD)
    assert (repo / ".git" / "config").read_bytes() == before
    if status:
        assert result.returncode
        assert "違反が" not in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert _git(repo, "show", "-s", "--format=%B").stdout.rstrip() == _GOOD


def test_existing_commit_msg_invalid_rewrite_is_rejected(repo: pathlib.Path) -> None:
    hook = repo / ".git" / "hooks" / "commit-msg"
    hook.write_text(f'#!/bin/sh\nprintf "%s\\n" {shlex.quote(_BAD)} > "$1"\n', encoding="utf-8")
    hook.chmod(0o755)
    head = _git(repo, "rev-parse", "HEAD").stdout
    result = _git(repo, "commit", "--allow-empty", "-m", _GOOD)
    assert result.returncode != 0
    assert "違反が2件" in result.stderr
    assert _git(repo, "rev-parse", "HEAD").stdout == head


def test_default_comment_cleanup_and_verbatim_limitation(repo: pathlib.Path) -> None:
    assert _git(repo, "config", "core.commentChar", ";").returncode == 0
    body = f"{_GOOD}\n; 20261126-123456-001.md"
    result = _git(repo, "commit", "--allow-empty", "--cleanup=strip", "-m", body)
    assert result.returncode == 0, result.stderr
    assert "20261126-123456-001.md" not in _git(repo, "show", "-s", "--format=%B").stdout
    result = _git(repo, "commit", "--allow-empty", "--cleanup=verbatim", "-m", body)
    assert result.returncode == 0, result.stderr
    assert "20261126-123456-001.md" in _git(repo, "show", "-s", "--format=%B").stdout


def test_auto_comment_selection_is_not_available_to_commit_msg(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _git(repo, "config", "core.commentChar", "auto").returncode == 0
    monkeypatch.setenv("GIT_EDITOR", ":")
    message = f"試験\n\n# 20261126-123456-001.md\n\n{_TRAILER}"
    result = _git(repo, "commit", "--allow-empty", "--edit", "--cleanup=strip", "-F", "-", stdin=message)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "show", "-s", "--format=%B").stdout.strip() == message


def test_claude_subagent_uses_own_latest_observation(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    base = {"session_id": "claude-1", "model": "claude-opus-5-5", "effort": "low"}
    parent = commit_identity.record_commit_observation(base, is_codex=False)
    child = commit_identity.record_commit_observation({**base, "agent_id": "child", "effort": "medium"}, is_codex=False)
    assert parent is not None and child is not None and parent != child
    monkeypatch.setenv(commit_hook_environment.CLAUDE_ACTOR_ENV, child)
    current = co_author_trailer(RuntimeIdentity("claude", base["model"], "medium", "observed"))
    parent_trailer = co_author_trailer(RuntimeIdentity("claude", base["model"], "low", "observed"))
    result = _git(repo, "commit", "--allow-empty", "-m", "試験", "-m", parent_trailer)
    assert result.returncode and current in result.stderr
    assert _git(repo, "commit", "--allow-empty", "-m", "試験", "-m", current).returncode == 0
    assert child == commit_identity.record_commit_observation({**base, "agent_id": "child", "effort": "high"}, is_codex=False)
    latest = co_author_trailer(RuntimeIdentity("claude", base["model"], "high", "observed"))
    result = _git(repo, "commit", "--allow-empty", "-m", "試験", "-m", current)
    assert result.returncode and latest in result.stderr
    assert _git(repo, "commit", "--allow-empty", "-m", "試験", "-m", latest).returncode == 0


def test_history_operations_and_human_commit_remain_allowed(repo: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _git(repo, "commit", "--allow-empty", "--amend", "--no-edit").returncode == 0
    monkeypatch.delenv("CODEX_THREAD_ID")
    assert _git(repo, "commit", "--allow-empty", "-m", _BAD).returncode == 0


def test_product_generated_commit_does_not_apply_agent_authorship(repo: pathlib.Path) -> None:
    inherited = dict(os.environ)
    clean = commit_hook_environment.atk_environment(["wi", "sync"], inherited)
    assert commit_hook_environment.atk_environment(["run-script", "commit-with-timeout"], inherited) is inherited
    result = git_command.run(["commit", "--allow-empty", "-m", "自動保存"], cwd=repo, env=clean, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "show", "-s", "--format=%B").stdout.strip() == "自動保存"
    assert _git(repo, "config", "--get", "color.ui").stdout.strip() == "never"


def test_git_can_add_valid_trailer_not_known_to_pretooluse(repo: pathlib.Path) -> None:
    direct = "git commit --allow-empty -m 試験"
    assert commit_input_unchanged_by_options(direct)
    assert not commit_input_unchanged_by_options(f"{direct} --trailer {shlex.quote(_TRAILER)}")
    result = _git(repo, "commit", "--allow-empty", "-m", "試験", "--trailer", _TRAILER)
    assert result.returncode == 0, result.stderr
    assert _git(repo, "show", "-s", "--format=%B").stdout.strip() == _GOOD


def test_existing_hook_receives_stdin(repo: pathlib.Path) -> None:
    original = repo / "my-hooks"
    original.mkdir()
    assert _git(repo, "config", "--local", "core.hooksPath", "my-hooks").returncode == 0
    hook = original / "post-rewrite"
    hook.write_text('#!/bin/sh\nprintf "%s\\n" "$1" > event\ncat > original-stdin\n', encoding="utf-8")
    hook.chmod(0o755)
    directory = repo.parent / "hook entry's"
    wrapper = directory / "post-rewrite"
    content = (directory / "commit-msg").read_text(encoding="utf-8")
    wrapper.write_text(content.replace(" commit-msg ", " post-rewrite "), encoding="utf-8")
    wrapper.chmod(0o755)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _git(repo, "commit", "--allow-empty", "--amend", "--no-edit").returncode == 0
    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (repo / "event").read_text(encoding="utf-8").strip() == "amend"
    assert (repo / "original-stdin").read_text(encoding="utf-8").strip() == f"{before} {after}"
