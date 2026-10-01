"""`_git_remote`のリモートURLの正規化と取得の動作を確かめる。"""

import pathlib
import subprocess

import pytest

from agent_toolkit._common.next_action import ActionableError
from agent_toolkit._git import remote as _git_remote


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("https://github.com/ak110/dotfiles.git", "github.com/ak110/dotfiles"),
        ("https://GitHub.com/AK110/Dotfiles", "github.com/ak110/dotfiles"),
        ("git@github.com:ak110/dotfiles.git\n", "github.com/ak110/dotfiles"),
        ("ssh://git@github.com/ak110/dotfiles.git", "github.com/ak110/dotfiles"),
        ("ssh://git@github.com:22/ak110/dotfiles.git", "github.com/ak110/dotfiles"),
        ("ssh://git@gitlab.example.com:2222/group/sub/repo.git", "gitlab.example.com/group/sub/repo"),
        ("https://github.com:443/ak110/dotfiles.git", "github.com/ak110/dotfiles"),
        ("github.com/ak110/dotfiles", "github.com/ak110/dotfiles"),
        ("gitlab.example.com/group/sub/repo", "gitlab.example.com/group/sub/repo"),
    ],
)
def test_normalize_remote_url(remote_url: str, expected: str) -> None:
    """URL形式ごとに`host/owner/repository`形式へ正規化されること。"""
    assert _git_remote.normalize_remote_url(remote_url) == expected


@pytest.mark.parametrize("remote_url", ["", "/home/user/dotfiles", "https://github.com/ak110"])
def test_normalize_remote_url_rejects_invalid_value(remote_url: str) -> None:
    """解析できない値は、originの値を確かめる次の操作を持つ例外を送出すること。"""
    with pytest.raises(ActionableError, match="リモートURLとして解析できません") as error_info:
        _git_remote.normalize_remote_url(remote_url)

    assert "git remote get-url origin" in error_info.value.next_action


def test_resolve_repo_identifier_reads_legacy_local_path(tmp_path: pathlib.Path) -> None:
    """旧ローカルパス形はoriginを介して正規なURL形へ解決する。"""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:Example/Repo.git"],
        check=True,
    )

    assert _git_remote.resolve_repo_identifier(str(tmp_path)) == "github.com/example/repo"


@pytest.mark.parametrize("kind", ["missing", "no-remote"])
def test_resolve_repo_identifier_returns_none_for_unresolvable_path(tmp_path: pathlib.Path, kind: str) -> None:
    """存在しないパスとorigin未設定リポジトリは解決不能として扱う。"""
    target = tmp_path / kind
    if kind == "no-remote":
        subprocess.run(["git", "init", str(target)], check=True, capture_output=True)

    assert _git_remote.resolve_repo_identifier(str(target)) is None
