"""`Operations`の確認状態の同期失敗で返す案内文のテスト。"""

import datetime
import os
import pathlib
import shlex
import subprocess

import pytest

from agent_toolkit._atk.serve import wi_operations
from agent_toolkit._atk.wi import entries as wi_entries
from agent_toolkit._testing import git_repository


def _confirmation_repository(tmp_path: pathlib.Path, stage: str) -> tuple[pathlib.Path, str, str]:
    """空白と引用符を含む保存先に要確認のAWIを置き、指定した段で失敗する状態を作る。"""
    root = tmp_path / "private notes's dir"
    path = wi_entries.entry_path(root, "confirmation.md", fallback_date=datetime.date(2026, 10, 1))
    path.parent.mkdir(parents=True, exist_ok=True)
    content = "---\ntarget_repo: github.com/example/repo\ntype: awi\nstate: inbox\nconfirmation: required\n---\n\n本文\n"
    path.write_text(content, encoding="utf-8")
    remote = git_repository.init_bare_repository(tmp_path / "remote.git")
    git_repository.init_repository(root, initial_branch="main", origin=str(remote), commit_message="試験の初期状態")
    git_repository.run_git(root, "push", "--set-upstream", "origin", "main")
    if stage == "push":
        git_repository.run_git(root, "remote", "set-url", "origin", str(tmp_path / "unreachable.git"))
    else:
        hook = root / ".git" / "hooks" / "pre-commit"
        hook.parent.mkdir(parents=True, exist_ok=True)
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
    return root, path.name, content


@pytest.mark.parametrize("stage", ["commit", "push"])
def test_sync_failure_guidance_quotes_repository_as_one_argument(tmp_path: pathlib.Path, stage: str) -> None:
    """commit・pushの失敗案内にある`git -C`の保存先は、空白と引用符を含んでもそのまま実行できる。"""
    root, filename, content = _confirmation_repository(tmp_path, stage)

    with pytest.raises(wi_operations.SyncIncompleteError) as raised:
        wi_operations.Operations(root).set_confirmation(filename, True, content)

    argv = ["git", "-C", str(root.resolve()), "status"]
    # 実行中のOSのコマンドライン規則で期待値を作る。Windowsでは文字列をそのままCreateProcessへ渡し、
    # POSIXではシェルの規則で分割して、案内の保存先が1個の引数として届くことを確かめる。
    expected = subprocess.list2cmdline(argv) if os.name == "nt" else shlex.join(argv)
    message = str(raised.value)
    assert f"`{expected}`" in message
    command: str | list[str] = expected if os.name == "nt" else shlex.split(expected)
    completed = subprocess.run(command, check=False, capture_output=True, text=True, encoding="utf-8")
    assert completed.returncode == 0, completed.stderr


def test_sync_failure_guidance_uses_windows_quoting(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows分岐の案内はコマンドラインの引用規則で示す。

    どのOSでも分岐の値を切り替えて文字列の形式を確かめる。Windowsでの実行による引数境界の確認は、
    上の試験がWindows上で実行されたときに行う。
    """
    monkeypatch.setattr(wi_operations, "_WINDOWS", True)
    root, filename, content = _confirmation_repository(tmp_path, "push")

    with pytest.raises(wi_operations.SyncIncompleteError) as raised:
        wi_operations.Operations(root).set_confirmation(filename, True, content)

    assert f'`git -C "{root.resolve()}" status`' in str(raised.value)
