"""独立実行のstrict保持と既存serverの保持方針が両立することを確認する。"""

import json
import os
import pathlib
import shutil
import sys
import types
import typing

import pytest

from agent_toolkit._agents_server import plugin_root
from agent_toolkit._common import plugin_root_leases, state_paths


def _source(tmp_path: pathlib.Path) -> pathlib.Path:
    root = tmp_path / "checkout" / "agent-toolkit"
    (root / ".claude-plugin").mkdir(parents=True)
    (root / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "agent-toolkit", "version": "1.0.0"}),
        encoding="utf-8",
    )
    (root / "content.py").write_text("value = 1\n", encoding="utf-8")
    return root


def test_independent_checkout_retains_each_new_dirty_input_and_reuses_held_child(tmp_path):
    source = _source(tmp_path)
    assert plugin_root.resolve_stable_plugin_root(source) == source
    first = plugin_root.resolve_stable_plugin_root(source, independent=True)
    (source / "content.py").write_text("value = 2\n", encoding="utf-8")
    second = plugin_root.resolve_stable_plugin_root(source, independent=True)
    assert first != second != source
    assert (first / "content.py").read_text(encoding="utf-8") == "value = 1\n"
    assert (second / "content.py").read_text(encoding="utf-8") == "value = 2\n"
    assert plugin_root.resolve_stable_plugin_root(first, independent=True) == first
    shutil.rmtree(source)
    assert (second / "content.py").is_file()
    with plugin_root_leases.plugin_root_lock(first.parent):
        assert plugin_root_leases.plugin_root_in_use(first.parent)


def test_independent_copy_failure_never_falls_back_to_source(tmp_path, monkeypatch):
    source = _source(tmp_path)

    def fail(*_args, **_kwargs):
        raise OSError("保持先へ保存できない")

    monkeypatch.setattr(plugin_root.shutil, "copytree", fail)
    with pytest.raises(OSError, match="保持先"):
        plugin_root.resolve_stable_plugin_root(source, independent=True)
    assert source.is_dir()


def test_checkout_resolution_does_not_write_capability_record(tmp_path):
    """保持実体を使わない起動のたびに状態ファイルを増やさない。"""
    source = _source(tmp_path)
    for _ in range(3):
        assert plugin_root.resolve_stable_plugin_root(source) == source
    assert not list((state_paths.state_dir() / "plugin-root-leases").glob("*.json"))


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_independent_bootstrap_passes_held_cli_and_python_to_worker(tmp_path, monkeypatch, platform):
    source = _source(tmp_path)
    (source / "scripts").mkdir()
    (source / "scripts/atk").write_text("entry", encoding="utf-8")
    held = plugin_root.resolve_stable_plugin_root(source, independent=True)
    captured: dict[str, typing.Any] = {}

    class Replaced(Exception):
        pass

    def replace(executable, arguments, environment, **_kwargs):
        captured.update(executable=executable, arguments=arguments, environment=environment)
        raise Replaced()

    def wait(arguments, **kwargs):
        captured.update(arguments=arguments, environment=kwargs["env"])
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(plugin_root, "resolve_stable_plugin_root", lambda **_kwargs: held)
    monkeypatch.setattr(
        plugin_root.plugin_environment,
        "prepared_python",
        lambda *_args, **_kwargs: pathlib.Path(sys.executable),
    )
    monkeypatch.setattr(plugin_root, "os", types.SimpleNamespace(name=platform, environ=os.environ, execvpe=replace))
    monkeypatch.setattr(plugin_root.subprocess, "run", wait)
    if platform == "posix":
        with pytest.raises(Replaced):
            plugin_root.main(["--wi-submit-worker", "a" * 64])
    else:
        assert plugin_root.main(["--wi-submit-worker", "a" * 64]) == 0
    assert captured["environment"]["PATH"].split(os.pathsep)[0] == str(held / "scripts")
    assert captured["environment"]["PYTHONPATH"] == str(held)
    assert captured["arguments"][0] == sys.executable
