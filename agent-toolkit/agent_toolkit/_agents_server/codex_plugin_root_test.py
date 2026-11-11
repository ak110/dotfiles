"""Codexのplugin_rootを検証する。"""

from __future__ import annotations

import pathlib
import shutil
from types import SimpleNamespace
from typing import Any

import psutil
import pytest

from agent_toolkit._agents_server import plugin_root as plugin_roots
from agent_toolkit._atk import managed_temp
from agent_toolkit._common import state_paths
from agent_toolkit._testing.codex_plugin_support import _lease_plugin_source, _make_plugin_root

# backendの内部境界を直接検証する。
# pylint: disable=protected-access

pytestmark = pytest.mark.usefixtures("codex_stable_plugin_roots")


@pytest.mark.parametrize("root_is_version", [True, False])
def test_versioned_plugin_root_is_copied_to_a_stable_location(tmp_path: pathlib.Path, root_is_version: bool) -> None:
    """版別ディレクトリ配下の配布物rootは、複製元を失っても解決できる実体へ写す。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True, root_is_version=root_is_version)

    resolved = plugin_roots.resolve_stable_plugin_root(source)

    assert resolved != source
    shutil.rmtree(source)
    assert (resolved / "agent_toolkit" / "agents_server_mcp.py").is_file()
    assert not (resolved / ".venv").exists()


def test_versioned_plugin_root_is_copied_once_per_source(tmp_path: pathlib.Path) -> None:
    """同じ複製元への解決を繰り返しても複製先は変わらない。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    first = plugin_roots.resolve_stable_plugin_root(source)
    second = plugin_roots.resolve_stable_plugin_root(source)

    assert first == second
    assert first != source
    assert [entry.name for entry in first.parent.parent.iterdir() if entry.is_dir()] == [first.parent.name]


def test_plain_plugin_root_is_used_as_is(tmp_path: pathlib.Path) -> None:
    """版別ディレクトリに当たらない配布物rootは複製せずそのまま使う。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=False)

    assert plugin_roots.resolve_stable_plugin_root(source) == source


def test_copy_failure_falls_back_to_the_resolved_root(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    """複製に失敗しても例外を伝播させず、解決したrootで委譲の起動を成立させる。"""
    source = _make_plugin_root(tmp_path / "cache", "2.125.0", versioned=True)

    def _fail(*_args: Any, **_kwargs: Any) -> pathlib.Path:
        raise plugin_roots._managed_temp.ManagedTempError("一時領域を作成できない")

    monkeypatch.setattr(plugin_roots._managed_temp, "create_managed_temp", _fail)

    assert plugin_roots.resolve_stable_plugin_root(source) == source


@pytest.mark.parametrize("legacy_server_present", [False, True])
def test_copy_failure_is_not_reused(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, legacy_server_present: bool
) -> None:
    """同namespaceの旧MCPが生存する場合も、失敗物を回収して次の解決で完成した実体を使用する。"""
    source = _lease_plugin_source(tmp_path / "broken")
    if legacy_server_present:
        process = psutil.Process()
        log = state_paths.state_dir() / "agents-server.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.touch()
        real_cmdline = psutil.Process.cmdline
        real_open_files = psutil.Process.open_files

        # 回収判定は列挙したPIDからProcessを新しく構築するため、インスタンスではなくクラスを差し替える。
        # 他のPIDの観測を変えないよう、旧MCPとして扱う自プロセスのPIDだけに限定する。
        def legacy_cmdline(self: psutil.Process) -> list[str]:
            if self.pid == process.pid:
                return ["python", "/old/agent_toolkit/agents_server_mcp.py"]
            return real_cmdline(self)

        def legacy_open_files(self: psutil.Process) -> list[Any]:
            if self.pid == process.pid:
                return [SimpleNamespace(path=str(log))]
            return real_open_files(self)

        monkeypatch.setattr(psutil.Process, "cmdline", legacy_cmdline)
        monkeypatch.setattr(psutil.Process, "open_files", legacy_open_files)
        monkeypatch.setattr(psutil, "process_iter", lambda: [process])

    def fail_copy(_src: pathlib.Path, dst: pathlib.Path, **_kwargs: object) -> None:
        dst.mkdir()
        (dst / "partial").touch()
        raise OSError("copy interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(shutil, "copytree", fail_copy)
        assert plugin_roots.resolve_stable_plugin_root(source) == source
    assert not managed_temp.list_managed_temp()
    resolved = plugin_roots.resolve_stable_plugin_root(source)
    assert resolved != source
    assert (resolved / "payload.txt").read_text(encoding="utf-8") == "起動した版"


def test_distinct_sources_and_versions_have_separate_roots(tmp_path: pathlib.Path) -> None:
    """配布元または版が異なる実体を、それぞれ独立に保持する。"""
    roots = [
        plugin_roots.resolve_stable_plugin_root(_lease_plugin_source(tmp_path / "first", "1.0.0")),
        plugin_roots.resolve_stable_plugin_root(_lease_plugin_source(tmp_path / "second", "1.0.0")),
        plugin_roots.resolve_stable_plugin_root(_lease_plugin_source(tmp_path / "first", "2.0.0")),
    ]
    assert len(set(roots)) == 3
    assert all((root / "payload.txt").read_text(encoding="utf-8") == "起動した版" for root in roots)
