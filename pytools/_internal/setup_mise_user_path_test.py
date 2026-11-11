# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""setup_miseのwindowsの試験。"""

# pylint: disable=protected-access

import os
import typing
from pathlib import Path

import pytest

from pytools._internal import setup_mise as _setup_mise
from pytools._internal.setup_mise_test_helpers import _expected_env, _ls_response, _MiseSubprocessStub, _WinregFake
from pytools._internal.setup_mise_test_helpers import _mise_stub as _mise_stub
from pytools._internal.setup_mise_test_helpers import _windows_env as _windows_env


class TestRunWindowsPathSetup:
    """Windows 分岐: ユーザー PATH への shims 追加と既存検出を `run()` 経由で検証する。"""

    def test_appends_shims_when_missing(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (r"C:\Windows", _WinregFake.REG_SZ)
        _setup_mise.run()
        writes = windows_env["writes"]
        assert len(writes) == 1
        assert writes[0]["name"] == "Path"
        assert writes[0]["value"] == r"C:\Windows;%LOCALAPPDATA%\mise\shims"
        # REG_SZ で保持されていても REG_EXPAND_SZ へ昇格させる
        assert writes[0]["type"] == _WinregFake.REG_EXPAND_SZ
        assert windows_env["broadcasts"] == 1

    def test_avoids_duplicate_separator(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (r"C:\Windows;", _WinregFake.REG_EXPAND_SZ)
        _setup_mise.run()
        assert windows_env["writes"][0]["value"] == r"C:\Windows;%LOCALAPPDATA%\mise\shims"

    def test_appends_from_empty(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = ("", _WinregFake.REG_EXPAND_SZ)
        _setup_mise.run()
        assert windows_env["writes"][0]["value"] == r"%LOCALAPPDATA%\mise\shims"

    def test_already_registered_literal_entry(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (
            r"C:\Windows;%LOCALAPPDATA%\mise\shims",
            _WinregFake.REG_EXPAND_SZ,
        )
        _setup_mise.run()
        assert not windows_env["writes"]
        assert windows_env["broadcasts"] == 0

    def test_already_registered_expanded_case_insensitive(self, windows_env: dict[str, typing.Any]):
        # %LOCALAPPDATA% 展開済みかつ大小不一致のエントリを既登録として認識する
        expanded = str(windows_env["shims_dir"]).upper()
        windows_env["existing"] = (f"C:\\WINDOWS;{expanded}", _WinregFake.REG_EXPAND_SZ)
        _setup_mise.run()
        assert not windows_env["writes"]

    def test_already_registered_in_system_path(self, windows_env: dict[str, typing.Any]):
        """システム側に同じディレクトリがあればユーザー側へ書き込まない。"""
        windows_env["existing"] = (r"C:\Windows", _WinregFake.REG_EXPAND_SZ)
        windows_env["system"] = str(windows_env["shims_dir"])
        _setup_mise.run()
        assert not windows_env["writes"]

    def test_not_present_when_other_paths(self, windows_env: dict[str, typing.Any]):
        windows_env["existing"] = (
            r"C:\Windows;C:\Users\x\AppData\Local\Programs\Python",
            _WinregFake.REG_EXPAND_SZ,
        )
        _setup_mise.run()
        assert len(windows_env["writes"]) == 1


class TestWindowsProcessPathPriority:
    """現プロセスPATHの既存優先順位を維持する。"""

    def test_appends_shims_after_existing_node_path(
        self,
        windows_env: dict[str, typing.Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """既存Nodeのパスをmise shimsより前に保つ。"""
        node_path = r"C:\DATA\Apps\node"
        monkeypatch.setenv("PATH", node_path)

        _setup_mise.run()

        assert os.environ["PATH"] == os.pathsep.join((node_path, str(windows_env["shims_dir"])))

    def test_sets_shims_without_separator_when_path_empty(
        self,
        windows_env: dict[str, typing.Any],
        monkeypatch: pytest.MonkeyPatch,
    ):
        """空PATHではshimsだけを設定する。"""
        monkeypatch.setenv("PATH", "")

        _setup_mise.run()

        assert os.environ["PATH"] == str(windows_env["shims_dir"])


class TestWindowsMiseSubprocessEnvironment:
    """Windowsのmise子プロセスが管理対象の.NET共有ルートを優先することを検証する。"""

    def test_prepends_default_dotnet_root(
        self,
        windows_env: dict[str, typing.Any],
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del windows_env
        system_dotnet = r"C:\Program Files\dotnet"
        monkeypatch.delenv("MISE_DOTNET_ROOT", raising=False)
        monkeypatch.delenv("MISE_DATA_DIR", raising=False)
        monkeypatch.setenv("PATH", system_dotnet)
        mise_data_dir = Path(os.environ["LOCALAPPDATA"]) / "mise"
        monkeypatch.setattr(_setup_mise.platformdirs, "user_data_dir", lambda *_args, **_kwargs: str(mise_data_dir))

        _setup_mise.run()

        expected_root = str(mise_data_dir / "dotnet-root")
        for record in mise_stub.records:
            env_overrides = record["env_overrides"]
            assert env_overrides["MISE_DOTNET_ROOT"] == expected_root
            assert env_overrides["PATH"].split(";") == [expected_root, system_dotnet]

    def test_respects_explicit_dotnet_root_without_duplication(
        self,
        windows_env: dict[str, typing.Any],
        mise_stub: _MiseSubprocessStub,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        del windows_env
        custom_root = r"D:\tools\dotnet"
        monkeypatch.setenv("MISE_DOTNET_ROOT", custom_root)
        monkeypatch.setenv("PATH", f"{custom_root};C:\\Program Files\\dotnet;{custom_root.upper()}")

        _setup_mise.run()

        for record in mise_stub.records:
            env_overrides = record["env_overrides"]
            assert env_overrides["MISE_DOTNET_ROOT"] == custom_root
            assert env_overrides["PATH"].split(";") == [custom_root, r"C:\Program Files\dotnet"]


class TestNonInteractiveEnvInjection:
    """全mise CLI呼び出しに無人セットアップ用の環境変数が注入されることを確認する。

    aqua/npm バックエンドの初回ダウンロード時に確認プロンプトでブロックする事象を
    防ぎ、版一覧取得の上限を延長するため、trust・ls・installの各サブコマンドが
    env_overrides付きで呼ばれる。
    """

    def test_all_mise_invocations_receive_env_overrides(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
        mise_stub: _MiseSubprocessStub,
    ):
        (tmp_path / "mise.toml").write_text("[tools]\n", encoding="utf-8")
        monkeypatch.setenv("CHEZMOI_WORKING_TREE", str(tmp_path))
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{"version": "24"}]})

        _setup_mise.run()

        assert mise_stub.calls_for("trust"), "mise trust が呼ばれていない"
        assert mise_stub.calls_for("ls", "--global", "--json"), "mise ls が呼ばれていない"
        assert mise_stub.calls_for("install"), "mise install が呼ばれていない"
        for record in mise_stub.records:
            assert record["env_overrides"] == _expected_env(record)
