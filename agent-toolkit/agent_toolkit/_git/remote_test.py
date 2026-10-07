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
        ("https://github.com/owner/repo", "github.com/owner/repo"),
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


@pytest.mark.parametrize("remote_url", ["", "not-a-url", "/home/user/dotfiles", "https://github.com/ak110"])
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


@pytest.mark.parametrize(
    ("value", "hostname", "project_path"),
    [
        ("https://github.com/ak110/dotfiles.git", "github.com", "ak110/dotfiles"),
        ("git@gitlab.example.com:group/sub/repo.git", "gitlab.example.com", "group/sub/repo"),
        ("ssh://git@github.com:22/ak110/dotfiles.git", "github.com", "ak110/dotfiles"),
        ("gitlab.example.com/group/repo", "gitlab.example.com", "group/repo"),
        ("ak110/dotfiles", None, "ak110/dotfiles"),
    ],
)
def test_parse_remote_location(value: str, hostname: str | None, project_path: str) -> None:
    """URL・SCP形式・`[host/]owner/repository`からホスト名とプロジェクトのパスを取り出すこと。"""
    assert _git_remote.parse_remote_location(value) == _git_remote.RemoteLocation(hostname, project_path)


@pytest.mark.parametrize("value", ["", "dotfiles", "https://github.com/ak110"])
def test_parse_remote_location_rejects_value_without_project_path(value: str) -> None:
    """`owner/repository`の形のプロジェクトのパスを持たない値は`ValueError`を送出すること。"""
    with pytest.raises(ValueError, match="リモートのプロジェクトのパスを取り出せない"):
        _git_remote.parse_remote_location(value)


def test_origin_url_and_remote_urls_read_configured_remotes(tmp_path: pathlib.Path) -> None:
    """`origin`のURLと全リモートのURLを返し、Git管理外では`None`と空のリストを返すこと。"""
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", str(repository)], check=True)
    subprocess.run(
        ["git", "-C", str(repository), "remote", "add", "origin", "https://github.com/ak110/dotfiles.git"], check=True
    )
    subprocess.run(["git", "-C", str(repository), "remote", "add", "fork", "git@github.com:other/dotfiles.git"], check=True)
    outside = tmp_path / "outside"
    outside.mkdir()

    assert _git_remote.origin_url(repository) == "https://github.com/ak110/dotfiles.git"
    assert sorted(_git_remote.remote_urls(repository, timeout=30)) == [
        "git@github.com:other/dotfiles.git",
        "https://github.com/ak110/dotfiles.git",
    ]
    assert _git_remote.origin_url(outside) is None
    assert _git_remote.remote_urls(outside, timeout=30) == []
