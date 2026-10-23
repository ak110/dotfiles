"""外部pyfltrへplugin専用のuv環境を渡さないことを検証する。"""

import os
from types import SimpleNamespace

import pytest

from agent_toolkit._common import inherited_venv, pyfltr_entry


@pytest.mark.parametrize("caller", [None, "", "対象リポジトリの環境"])
def test_pyfltr_restores_caller_project_environment(monkeypatch, caller):
    """MCP内の対象プロジェクト操作が共有plugin環境を更新しないようにする。"""
    monkeypatch.setenv(inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY, "/plugin/environment")
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", "/plugin/environment")
    monkeypatch.delenv(inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY, raising=False)
    if caller is not None:
        monkeypatch.setenv(inherited_venv.CALLER_PROJECT_ENVIRONMENT_KEY, caller)

    def invoke():
        assert os.environ.get("UV_PROJECT_ENVIRONMENT") == caller
        assert inherited_venv.PLUGIN_RUNTIME_ENVIRONMENT_KEY not in os.environ
        return 7

    entry = SimpleNamespace(group="console_scripts", name="pyfltr", load=lambda: invoke)
    monkeypatch.setattr(pyfltr_entry.importlib.metadata, "distribution", lambda name: SimpleNamespace(entry_points=[entry]))
    assert pyfltr_entry.main() == 7
