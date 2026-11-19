"""昇格したプロセスが書いた状態を、同じユーザーの非昇格のプロセスが読めることを検証する。"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable

import pytest

from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import windows_security

pytestmark = pytest.mark.windows


def _registry_index(base: pathlib.Path) -> pathlib.Path:
    """管理サービスの世代索引（2026-11-19に読めなくなったファイル）を書く。"""
    GenerationRegistry(base / "agent-toolkit" / "services" / "agents-server").reserve("generation", base, "1.0.0")
    return base / "agent-toolkit" / "services" / "agents-server" / "generations.json"


def _managed_temp(base: pathlib.Path) -> pathlib.Path:
    """managed-tempの一時rootと、その直下の作業ディレクトリへ書く。"""
    del base  # 一時rootはLOCALAPPDATAから解決する。
    path = managed_temp.create_managed_temp("work") / "state.json"
    path.write_text("{}", encoding="utf-8")
    return path


@pytest.mark.skipif(sys.platform != "win32", reason="Windowsのtokenと継承ACLを実APIで確かめる")
@pytest.mark.parametrize("write", [_registry_index, _managed_temp])
def test_state_written_by_elevated_process_stays_readable_without_administrators(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, write: Callable[[pathlib.Path], pathlib.Path]
) -> None:
    """昇格したプロセスが状態ディレクトリへ書いたファイルを、同じユーザーの非昇格のプロセスが読める。

    2026-11-19、SSHで昇格して実行した`update-dotfiles`が管理サービスの世代索引を書き直し、
    0o700で作成された状態ディレクトリのOWNER RIGHTSがAdministratorsを指して、非昇格の旧世代が読めずに停止した。
    """
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    if not windows_security.is_elevated():
        pytest.skip("昇格したtokenで実行した場合だけ、所有者がAdministratorsのファイルを作成できる")
    base = tmp_path / "local-app-data"
    windows_tokens.create_profile_directory(base)
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    path = write(base)
    assert windows_tokens.readable_without_administrators(path), windows_security.dacl_sddl(path)
