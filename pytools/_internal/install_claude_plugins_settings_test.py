"""pytools._internal.install_claude_plugins の設定ファイル読み取りテスト。

installed_plugins.json・known_marketplaces.json の直接読み取りパスと
directory 型健全環境での統合動作を検証する。
"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import _FakeResult


@pytest.fixture(name="fake_which_present")
def _fake_which_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """claude と uv の両方が存在する状態に見せかける。"""
    monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")


@pytest.fixture(name="fake_target_info")
def _fake_target_info(monkeypatch: pytest.MonkeyPatch) -> None:
    """marketplace.json の読み込み結果を固定値に差し替える。

    テストを実際の marketplace.json の内容から切り離すため。
    対象プラグインはハードコードではなく marketplace.json 由来で決まるため、
    複数プラグインが正しくループで処理されることを検証できるよう 2 件返す。
    """
    monkeypatch.setattr(
        _install_claude_plugins,
        "_read_target_info",
        lambda _root: ({"agent-toolkit": "0.2.0", "sample-plugin": "1.0.0"}, set()),
    )


@pytest.fixture(name="disable_auto_managed_plugins")
def _disable_auto_managed_plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """自動 disable 実行と推奨コマンド算出を no-op に差し替える。

    既存テストは ak110-dotfiles marketplace のプラグインだけを対象にしているため、
    外部 marketplace を参照する自動 disable と推奨コマンド算出は専用テストへ委ねる。
    ``_auto_disable_plugins`` / ``compute_recommended_commands`` の振る舞いは
    ``install_claude_plugins_auto_manage_test.py`` で単体検証している。
    """
    monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
    monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])


class TestReadInstalledFromFile:
    """installed_plugins.json の直接読み取りが run() のインストール判定に反映されること。"""

    def test_normal_read_version_match_skips_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """正常な installed_plugins.json を読み取り、version 一致でインストールをスキップする。"""
        path = tmp_path / "installed_plugins.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        "agent-toolkit@ak110-dotfiles": [
                            {"scope": "user", "version": "0.2.0"},
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_install_claude_plugins, "_INSTALLED_PLUGINS_PATH", path)
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        assert changed is False
        assert not any(c[:3] == ["claude", "plugin", "install"] for c in calls)

    def test_file_not_found_falls_back_to_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """ファイルが存在しない場合は CLI フォールバックで plugin list が呼ばれる。"""
        monkeypatch.setattr(_install_claude_plugins, "_INSTALLED_PLUGINS_PATH", tmp_path / "missing.json")
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        assert changed is True
        # CLI フォールバックとして plugin list が呼ばれている
        assert any(c[:3] == ["claude", "plugin", "list"] for c in calls)

    def test_invalid_json_falls_back_to_cli(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """不正な JSON の場合は CLI フォールバックで動作する。"""
        path = tmp_path / "installed_plugins.json"
        path.write_text("{invalid", encoding="utf-8")
        monkeypatch.setattr(_install_claude_plugins, "_INSTALLED_PLUGINS_PATH", path)
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        assert changed is True
        assert any(c[:3] == ["claude", "plugin", "list"] for c in calls)

    def test_mixed_scopes_user_scope_only_in_version_check(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """複数スコープのエントリがある場合、user scope のみがバージョン確認の対象になる。"""
        path = tmp_path / "installed_plugins.json"
        path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        "agent-toolkit@ak110-dotfiles": [
                            {"scope": "user", "version": "0.2.0"},
                            {"scope": "project", "version": "0.1.0", "projectPath": "/some/path"},
                        ],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_install_claude_plugins, "_INSTALLED_PLUGINS_PATH", path)
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            # project scope の uninstall は成功を返す
            if cmd[:3] == ["claude", "plugin", "uninstall"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        _install_claude_plugins.run()
        # user scope で version 一致のため install/update は発行されない
        assert not any(c[:3] == ["claude", "plugin", "install"] for c in calls)
        assert not any(c[:3] == ["claude", "plugin", "update"] for c in calls)


class TestCheckMarketplaceFromFile:
    """marketplace ファイル検査が is_directory_type_registered() の結果に正しく反映されること。

    settings.json を含む 2 ファイル同時検査と修復ロジックの詳細テストは
    ``install_claude_plugins_repair_test.py`` に置いている。
    """

    def test_directory_type_healthy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """directory 型 + dotfiles 絶対パスなら is_directory_type_registered が True を返す。"""
        dotfiles_root = _claude_common.find_dotfiles_root()
        assert dotfiles_root is not None
        path = tmp_path / "known_marketplaces.json"
        path.write_text(
            json.dumps(
                {
                    _claude_common.MARKETPLACE_NAME: {
                        "source": {"source": "directory", "path": str(dotfiles_root)},
                        "installLocation": str(dotfiles_root),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", path)
        # 実環境の settings.json に依存しないよう、存在しないパスへ差し替える
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_legacy_github_type_unhealthy(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """旧 GitHub 型エントリは is_directory_type_registered が False を返す（マイグレーション対象）。"""
        path = tmp_path / "known_marketplaces.json"
        path.write_text(
            json.dumps(
                {
                    _claude_common.MARKETPLACE_NAME: {
                        "source": {"source": "github", "repo": "ak110/dotfiles"},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", path)
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_marketplace_not_registered(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """marketplace キーが存在しない場合 is_directory_type_registered は False を返す。"""
        path = tmp_path / "known_marketplaces.json"
        path.write_text(json.dumps({"other-marketplace": {}}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", path)
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_file_not_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """ファイルが存在しない場合 is_directory_type_registered は False を返す。"""
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", tmp_path / "missing.json")
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        assert _claude_marketplace.is_directory_type_registered() is False


@pytest.mark.usefixtures("fake_which_present", "fake_target_info", "disable_auto_managed_plugins")
class TestHappyPathDirectoryType:
    """directory 型登録が健全かつ全プラグイン最新の環境での挙動を検証する統合テスト。

    directory 型ではバージョン一致時も dotfiles 側編集を反映するため
    ``plugin install`` を再実行する (キャッシュ同期目的)。
    ``marketplace list``・``plugin list``・``plugin update`` などの余計な CLI は呼ばれない。
    """

    def test_directory_type_healthy_resyncs_via_install(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """directory 型健全 + version 一致の場合、各プラグインに対して install が再実行される。"""
        # installed_plugins.json: 全プラグインが最新
        installed_path = tmp_path / "installed_plugins.json"
        installed_path.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        "agent-toolkit@ak110-dotfiles": [{"scope": "user", "version": "0.2.0"}],
                        "sample-plugin@ak110-dotfiles": [{"scope": "user", "version": "1.0.0"}],
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_install_claude_plugins, "_INSTALLED_PLUGINS_PATH", installed_path)

        # known_marketplaces.json: directory 型 + dotfiles 絶対パスで正常登録済み
        dotfiles_root = _claude_common.find_dotfiles_root()
        assert dotfiles_root is not None
        marketplace_path = tmp_path / "known_marketplaces.json"
        marketplace_path.write_text(
            json.dumps(
                {
                    _claude_common.MARKETPLACE_NAME: {
                        "source": {"source": "directory", "path": str(dotfiles_root)},
                        "installLocation": str(dotfiles_root),
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", marketplace_path)
        # 実環境の settings.json に依存しないよう、存在しないパスへ差し替える。
        # `_claude_marketplace._SETTINGS_JSON_PATH` は `_check_marketplace_from_file` の参照先、
        # `_claude_common.SETTINGS_JSON_PATH` は `_read_enabled_plugins_from_file` の参照先
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        monkeypatch.setattr(_claude_common, "SETTINGS_JSON_PATH", tmp_path / "settings.json")

        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            raise AssertionError(f"予期しない subprocess 呼び出し: {cmd}")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # 全プラグインに対して install が --scope=user で再実行される
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert [
            "claude",
            "plugin",
            "install",
            "agent-toolkit@ak110-dotfiles",
            "--scope=user",
        ] in install_calls
        assert [
            "claude",
            "plugin",
            "install",
            "sample-plugin@ak110-dotfiles",
            "--scope=user",
        ] in install_calls
        # marketplace update / plugin update は呼ばれない
        assert not any(c[:4] == ["claude", "plugin", "marketplace", "update"] for c in calls)
        assert not any(c[:3] == ["claude", "plugin", "update"] for c in calls)
