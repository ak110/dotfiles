"""repository編集制約を両ホストの公開hook入力から検証する。"""

import json
import pathlib
import shlex

import pytest

from agent_toolkit._atk import managed_temp
from agent_toolkit._common import private_notes
from agent_toolkit._hooks import rules_context
from agent_toolkit._hooks.pretooluse import dispatch
from agent_toolkit._hooks.pretooluse.decision import Decision
from agent_toolkit._testing import git_repository as git
from agent_toolkit._testing.pretooluse_support import _run_hook_in_process, denial_reason


@pytest.fixture(autouse=True)
def _skip_unrelated_document_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    """保護の通過後に働く資料取得案内を分離し、Git境界と公開deny出力だけを検証する。"""
    monkeypatch.setattr(dispatch, "read_delivery", lambda *_args, **_kwargs: Decision())


def _payload(repository: pathlib.Path, kind: str, *, target: pathlib.Path | None = None) -> dict:
    path = target or repository / "file.txt"
    data = {"cwd": str(repository), "session_id": "repository-protection"}
    if kind == "patch":
        return {
            **data,
            "turn_id": "turn",
            "tool_name": "apply_patch",
            "tool_input": {"command": f"*** Begin Patch\n*** Add File: {path}\n+text\n*** End Patch"},
        }
    if kind == "commit":
        return {**data, "tool_name": "Bash", "tool_input": {"command": "git commit --amend --no-edit"}}
    if kind == "NotebookEdit":
        return {**data, "tool_name": kind, "tool_input": {"notebook_path": str(path), "new_source": "text"}}
    return {
        **data,
        "tool_name": kind,
        "tool_input": {"file_path": str(path), "content": "text", "old_string": "", "new_string": "text"},
    }


@pytest.mark.parametrize(
    "edit, expected",
    [(None, "承認後"), ("awi", "atk wi add --target-repo"), ("deny", "ユーザーへ報告"), ("allow", None)],
)
@pytest.mark.parametrize("kind", ["Write", "Edit", "NotebookEdit", "patch", "commit"])
def test_public_edit_and_commit_protection(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    edit: str | None,
    expected: str | None,
    kind: str,
) -> None:
    """未登録・awi・denyだけを正常denyとし、拒否本文で次の行動を案内する。"""
    repository = git.init_repository(tmp_path / "repo", origin="git@example.test:team/repo.git")
    settings = {} if edit is None else {"example.test/team/repo": {"edit": edit}}
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps(settings))
    result = _run_hook_in_process("pretooluse", _payload(repository, kind))
    assert result.returncode == 0 and not result.stderr
    if expected:
        reason = denial_reason(result)
        assert expected in reason and "次の操作:" in reason
    else:
        assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


@pytest.mark.parametrize("origin", [None, "https://example.test/team/repo.git"])
def test_allowed_worktree_and_exempt_paths(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, origin: str | None) -> None:
    """worktreeの同一識別とGit外・private-notes・managed-tempの例外を確かめる。"""
    repository = git.init_repository(tmp_path / "repo", origin=origin, commit_message="初期履歴")
    linked = tmp_path / "linked"
    git.run_git(repository, "worktree", "add", "-b", "linked", str(linked))
    repo_id = "example.test/team/repo" if origin else str(repository)
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps({repo_id: {"edit": "allow"}}))
    for kind in ("patch", "commit"):
        result = _run_hook_in_process("pretooluse", _payload(linked, kind))
        assert result.returncode == 0
        assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", "{}")
    for root in (tmp_path / "outside", private_notes.default_private_notes(), managed_temp.temp_root_path() / "session-test"):
        root.mkdir(parents=True, exist_ok=True)
        if root.name != "outside":
            git.init_repository(root)
        result = _run_hook_in_process("pretooluse", _payload(root, "Write"))
        assert result.returncode == 0
        assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


@pytest.mark.parametrize(
    "command",
    [
        "git -C {target} commit --amend --no-edit",
        "cd {target} && git commit --amend --no-edit",
        "git --git-dir={target}/.git --work-tree={target} commit --amend --no-edit",
        "git --git-dir={target}/.git commit --amend --no-edit",
        "git -C {target} commit --fixup HEAD",
        "echo literal && git -C {target} commit -m text",
    ],
)
def test_commit_location_is_checked(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """shellのcd・gitの対象指定とメッセージ非変更commitにも同じ拒否を適用する。"""
    allowed = git.init_repository(managed_temp.temp_root_path() / "allowed")
    target = git.init_repository(tmp_path / "denied", origin="https://example.test/team/denied.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", "{}")
    payload = {
        "tool_name": "Bash",
        "cwd": str(allowed),
        "tool_input": {"command": command.format(target=shlex.quote(str(target)))},
    }
    assert "example.test/team/denied" in denial_reason(_run_hook_in_process("pretooluse", payload))


@pytest.mark.parametrize(
    "command",
    [
        "GIT_DIR={target}/.git git commit --amend --no-edit",
        "env GIT_WORK_TREE={target} git commit --amend --no-edit",
        "GIT_COMMON_DIR={target}/.git bash -c 'git commit --amend --no-edit'",
        "env GIT_DIR={target}/.git bash -c 'env GIT_WORK_TREE={target} git commit --amend --no-edit'",
        "export GIT_DIR={target}/.git; git commit --amend --no-edit",
        "GIT_DIR={target}/.git; export GIT_DIR; git commit --amend --no-edit",
        "declare -x GIT_WORK_TREE={target}; git commit --amend --no-edit",
        "set -a; GIT_COMMON_DIR={target}/.git; git commit --amend --no-edit",
        "export GIT_DIR={target}/.git; bash -c 'git commit --amend --no-edit'",
        "{{ export GIT_DIR={target}/.git; }}; git commit --amend --no-edit",
    ],
)
def test_commit_with_repository_environment_is_not_assumed_to_use_cwd(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    """前置代入と子シェルの継承を失わず、未解決のcommit先をallowと推定しない。"""
    allowed = git.init_repository(managed_temp.temp_root_path() / "allowed")
    target = git.init_repository(tmp_path / "denied", origin="https://example.test/team/denied.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", "{}")
    result = _run_hook_in_process(
        "pretooluse",
        {"tool_name": "Bash", "cwd": str(allowed), "tool_input": {"command": command.format(target=shlex.quote(str(target)))}},
    )
    reason = denial_reason(result)
    assert "環境変数" in reason and "git -C" in reason and "--git-dir" in reason


@pytest.mark.parametrize("edit", [None, "awi", "deny", "allow"])
def test_git_dir_exemption_uses_repository_storage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, edit: str | None
) -> None:
    """例外cwdから外部Git実体を指定しても、その実体の登録を適用する。"""
    allowed = git.init_repository(managed_temp.temp_root_path() / "allowed")
    target = git.init_repository(tmp_path / "target", origin="https://example.test/team/target.git")
    settings = {} if edit is None else {"example.test/team/target": {"edit": edit}}
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps(settings))
    for repository, blocked in ((target, edit != "allow"), (allowed, False)):
        result = _run_hook_in_process(
            "pretooluse",
            {
                "tool_name": "Bash",
                "cwd": str(allowed),
                "tool_input": {"command": f"git --git-dir={shlex.quote(str(repository / '.git'))} commit --amend --no-edit"},
            },
        )
        if blocked:
            assert "example.test/team/target" in denial_reason(result)
        else:
            assert result.returncode == 0 and not result.stderr
            assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


@pytest.mark.parametrize(
    "command",
    [
        "(export GIT_DIR=/elsewhere); git commit --amend --no-edit",
        "bash -c 'export GIT_DIR=/elsewhere'; git commit --amend --no-edit",
        "env GIT_DIR=/elsewhere true; git commit --amend --no-edit",
        "git commit --amend --no-edit; export GIT_DIR=/elsewhere",
    ],
)
def test_repository_environment_does_not_taint_independent_commit(monkeypatch: pytest.MonkeyPatch, command: str) -> None:
    """子環境と後続だけの代入を、作用しないcommitへ広げない。"""
    repository = git.init_repository(managed_temp.temp_root_path() / "allowed")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", "{}")
    result = _run_hook_in_process(
        "pretooluse", {"tool_name": "Bash", "cwd": str(repository), "tool_input": {"command": command}}
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_exempt_worktree_does_not_exempt_external_repository_storage(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """例外領域へ置いたlinked worktreeも、更新する外部Git実体の登録でcommitを判定する。"""
    target = git.init_repository(tmp_path / "target", origin="https://example.test/team/target.git", commit_message="初期履歴")
    linked = managed_temp.temp_root_path() / "linked"
    git.run_git(target, "worktree", "add", "-b", "linked", str(linked))
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", "{}")
    assert "example.test/team/target" in denial_reason(_run_hook_in_process("pretooluse", _payload(linked, "commit")))


@pytest.mark.parametrize("reverse", [False, True])
def test_patch_move_checks_both_paths(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, reverse: bool) -> None:
    """移動元と移動先のどちらが禁止repositoryでも変更前に拒否する。"""
    allowed = git.init_repository(tmp_path / "allowed", origin="https://example.test/team/allowed.git")
    denied = git.init_repository(tmp_path / "denied", origin="https://example.test/team/denied.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps({"example.test/team/allowed": {"edit": "allow"}}))
    source, target = (denied, allowed) if reverse else (allowed, denied)
    patch = f"*** Begin Patch\n*** Update File: {source}/a.txt\n*** Move to: {target}/b.txt\n@@\n-old\n+new\n*** End Patch"
    result = _run_hook_in_process(
        "pretooluse",
        {
            "tool_name": "apply_patch",
            "turn_id": "turn",
            "cwd": str(allowed),
            "tool_input": {"command": patch},
        },
    )
    assert "example.test/team/denied" in denial_reason(result)


def test_symlink_and_unrelated_missing_worktree(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """リンク先とリンク元を保護し、別repositoryの失われた処理対象でallowを拒否しない。"""
    allowed = git.init_repository(tmp_path / "allowed", origin="https://example.test/team/allowed.git")
    denied = git.init_repository(tmp_path / "denied", origin="https://example.test/team/denied.git")
    settings = {
        "example.test/team/allowed": {"edit": "allow"},
        "example.test/team/stale": {"edit": "allow", "process_loop": True, "path": str(tmp_path / "missing")},
    }
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps(settings))
    (denied / "file.txt").write_text("text", encoding="utf-8")
    (allowed / "link").symlink_to(denied, target_is_directory=True)
    result = _run_hook_in_process("pretooluse", _payload(allowed, "Write", target=allowed / "link" / "file.txt"))
    assert "example.test/team/denied" in denial_reason(result)
    (denied / "link").symlink_to(allowed / "new.txt")
    result = _run_hook_in_process("pretooluse", _payload(denied, "patch", target=denied / "link"))
    assert "example.test/team/denied" in denial_reason(result)
    result = _run_hook_in_process("pretooluse", _payload(allowed, "Write"))
    assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


def test_plain_shell_write_is_not_blocked(tmp_path: pathlib.Path) -> None:
    """Bashの一般の書込みは要求どおり保護対象へ広げない。"""
    repository = git.init_repository(tmp_path / "unregistered")
    result = _run_hook_in_process(
        "pretooluse",
        {
            "tool_name": "Bash",
            "cwd": str(repository),
            "tool_input": {"command": "printf text > file.txt"},
        },
    )
    assert result.returncode == 0
    assert json.loads(result.stdout or "{}").get("hookSpecificOutput", {}).get("permissionDecision") != "deny"


@pytest.mark.parametrize("delegated", [False, True])
def test_session_start_notifies_delegates(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    delegated: bool,
) -> None:
    """SessionStartはレーン担当の起動でも扱いと次の行動を通知する。"""
    repository = git.init_repository(tmp_path / "repo", origin="https://example.test/team/repo.git")
    monkeypatch.setenv("AGENT_TOOLKIT_CONFIG_REPOSITORIES", json.dumps({"example.test/team/repo": {"edit": "awi"}}))
    if delegated:
        monkeypatch.setenv("AGENT_TOOLKIT_DELEGATED_SESSION", "1")
    assert rules_context.main(json.dumps({"hook_event_name": "SessionStart", "source": "startup", "cwd": str(repository)})) == 0
    context = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["additionalContext"]
    assert "example.test/team/repo" in context and "atk wi add --target-repo" in context
