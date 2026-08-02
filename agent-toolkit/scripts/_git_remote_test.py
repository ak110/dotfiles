"""`_git_remote`のリモートURL正規化と取得を検査する。"""

import pathlib
import subprocess

import _git_remote
import pytest


@pytest.mark.parametrize(
    ("remote_url", "expected"),
    [
        ("https://github.com/ak110/dotfiles.git", "github.com/ak110/dotfiles"),
        ("https://GitHub.com/AK110/Dotfiles", "github.com/ak110/dotfiles"),
        ("git@github.com:ak110/dotfiles.git\n", "github.com/ak110/dotfiles"),
        ("ssh://git@github.com/ak110/dotfiles.git", "github.com/ak110/dotfiles"),
        ("github.com/ak110/dotfiles", "github.com/ak110/dotfiles"),
    ],
)
def test_normalize_remote_url(remote_url: str, expected: str) -> None:
    """URL形式ごとに`host/owner/repository`形式へ正規化されること。"""
    assert _git_remote.normalize_remote_url(remote_url) == expected


@pytest.mark.parametrize("remote_url", ["", "/home/user/dotfiles", "https://github.com/ak110"])
def test_normalize_remote_url_rejects_invalid_value(remote_url: str) -> None:
    """解析できない値はValueErrorを送出すること。"""
    with pytest.raises(ValueError, match="リモートURLとして解析できません"):
        _git_remote.normalize_remote_url(remote_url)


def test_get_normalized_origin_returns_configured_remote(tmp_path: pathlib.Path) -> None:
    """originを設定したリポジトリから正規化済みの値を取得できること。"""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True, text=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "remote", "add", "origin", "git@github.com:ak110/dotfiles.git"],
        check=True,
        capture_output=True,
        text=True,
    )
    assert _git_remote.get_normalized_origin(tmp_path) == "github.com/ak110/dotfiles"


def test_get_normalized_origin_returns_none_for_missing_directory(tmp_path: pathlib.Path) -> None:
    """不在のディレクトリではNoneを返すこと。"""
    assert _git_remote.get_normalized_origin(tmp_path / "missing") is None


def test_get_normalized_origin_returns_none_without_origin(tmp_path: pathlib.Path) -> None:
    """origin未設定のディレクトリではNoneを返すこと。"""
    assert _git_remote.get_normalized_origin(tmp_path) is None
