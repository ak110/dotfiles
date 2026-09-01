"""agent-toolkit/scripts/_inherited_venv.py のテスト。"""

import os

import _inherited_venv
import pytest


class TestStripInheritedVenv:
    """`VIRTUAL_ENV`と対応する`PATH`要素だけを取り除く契約。"""

    @pytest.mark.parametrize("bin_dir_name", ["bin", "Scripts"])
    def test_removes_venv_bin_dir_for_each_platform_layout(self, bin_dir_name: str) -> None:
        """POSIXの`bin`とWindowsの`Scripts`のいずれのレイアウトでも当該要素だけを除く。"""
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
