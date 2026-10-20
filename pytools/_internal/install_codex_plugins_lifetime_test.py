"""公開plugin更新のhook照会で、読取準備失敗後も子とパイプを回収する。"""

import contextlib
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from pytools._internal import codex_processes, common, install_codex_plugins, install_codex_plugins_test

plugin_env_fixture = install_codex_plugins_test.plugin_env_fixture


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_plugin_update_reclaims_hook_process_after_reader_start_failure(
    plugin_env: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest, error_type: type[BaseException]
) -> None:
    """起動したapp-serverの読取準備が失敗しても、公開更新の終了時に資源が残らない。"""
    monkeypatch.setattr(install_codex_plugins, "_UNUSED_PLUGINS", ())
    monkeypatch.setattr(codex_processes, "codex_processes", lambda: ())
    calls = 0

    def state(args: list[str], **_kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        if "marketplace" in args:
            return {"marketplaces": [{"name": "ak110-dotfiles", "root": str(plugin_env)}]}
        calls += 1
        return {
            "installed": [
                {"pluginId": "agent-toolkit@ak110-dotfiles", "enabled": True, "version": "1.0" if calls == 1 else "1.2.3"}
            ]
        }

    monkeypatch.setattr(install_codex_plugins, "_codex_json", state)
    monkeypatch.setattr(install_codex_plugins, "_command", lambda _args, **_kwargs: True)
    real_popen = subprocess.Popen
    children: list[subprocess.Popen[str]] = []
    rescue = contextlib.ExitStack()
    request.addfinalizer(rescue.close)

    def popen(_args: list[str], **kwargs: Any) -> subprocess.Popen[str]:
        # 呼出元の文脈マネージャーへ所有を渡し、回収後の実pipeを検査する。
        child = real_popen([sys.executable, "-c", "import sys; sys.stdin.read()"], **kwargs)  # pylint: disable=consider-using-with
        children.append(child)
        for stream in (child.stdin, child.stdout):
            if stream is not None:
                rescue.callback(stream.close)
        rescue.callback(common.stop_process_tree, child)
        return child

    def fail_start(_reader: threading.Thread, **_kwargs: Any) -> None:
        raise error_type("reader start failure")

    monkeypatch.setattr(subprocess, "Popen", popen)
    monkeypatch.setattr(threading.Thread, "start", fail_start)
    if error_type is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            install_codex_plugins.run()
    else:
        assert not install_codex_plugins.run().changed
    assert len(children) == 1
    child = children[0]
    assert child.poll() is not None
    assert child.stdin is not None and child.stdin.closed
    assert child.stdout is not None and child.stdout.closed
