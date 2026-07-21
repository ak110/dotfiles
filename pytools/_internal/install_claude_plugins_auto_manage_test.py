"""pytools._internal.install_claude_plugins の自動管理機構のテスト。

`compute_recommended_commands` (install/enable 推奨) と
自動 disable 機構を `run()` 経由で検証する。
また、`run()` 末尾で推奨コマンドが算出され戻り値経由で取り出せること、
自動 disable が発火する経路、および外部 marketplace 向けの自動 install/enable
CLI が発行されないことも合わせて検証する。
"""

import json

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import _FakeResult, _plugin_list_json, make_installed_two_plugin_fake


class TestComputeRecommendedCommands:
    """`compute_recommended_commands` の単体テスト (install/enable のみ)。"""

    _ENABLE_TARGET = "context7@claude-plugins-official"

    def test_install_recommended_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        """有効化対象が未インストールなら install コマンドを提案する。"""
        # _AUTO_ENABLED_PLUGIN_IDS はモジュールレベルのグローバル定数のため、
        # 引数注入では制御不能。テスト用に1件に固定して振る舞いを確認する。
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET})
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset()
        )
        result = _install_claude_plugins.compute_recommended_commands([], {})
        assert f"claude plugin install {self._ENABLE_TARGET} --scope=user" in result

    def test_enable_recommended_when_explicitly_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins=false` なら enable コマンドを提案する。"""
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET})
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset()
        )
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        result = _install_claude_plugins.compute_recommended_commands(raw_data, {self._ENABLE_TARGET: False})
        assert result == [f"claude plugin enable {self._ENABLE_TARGET} --scope=user"]

    def test_no_recommendation_when_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ既に有効なら何も提案しない。"""
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET})
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset()
        )
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        assert not _install_claude_plugins.compute_recommended_commands(raw_data, {self._ENABLE_TARGET: True})

    def test_no_recommendation_when_key_missing(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins` に対象キーが無い (既定で有効) なら提案しない。"""
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET})
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset()
        )
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        assert not _install_claude_plugins.compute_recommended_commands(raw_data, {})


class TestAutoDisablePlugins:
    """自動 disable 機構を `run()` 経由で検証する。

    各テストは共通の前提設定をサポートメソッドで統一し、
    対象の `_AUTO_DISABLED_PLUGIN_IDS` を1件に固定して分岐を検証する。
    """

    _DISABLE_TARGET = "serena@claude-plugins-official"

    # インストールループを素通りさせるためのダミーエントリ名 (marketplace suffix なし)。
    # target_versions に1件以上ないと run() が早期リターンするため必要。
    _DUMMY_PLUGIN = "dummy"

    def _setup_run(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *,
        disable_target: str,
        installed_ids: list[str],
        enabled_map: dict[str, bool] | None,
    ) -> list[list[str]]:
        """``run()`` を最小限の前提で動かし、CLI 呼び出しリストを返す。

        ``_read_target_info`` はダミー 1 件の targets を返す設定にして dotfiles
        プラグインの install/update ループを「最新」スルーで無害化する。
        """
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        # target_versions が空だと run() が早期リターンするため1件設定する。
        # インストールループは CLI リストにダミーを含めることで「最新」スルーになる。
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({self._DUMMY_PLUGIN: None}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({disable_target})
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset()
        )
        # settings.json の読み取りを固定値で差し替える
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_enabled_plugins_from_file",
            lambda: enabled_map,
        )

        calls: list[list[str]] = []
        # ダミープラグインをインストール済み扱いにして install ループを素通りさせる。
        # id は `<name>@<marketplace>` 形式。_extract_plugin_version_map が @ 前を name として使う。
        dummy_entry = {"id": f"{self._DUMMY_PLUGIN}@{_claude_common.MARKETPLACE_NAME}", "scope": "user", "version": "1.0.0"}
        raw_list = [dummy_entry] + [{"id": pid, "scope": "user", "version": "1.0.0"} for pid in installed_ids]

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout=json.dumps(raw_list, ensure_ascii=False))
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        return calls

    def test_disable_called_when_installed_and_not_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """無効化対象がインストール済みかつ `enabledPlugins` で false でないなら disable CLI を発行する。"""
        calls = self._setup_run(
            monkeypatch,
            disable_target=self._DISABLE_TARGET,
            installed_ids=[self._DISABLE_TARGET],
            enabled_map={self._DISABLE_TARGET: True},
        )
        changed, _ = _install_claude_plugins.run()
        assert changed is True
        assert ["claude", "plugin", "disable", self._DISABLE_TARGET, "--scope=user"] in calls

    def test_disable_called_when_settings_missing(self, monkeypatch: pytest.MonkeyPatch):
        """settings.json 自体が無い (enabled_map=None) 環境でも disable CLI を発行する (既定で有効扱いのため)。"""
        calls = self._setup_run(
            monkeypatch,
            disable_target=self._DISABLE_TARGET,
            installed_ids=[self._DISABLE_TARGET],
            enabled_map=None,
        )
        changed, _ = _install_claude_plugins.run()
        assert changed is True
        assert ["claude", "plugin", "disable", self._DISABLE_TARGET, "--scope=user"] in calls

    def test_disable_skipped_when_already_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """既に `enabledPlugins=false` なら disable CLI を発行しない。"""
        calls = self._setup_run(
            monkeypatch,
            disable_target=self._DISABLE_TARGET,
            installed_ids=[self._DISABLE_TARGET],
            enabled_map={self._DISABLE_TARGET: False},
        )
        changed, _ = _install_claude_plugins.run()
        assert changed is False
        assert not any(c[:3] == ["claude", "plugin", "disable"] for c in calls)

    def test_disable_skipped_when_not_installed(self, monkeypatch: pytest.MonkeyPatch):
        """未インストールなら disable CLI を発行しない (install して無効化するのは過剰介入)。"""
        calls = self._setup_run(
            monkeypatch,
            disable_target=self._DISABLE_TARGET,
            installed_ids=[],
            enabled_map={},
        )
        changed, _ = _install_claude_plugins.run()
        assert changed is False
        assert not any(c[:3] == ["claude", "plugin", "disable"] for c in calls)

    def test_disable_failure_does_not_raise(self, monkeypatch: pytest.MonkeyPatch):
        """disable CLI が失敗しても例外は送出されず run() が正常終了する。"""
        # _setup_run() で共通前提を設定したうえで、disable 失敗の振る舞いだけ追加 monkeypatch する。
        calls = self._setup_run(
            monkeypatch,
            disable_target=self._DISABLE_TARGET,
            installed_ids=[self._DISABLE_TARGET],
            enabled_map={self._DISABLE_TARGET: True},
        )

        # _setup_run() の fake_run を上書きし、disable コマンドだけ失敗レスポンスを返す形に差し替える。
        # ダミープラグインと disable 対象の両方をインストール済み扱いにする
        raw_list = [
            {"id": f"{self._DUMMY_PLUGIN}@{_claude_common.MARKETPLACE_NAME}", "scope": "user", "version": "1.0.0"},
            {"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"},
        ]

        def fake_run_with_disable_failure(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout=json.dumps(raw_list, ensure_ascii=False))
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:3] == ["claude", "plugin", "disable"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run_with_disable_failure)

        # disable 失敗でも例外は出ない (changed は False: 成功件数 0)
        changed, _ = _install_claude_plugins.run()
        assert changed is False
        assert any(c[:3] == ["claude", "plugin", "disable"] for c in calls)


class TestRunAutoDisable:
    """`run()` から自動 disable 経路が発火するエンドツーエンドテスト。"""

    def test_disable_called_and_changed_set(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ有効な disable 対象に対し `claude plugin disable` が発行され changed が真になる。"""
        target_disable = "serena@claude-plugins-official"
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        # settings.json の読み取りは None (既定で有効扱い) で固定する
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: None)
        # 有効化対象の自動 install/enable には立ち入らないよう空集合にする
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset()
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({target_disable})
        )

        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001 -- subprocess.run 互換シグネチャ
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"},
                        {"id": target_disable, "scope": "user", "version": "1.0.0"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, recommendations = _install_claude_plugins.run()

        assert changed is True
        assert not recommendations
        assert ["claude", "plugin", "disable", target_disable, "--scope=user"] in calls


class TestRunNoAutomaticStateChange:
    """対象が空のとき、`run()` は外部 marketplace 向けの install/enable/disable CLI を発行しないこと。"""

    def test_no_state_change_cli_and_recommendations_returned(self, monkeypatch: pytest.MonkeyPatch):
        """有効化対象は未インストール、無効化対象は空集合のときに CLI 発行なしで推奨のみ返す。"""
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0", "sample-plugin": "1.0.0"}, set()),
        )
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        # settings.json の読み取りは None (未設定扱い) で固定してテスト環境差を排除する
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: None)
        # 有効化対象を全て未インストール状態に限定して検証する
        target_enable = "context7@claude-plugins-official"
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({target_enable})
        )
        monkeypatch.setattr(  # noqa: SLF001 -- グローバル定数のため引数注入では到達不能
            _install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset()
        )

        calls: list[list[str]] = []
        fake_run = make_installed_two_plugin_fake(calls)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        _changed, recommendations = _install_claude_plugins.run()

        # 自動 enable/disable/install (外部 marketplace 向け) の CLI は発行されない
        install_target_cmd = ["claude", "plugin", "install", target_enable, "--scope=user"]
        for cmd in calls:
            assert cmd[:3] != ["claude", "plugin", "enable"], f"unexpected enable call: {cmd}"
            assert cmd[:3] != ["claude", "plugin", "disable"], f"unexpected disable call: {cmd}"
            assert cmd != install_target_cmd, f"unexpected install call: {cmd}"

        assert recommendations == [f"claude plugin install {target_enable} --scope=user"]
