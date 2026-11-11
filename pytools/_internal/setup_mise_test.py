# pylint: disable=useless-import-alias  # pytestのfixtureを試験モジュールの名前空間へ登録するため。
"""setup_miseの基本動作の試験。"""

# pylint: disable=protected-access

import subprocess
import typing
from pathlib import Path

import httpx
import pytest

from pytools._internal import setup_mise as _setup_mise
from pytools._internal import setup_mise_binary, setup_mise_common
from pytools._internal.setup_mise_test_helpers import (
    _EXPECTED_ENV_OVERRIDES,
    _GCLOUD_PLUGIN_INSTALL,
    _WINGET_MISE,
    _WINGET_UPGRADE_ARGS,
    _ls_response,
    _MiseSubprocessStub,
    _version_response,
)
from pytools._internal.setup_mise_test_helpers import _mise_stub as _mise_stub
from pytools._internal.setup_mise_test_helpers import _windows_env as _windows_env


class TestFindMiseBinary:
    """mise実行ファイルのPATH・既知パス探索を検証する。"""

    def test_returns_path_entry_as_absolute_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        mise_path = tmp_path / "bin" / "mise"
        monkeypatch.setattr(setup_mise_binary.shutil, "which", lambda _name: str(mise_path))

        assert setup_mise_binary.find_mise_binary() == mise_path

    def test_returns_linux_known_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        mise_path = tmp_path / ".local" / "bin" / "mise"
        mise_path.parent.mkdir(parents=True)
        mise_path.write_bytes(b"mise")
        monkeypatch.setattr(setup_mise_binary.shutil, "which", lambda _name: None)
        monkeypatch.setattr(setup_mise_common, "is_windows", lambda: False)
        monkeypatch.setattr(_setup_mise.Path, "home", lambda: tmp_path)

        assert setup_mise_binary.find_mise_binary() == mise_path

    def test_returns_windows_known_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        localappdata = tmp_path / "AppData" / "Local"
        mise_path = localappdata / "Microsoft" / "WinGet" / "Links" / "mise.exe"
        mise_path.parent.mkdir(parents=True)
        mise_path.write_bytes(b"mise")
        monkeypatch.setattr(setup_mise_binary.shutil, "which", lambda _name: None)
        monkeypatch.setattr(setup_mise_common, "is_windows", lambda: True)
        monkeypatch.setenv("LOCALAPPDATA", str(localappdata))

        assert setup_mise_binary.find_mise_binary() == mise_path

    def test_returns_none_when_not_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setattr(setup_mise_binary.shutil, "which", lambda _name: None)
        monkeypatch.setattr(setup_mise_common, "is_windows", lambda: False)
        monkeypatch.setattr(_setup_mise.Path, "home", lambda: tmp_path)

        assert setup_mise_binary.find_mise_binary() is None


class TestRunMiseInstallation:
    """mise未検出時の自動導入と再解決を検証する。"""

    def test_run_installs_mise_then_continues(self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub):
        binaries = iter((None, Path("/fake/mise")))
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: next(binaries))
        installs: list[bool] = []

        def install() -> bool:
            installs.append(True)
            return True

        monkeypatch.setattr(setup_mise_binary, "ensure_mise_installed", install)

        assert _setup_mise.run().changed is True
        assert installs == [True]
        assert mise_stub.calls_for("install")

    def test_run_skips_when_install_does_not_resolve(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: None)
        installs: list[bool] = []

        def install() -> bool:
            installs.append(True)
            return False

        monkeypatch.setattr(setup_mise_binary, "ensure_mise_installed", install)

        assert _setup_mise.run().changed is False
        assert installs == [True]
        assert not mise_stub.records

    def test_run_does_not_install_when_mise_is_resolved(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(
            setup_mise_binary,
            "ensure_mise_installed",
            lambda: pytest.fail("導入済み環境でインストーラーを呼んだ"),
        )
        assert _setup_mise.run().changed is True
        assert mise_stub.calls_for("install")

    def test_run_skips_when_installer_download_fails(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: None)

        class _FailingClient:
            def __enter__(self) -> typing.Self:
                return self

            def __exit__(self, *_error: object) -> None:
                self.close()

            def get(self, _url: str) -> typing.NoReturn:
                raise httpx.ConnectError("offline")

            def close(self) -> None:
                return None

        monkeypatch.setattr(setup_mise_binary.httpx, "Client", lambda **_kwargs: _FailingClient())
        assert _setup_mise.run().changed is False
        assert not mise_stub.records

    def test_run_installs_mise_with_winget_on_windows(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        binaries = iter((None, Path("/fake/mise")))
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: next(binaries))
        monkeypatch.setattr(setup_mise_common, "is_windows", lambda: True)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)

        assert _setup_mise.run().changed is True
        assert [record["cmd"] for record in mise_stub.records].count(
            [
                "winget",
                "install",
                "--accept-package-agreements",
                "--accept-source-agreements",
                "--disable-interactivity",
                "jdx.mise",
            ]
        ) == 1
        assert mise_stub.calls_for("install")


class TestEnsureMiseUpToDate:
    """`mise self-update -y` の呼び出しと失敗時の後続ステップ継続を検証する。"""

    def test_self_update_invoked(self, mise_stub: _MiseSubprocessStub):
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{"version": "24"}]})

        assert _setup_mise.run().changed is True

        self_update_calls = mise_stub.calls_for("self-update")
        assert self_update_calls and self_update_calls[0]["args"] == ["self-update", "-y"]
        assert self_update_calls[0]["env_overrides"] == _EXPECTED_ENV_OVERRIDES

    def test_self_update_failure_does_not_block_install(self, mise_stub: _MiseSubprocessStub, caplog: pytest.LogCaptureFixture):
        """`self-update` の失敗は警告として残し、後続ステップは継続する。"""
        mise_stub.handlers[("self-update", "-y")] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="mise ERROR not available via package manager"
        )
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

        with caplog.at_level("WARNING"):
            _setup_mise.run()

        assert any("self-update" in record.getMessage() for record in caplog.records if record.levelname == "WARNING")
        assert mise_stub.calls_for("install")

    def test_self_update_none_result_does_not_block_install(self, mise_stub: _MiseSubprocessStub):
        """タイムアウト・例外で`None`が返っても後続ステップは継続する。"""
        mise_stub.handlers[("self-update", "-y")] = None
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

        _setup_mise.run()

        assert mise_stub.calls_for("install")


class TestUpgradeWingetManagedMise:
    """WinGetで導入したmiseは`self-update`の代わりに`winget upgrade`で更新する。"""

    @pytest.fixture(autouse=True)
    def _windows_winget_mise(self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub) -> None:
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: _WINGET_MISE)
        monkeypatch.setattr(setup_mise_common, "is_windows", lambda: True)
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

    def _winget_upgrades(self, mise_stub: _MiseSubprocessStub) -> list[dict[str, typing.Any]]:
        return [record for record in mise_stub.records if record["cmd"] == ["winget", *_WINGET_UPGRADE_ARGS]]

    def test_winget_upgrade_replaces_self_update(self, mise_stub: _MiseSubprocessStub) -> None:
        _setup_mise.run()

        assert len(self._winget_upgrades(mise_stub)) == 1
        assert not mise_stub.calls_for("self-update")
        assert mise_stub.calls_for("install")

    @pytest.mark.parametrize("returncode", [0x8A15002B, 0x8A15002B - (1 << 32)])
    def test_update_not_applicable_is_not_warned(
        self, mise_stub: _MiseSubprocessStub, caplog: pytest.LogCaptureFixture, returncode: int
    ) -> None:
        mise_stub.handlers[tuple(_WINGET_UPGRADE_ARGS)] = subprocess.CompletedProcess(
            args=[], returncode=returncode, stdout="No applicable update found.", stderr=""
        )

        with caplog.at_level("WARNING"):
            _setup_mise.run()

        assert not [record for record in caplog.records if record.levelname == "WARNING"]
        assert mise_stub.calls_for("install")

    def test_upgrade_failure_warns_and_continues(
        self, mise_stub: _MiseSubprocessStub, caplog: pytest.LogCaptureFixture
    ) -> None:
        mise_stub.handlers[tuple(_WINGET_UPGRADE_ARGS)] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="network error"
        )

        with caplog.at_level("WARNING"):
            _setup_mise.run()

        assert any("winget upgrade" in record.getMessage() for record in caplog.records if record.levelname == "WARNING")
        assert mise_stub.calls_for("install")

    def test_mise_outside_winget_uses_self_update(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        """PATHで公式インストーラー版のmiseが先行する場合は、その実体を`self-update`で更新する。"""
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: Path(r"C:\Users\user\AppData\Local\mise\bin\mise.exe"))

        _setup_mise.run()

        assert not self._winget_upgrades(mise_stub)
        assert mise_stub.calls_for("self-update")

    @pytest.mark.parametrize(
        "path",
        [
            r"C:\Users\winget\AppData\Local\mise\bin\mise.exe",
            r"C:\tools\winget\mise.exe",
        ],
    )
    def test_unrelated_winget_directory_is_not_winget_managed(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub, path: str
    ) -> None:
        """WinGetの配置先以外にある`winget`という名前のディレクトリでは`self-update`を使う。"""
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: Path(path))

        _setup_mise.run()

        assert not self._winget_upgrades(mise_stub)
        assert mise_stub.calls_for("self-update")

    def test_winget_packages_directory_is_winget_managed(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        monkeypatch.setattr(
            setup_mise_binary,
            "find_mise_binary",
            lambda: Path(r"C:\Users\user\AppData\Local\Microsoft\WinGet\Packages\jdx.mise_Microsoft.Winget.Source\mise.exe"),
        )

        _setup_mise.run()

        assert len(self._winget_upgrades(mise_stub)) == 1
        assert not mise_stub.calls_for("self-update")

    def test_new_winget_install_is_updated_with_winget(
        self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub
    ) -> None:
        """WinGetで新規導入した直後も、同じ実行の更新は`self-update`ではなく`winget upgrade`を使う。"""
        binaries = iter((None, _WINGET_MISE))
        monkeypatch.setattr(_setup_mise, "find_mise_binary", lambda: next(binaries))

        _setup_mise.run()

        commands = [record["cmd"][:2] for record in mise_stub.records]
        assert commands.index(["winget", "install"]) < commands.index(["winget", "upgrade"])
        assert not mise_stub.calls_for("self-update")
        assert mise_stub.calls_for("install")


class TestEnsureGcloudPluginUsable:
    """同梱gcloudプラグインが壊れたmiseの版でだけ、Git配布のプラグインへ置き換える。"""

    @pytest.fixture(autouse=True)
    def _global_node(self, mise_stub: _MiseSubprocessStub) -> None:
        mise_stub.handlers[("ls", "--global", "--json")] = _ls_response({"node": [{}]})

    @pytest.mark.parametrize("version", ["2026.10.20", "2026.10.21"])
    def test_broken_version_installs_git_plugin_before_install(self, mise_stub: _MiseSubprocessStub, version: str) -> None:
        mise_stub.handlers[("--version",)] = _version_response(version)

        assert _setup_mise.run().changed is True

        args = [record["args"] for record in mise_stub.records]
        assert list(_GCLOUD_PLUGIN_INSTALL) in args
        assert args.index(list(_GCLOUD_PLUGIN_INSTALL)) < args.index(["install"])

    @pytest.mark.parametrize("version", ["2026.10.12", "2026.10.22"])
    def test_unaffected_version_keeps_embedded_plugin(self, mise_stub: _MiseSubprocessStub, version: str) -> None:
        mise_stub.handlers[("--version",)] = _version_response(version)

        _setup_mise.run()

        assert not mise_stub.calls_for("plugins")

    def test_installed_git_plugin_is_not_reinstalled(self, mise_stub: _MiseSubprocessStub) -> None:
        mise_stub.handlers[("--version",)] = _version_response("2026.10.20")
        mise_stub.handlers[("plugins", "ls", "--urls")] = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="gcloud  https://github.com/jdx/vfox-gcloud  HEAD 3144d84\n", stderr=""
        )

        _setup_mise.run()

        assert not mise_stub.calls_for(*_GCLOUD_PLUGIN_INSTALL)

    def test_plugin_install_failure_warns_and_continues(
        self, mise_stub: _MiseSubprocessStub, caplog: pytest.LogCaptureFixture
    ) -> None:
        mise_stub.handlers[("--version",)] = _version_response("2026.10.20")
        mise_stub.handlers[_GCLOUD_PLUGIN_INSTALL] = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="mise ERROR git clone failed"
        )

        with caplog.at_level("WARNING"):
            _setup_mise.run()

        assert any("gcloud" in record.getMessage() for record in caplog.records if record.levelname == "WARNING")
        assert mise_stub.calls_for("install")

    def test_minimal_mode_skips_plugin(self, monkeypatch: pytest.MonkeyPatch, mise_stub: _MiseSubprocessStub) -> None:
        """minimalはgcloudを導入しないため、プラグインの置き換えもしない。"""
        monkeypatch.setattr(_setup_mise.dotfiles_mode, "is_minimal", lambda: True)
        mise_stub.handlers[("--version",)] = _version_response("2026.10.20")

        _setup_mise.run()

        assert not mise_stub.calls_for("plugins")
