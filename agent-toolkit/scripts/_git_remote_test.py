"""`_git_remote`のリモートURL正規化と取得を検査する。"""

import _git_remote
import pytest


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
    """解析できない値はValueErrorを送出すること。"""
    with pytest.raises(ValueError, match="リモートURLとして解析できません"):
        _git_remote.normalize_remote_url(remote_url)
