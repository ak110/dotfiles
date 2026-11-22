from __future__ import annotations

import pathlib
import sys
from collections.abc import Callable

import pytest

from agent_toolkit._agents_server.service_generations import GenerationRegistry
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import plugin_root_leases, windows_security

pytestmark = pytest.mark.windows


def _registry_index(base: pathlib.Path) -> pathlib.Path:
    GenerationRegistry(base / "agent-toolkit" / "services" / "agents-server").reserve("generation", base, "1.0.0")
    return base / "agent-toolkit" / "services" / "agents-server" / "generations.json"


def _managed_temp(base: pathlib.Path) -> pathlib.Path:
    del base
    path = managed_temp.create_managed_temp("work") / "state.json"
    path.write_text("{}", encoding="utf-8")
    return path


def _plugin_root_lock(base: pathlib.Path) -> pathlib.Path:
    """配布物の保持領域のロックを、状態ディレクトリ直下の`plugin-root-leases`へ作成する。"""
    with plugin_root_leases.plugin_root_lock(base / "plugin-root"):
        pass
    (lock,) = (base / "agent-toolkit" / "plugin-root-leases").glob("*.lock")
    return lock


@pytest.mark.skipif(sys.platform != "win32", reason="Windowsのtokenと継承ACLを実APIで確かめる")
@pytest.mark.parametrize("write", [_registry_index, _managed_temp, _plugin_root_lock])
def test_state_written_by_elevated_process_stays_readable_without_administrators(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, write: Callable[[pathlib.Path], pathlib.Path]
) -> None:
    from agent_toolkit._testing import windows_tokens  # noqa: PLC0415  # pylint: disable=import-outside-toplevel

    if not windows_security.is_elevated():
        pytest.skip("昇格したtokenで実行した場合だけ、所有者がAdministratorsのファイルを作成できる")
    base = tmp_path / "local-app-data"
    windows_tokens.create_profile_directory(base)
    monkeypatch.setenv("LOCALAPPDATA", str(base))
    path = write(base)
    assert windows_tokens.readable_without_administrators(path), windows_security.dacl_sddl(path)
