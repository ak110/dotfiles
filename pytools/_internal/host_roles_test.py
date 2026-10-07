"""pytools._internal.host_roles のテスト。"""

import socket
from pathlib import Path

import pytest

from pytools._internal import common, host_roles

# 変更前の判定（`claude_common.is_euryale()`と`setup_media_remote`のホスト名比較）を再現する。
# 役割の定義を`.chezmoidata.toml`へ移しても、各機構の対象ホスト集合が変わらないことを比べる。
_HOSTNAMES = ["euryale", "EURYALE", "Euryale.example.test", "euryale-container", "stheno", "Stheno", "STHENO", "other-host"]
_PLATFORMS = ["linux", "win32", "darwin"]


def _previous_is_euryale(platform: str, hostname: str) -> bool:
    return platform == "linux" and hostname.lower().split(".")[0] == "euryale"


def _previous_is_media_remote_target(hostname: str) -> bool:
    return hostname.lower() == "stheno"


@pytest.mark.parametrize("platform", _PLATFORMS)
@pytest.mark.parametrize("hostname", _HOSTNAMES)
def test_linux_server_matches_previous_is_euryale(platform: str, hostname: str) -> None:
    """Linuxの自動更新・atk serve・Codex更新の延期の対象は変更前の`is_euryale()`と同じである。"""
    assert host_roles.is_linux_server(hostname=hostname, platform=platform) is _previous_is_euryale(platform, hostname)


@pytest.mark.parametrize("hostname", _HOSTNAMES)
def test_media_remote_matches_previous_hostname_comparison(hostname: str) -> None:
    """media-remoteの対象は変更前のホスト名比較と同じである。

    変更前はドメインを切り捨てずに比較していたが、Windowsの`socket.gethostname()`はDNSサフィックスを含まない名前を返すため、
    ドメイン付きの入力は対象ホストで生じない。本テストの入力はドメインを含まない名前に限る。
    """
    assert host_roles.has_role(host_roles.MEDIA_REMOTE, hostname=hostname) is _previous_is_media_remote_target(hostname)


def test_euryale_container_is_not_linux_server() -> None:
    """`euryale-container`は全プロジェクトの開発環境だが、自動更新とatk serveの対象に含めない。"""
    assert host_roles.has_role("all_projects", hostname="euryale-container") is True
    assert host_roles.is_linux_server(hostname="euryale-container", platform="linux") is False


def test_defaults_to_current_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    """`hostname`を省略した場合は`socket.gethostname()`を使う。"""
    monkeypatch.setattr(socket, "gethostname", lambda: "EURYALE.example.test")
    assert host_roles.is_linux_server(platform="linux") is True


def test_without_dotfiles_root_has_no_role(monkeypatch: pytest.MonkeyPatch) -> None:
    """作業ツリーを解決できない場合は役割なしとして扱う。"""
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: None)
    assert host_roles.has_role(host_roles.LINUX_SERVER, hostname="euryale") is False


@pytest.mark.parametrize("content", ["[host_roles\n", "host_roles = 1\n", '[host_roles]\nlinux_server = "euryale"\n'])
def test_unreadable_data_has_no_role(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str) -> None:
    """役割データを読めない場合と形式が想定と異なる場合は役割なしとして扱う。"""
    data = tmp_path / ".chezmoi-source" / ".chezmoidata.toml"
    data.parent.mkdir()
    data.write_text(content, encoding="utf-8")
    monkeypatch.setattr(common, "find_dotfiles_root", lambda: tmp_path)
    assert host_roles.has_role(host_roles.LINUX_SERVER, hostname="euryale") is False
