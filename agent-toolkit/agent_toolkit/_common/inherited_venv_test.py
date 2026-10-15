"""agent-toolkit/agent_toolkit/_common/inherited_venv.py のテスト。"""

import os

import pytest

from agent_toolkit._common import inherited_venv as _inherited_venv


class TestStripInheritedVenv:
    """`VIRTUAL_ENV`と対応する`PATH`要素だけを取り除く契約。"""

    @pytest.mark.parametrize("bin_dir_name", ["bin", "Scripts"])
    def test_removes_venv_bin_dir_for_each_platform_layout(self, bin_dir_name: str) -> None:
        """POSIXの`bin`とWindowsの`Scripts`のどちらの配置でも、対応する要素だけを取り除く。"""
        venv_root = "/tmp/launcher-venv"
        env = {
            "VIRTUAL_ENV": venv_root,
            "PATH": os.pathsep.join([f"{venv_root}/{bin_dir_name}", "/usr/local/bin", "", "/usr/bin"]),
        }
        _inherited_venv.strip_inherited_venv(env)
        assert "VIRTUAL_ENV" not in env
        assert env["PATH"] == os.pathsep.join(["/usr/local/bin", "", "/usr/bin"])

    def test_keeps_unrelated_entries_that_share_the_bin_dir_name(self) -> None:
        """除去対象は`VIRTUAL_ENV`から導いたパスに限り、同名ディレクトリを持つ他の要素は残す。"""
        venv_root = "/tmp/launcher-venv"
        env = {
            "VIRTUAL_ENV": venv_root,
            "PATH": os.pathsep.join([f"{venv_root}/bin", "/opt/other-venv/bin", "/usr/bin"]),
        }
        _inherited_venv.strip_inherited_venv(env)
        assert env["PATH"] == os.pathsep.join(["/opt/other-venv/bin", "/usr/bin"])

    def test_keeps_path_unchanged_without_virtual_env(self) -> None:
        """`VIRTUAL_ENV`が無い環境では`PATH`を変更しない。"""
        original = os.pathsep.join(["/usr/local/bin", "", "/usr/bin"])
        env: dict[str, str] = {"PATH": original}
        _inherited_venv.strip_inherited_venv(env)
        assert env["PATH"] == original

    def test_removes_virtual_env_without_path(self) -> None:
        """`PATH`が無い環境でも`VIRTUAL_ENV`は取り除き、`PATH`を新設しない。"""
        env: dict[str, str] = {"VIRTUAL_ENV": "/tmp/launcher-venv"}
        _inherited_venv.strip_inherited_venv(env)
        assert not env

    @pytest.mark.parametrize("project_environment", ["/plugin/environment", "/user/environment"])
    def test_removes_only_the_plugin_project_override(self, project_environment: str) -> None:
        """対象リポジトリのuvにはplugin専用の保存先を引き継がず、呼び出し元が別に指定した値は保つ。"""
        env = {
            "AGENT_TOOLKIT_RUNTIME_ENVIRONMENT": "/plugin/environment",
            "UV_PROJECT_ENVIRONMENT": project_environment,
            "PATH": "user-path",
        }
        _inherited_venv.strip_inherited_venv(env)
        assert "AGENT_TOOLKIT_RUNTIME_ENVIRONMENT" not in env
        assert env["PATH"] == "user-path"
        if project_environment == "/plugin/environment":
            assert "UV_PROJECT_ENVIRONMENT" not in env
        else:
            assert env["UV_PROJECT_ENVIRONMENT"] == project_environment
