"""pytools._internal.install_claude_plugins の自動管理機構のテスト。

`compute_recommended_commands` (install/enable 推奨) と
`_auto_disable_plugins` (disable 自動実行) を単体で検証する。
また、`run()` 末尾で推奨コマンドが算出され戻り値経由で取り出せること、
自動 disable が発火する経路、および外部 marketplace 向けの自動 install/enable
CLI が発行されないことも合わせて検証する。
"""

import json

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import _FakeResult, _plugin_list_json


class TestComputeRecommendedCommands:
    """`compute_recommended_commands` の単体テスト (install/enable のみ)。"""

    _ENABLE_TARGET = "context7@claude-plugins-official"

    def test_install_recommended_when_missing(self):
        """有効化対象が未インストールなら install コマンドを提案する。"""
        # pylint: disable-next=protected-access
        result = _install_claude_plugins.compute_recommended_commands([], {})
        # pylint: disable-next=protected-access
        for plugin_id in _install_claude_plugins._AUTO_ENABLED_PLUGIN_IDS:
            assert f"claude plugin install {plugin_id} --scope=user" in result

    def test_enable_recommended_when_explicitly_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins=false` なら enable コマンドを提案する。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET}))
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset())
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        result = _install_claude_plugins.compute_recommended_commands(raw_data, {self._ENABLE_TARGET: False})
        assert result == [f"claude plugin enable {self._ENABLE_TARGET} --scope=user"]

    def test_no_recommendation_when_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ既に有効なら何も提案しない。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET}))
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset())
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        assert not _install_claude_plugins.compute_recommended_commands(raw_data, {self._ENABLE_TARGET: True})

    def test_no_recommendation_when_key_missing(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins` に対象キーが無い (既定で有効) なら提案しない。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET}))
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset())
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        assert not _install_claude_plugins.compute_recommended_commands(raw_data, {})


class TestAutoDisablePlugins:
    """`_auto_disable_plugins` の単体テスト。"""

    _DISABLE_TARGET = "serena@claude-plugins-official"

    @pytest.fixture(name="single_disable_target")
    def _single_disable_target(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_AUTO_DISABLED_PLUGIN_IDS` を1件に限定する共通fixture。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({self._DISABLE_TARGET}))

    def _record_calls(self, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
        """`subprocess.run` の呼び出しを記録するモックを設定し、呼び出しリストを返す。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001 -- subprocess.run 互換シグネチャ
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        return calls

    @pytest.mark.usefixtures("single_disable_target")
    def test_disable_called_when_installed_and_not_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """無効化対象がインストール済みかつ `enabledPlugins` で false でないなら disable CLI を発行する。"""
        calls = self._record_calls(monkeypatch)
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        disabled, failed = _install_claude_plugins._auto_disable_plugins(raw_data, {self._DISABLE_TARGET: True})
        assert (disabled, failed) == (1, 0)
        assert calls == [["claude", "plugin", "disable", self._DISABLE_TARGET, "--scope=user"]]

    @pytest.mark.usefixtures("single_disable_target")
    def test_disable_called_when_settings_missing(self, monkeypatch: pytest.MonkeyPatch):
        """settings.json 自体が無い (enabled_map=None) 環境でも disable CLI を発行する (既定で有効扱いのため)。"""
        calls = self._record_calls(monkeypatch)
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        disabled, failed = _install_claude_plugins._auto_disable_plugins(raw_data, None)
        assert (disabled, failed) == (1, 0)
        assert calls == [["claude", "plugin", "disable", self._DISABLE_TARGET, "--scope=user"]]

    @pytest.mark.usefixtures("single_disable_target")
    def test_disable_skipped_when_already_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """既に `enabledPlugins=false` なら disable CLI を発行しない。"""
        calls = self._record_calls(monkeypatch)
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        disabled, failed = _install_claude_plugins._auto_disable_plugins(raw_data, {self._DISABLE_TARGET: False})
        assert (disabled, failed) == (0, 0)
        assert not calls

    @pytest.mark.usefixtures("single_disable_target")
    def test_disable_skipped_when_not_installed(self, monkeypatch: pytest.MonkeyPatch):
        """未インストールなら disable CLI を発行しない (installして無効化するのは過剰介入)。"""
        calls = self._record_calls(monkeypatch)
        # pylint: disable-next=protected-access
        disabled, failed = _install_claude_plugins._auto_disable_plugins([], {})
        assert (disabled, failed) == (0, 0)
        assert not calls

    @pytest.mark.usefixtures("single_disable_target")
    def test_disable_failure_counted(self, monkeypatch: pytest.MonkeyPatch):
        """disable CLI が失敗したら failed 件数として集計し、他対象は続行する。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001 -- subprocess.run 互換シグネチャ
            calls.append(cmd)
            return _FakeResult(returncode=1, stderr="boom")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        disabled, failed = _install_claude_plugins._auto_disable_plugins(raw_data, None)
        assert (disabled, failed) == (0, 1)
        assert calls == [["claude", "plugin", "disable", self._DISABLE_TARGET, "--scope=user"]]


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
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)
        # settings.json の読み取りは None (既定で有効扱い) で固定する
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: None)
        # 有効化対象の自動 install/enable には立ち入らないよう空集合にする
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset())
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({target_disable}))

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
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}], ensure_ascii=False),
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
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)
        # settings.json の読み取りは None (未設定扱い) で固定してテスト環境差を排除する
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: None)
        # 有効化対象を全て未インストール状態に限定して検証する
        target_enable = "context7@claude-plugins-official"
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({target_enable}))
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset())

        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"},
                        {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "user"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    # pylint: disable-next=protected-access
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        _changed, recommendations = _install_claude_plugins.run()

        # 自動 enable/disable/install (外部 marketplace 向け) の CLI は発行されない
        install_target_cmd = ["claude", "plugin", "install", target_enable, "--scope=user"]
        for cmd in calls:
            assert cmd[:3] != ["claude", "plugin", "enable"], f"unexpected enable call: {cmd}"
            assert cmd[:3] != ["claude", "plugin", "disable"], f"unexpected disable call: {cmd}"
            assert cmd != install_target_cmd, f"unexpected install call: {cmd}"

        assert recommendations == [f"claude plugin install {target_enable} --scope=user"]
