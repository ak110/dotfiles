"""pytools._internal.install_claude_plugins の推奨コマンド算出機構のテスト。

`compute_recommended_commands` と `consume_recommendations` を単体で検証する。
また、`run()` 末尾で推奨コマンドが算出され `consume_recommendations()` 経由で
取り出せること、および自動有効化・無効化のための CLI (install / enable / disable)
が発行されないことも合わせて検証する。
"""

import json

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import install_claude_plugins as _install_claude_plugins

from .helpers import _FakeResult, _plugin_list_json


class TestComputeRecommendedCommands:
    """`compute_recommended_commands` の単体テスト。"""

    _ENABLE_TARGET = "context7@claude-plugins-official"
    _DISABLE_TARGET = "serena@claude-plugins-official"

    def test_install_recommended_when_missing(self):
        """有効化対象が未インストールなら install コマンドを提案する。"""
        # pylint: disable-next=protected-access
        result = _install_claude_plugins.compute_recommended_commands([], {})
        # pylint: disable-next=protected-access
        for plugin_id in _install_claude_plugins._AUTO_ENABLED_PLUGIN_IDS:
            assert f"claude plugin install {plugin_id} --scope user" in result

    def test_enable_recommended_when_explicitly_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins=false` なら enable コマンドを提案する。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._ENABLE_TARGET}))
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset())
        raw_data = [{"id": self._ENABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        result = _install_claude_plugins.compute_recommended_commands(raw_data, {self._ENABLE_TARGET: False})
        assert result == [f"claude plugin enable {self._ENABLE_TARGET} --scope user"]

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

    def test_disable_recommended_when_installed_and_not_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """無効化対象がインストール済みかつ `enabledPlugins` で false になっていなければ disable を提案する。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset())
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({self._DISABLE_TARGET}))
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        result = _install_claude_plugins.compute_recommended_commands(raw_data, {self._DISABLE_TARGET: True})
        assert result == [f"claude plugin disable {self._DISABLE_TARGET} --scope user"]

    def test_disable_also_recommended_when_settings_missing(self, monkeypatch: pytest.MonkeyPatch):
        """settings.json 自体が無い (enabled_map=None) 環境でも disable を提案する (既定で有効扱いのため)。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset())
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({self._DISABLE_TARGET}))
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        result = _install_claude_plugins.compute_recommended_commands(raw_data, None)
        assert result == [f"claude plugin disable {self._DISABLE_TARGET} --scope user"]

    def test_disable_not_recommended_when_already_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """既に `enabledPlugins=false` なら disable を提案しない。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset())
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({self._DISABLE_TARGET}))
        raw_data = [{"id": self._DISABLE_TARGET, "scope": "user", "version": "1.0.0"}]
        assert not _install_claude_plugins.compute_recommended_commands(raw_data, {self._DISABLE_TARGET: False})

    def test_disable_not_recommended_when_not_installed(self, monkeypatch: pytest.MonkeyPatch):
        """未インストールなら disable を提案しない (installして無効化するのは過剰介入)。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset())
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_DISABLED_PLUGIN_IDS", frozenset({self._DISABLE_TARGET}))
        assert not _install_claude_plugins.compute_recommended_commands([], {})


class TestConsumeRecommendations:
    """`consume_recommendations` の取り出し&クリア動作。"""

    def test_returns_last_recommendations_and_clears(self, monkeypatch: pytest.MonkeyPatch):
        # pylint: disable-next=protected-access
        monkeypatch.setattr(_install_claude_plugins, "_LAST_RECOMMENDATIONS", ["claude plugin install a --scope user"])
        assert _install_claude_plugins.consume_recommendations() == ["claude plugin install a --scope user"]
        # 2 回目は空 (ワンショット取り出し)
        assert not _install_claude_plugins.consume_recommendations()


class TestRunNoAutomaticStateChange:
    """`run()` が install/enable/disable の CLI を発行せず、推奨コマンドを残すこと。"""

    def test_no_state_change_cli_and_recommendations_recorded(self, monkeypatch: pytest.MonkeyPatch):
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
        # 有効化対象を全て未インストール状態に絞って検証する
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
                    stdout=json.dumps([{"name": _install_claude_plugins._MARKETPLACE_NAME}]),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        _install_claude_plugins.run()

        # 自動 enable/disable/install (外部 marketplace 向け) の CLI は発行されない
        banned = (
            ["claude", "plugin", "enable"],
            ["claude", "plugin", "disable"],
            ["claude", "plugin", "install", target_enable, "--scope", "user"],
        )
        for cmd in calls:
            assert cmd[:3] != ["claude", "plugin", "enable"], f"unexpected enable call: {cmd}"
            assert cmd[:3] != ["claude", "plugin", "disable"], f"unexpected disable call: {cmd}"
            assert cmd != list(banned[2]), f"unexpected install call: {cmd}"

        recommendations = _install_claude_plugins.consume_recommendations()
        assert recommendations == [f"claude plugin install {target_enable} --scope user"]
