"""pytools._install_claude_plugins の自動有効化・自動無効化機構のテスト。

`_auto_disable_plugins` と `_auto_install_and_enable_plugins` の各パスを
単体で検証する。`run()` 末尾でこの 2 関数が想定順序で呼ばれることも併せて
検証する (自動管理処理はすべての install/update 試行の後に走らせるため)。
"""

import json

import pytest

from pytools import _install_claude_plugins


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _plugin_list_json(*entries: dict[str, object]) -> str:
    """テスト用の `claude plugin list --json` 出力を組み立てる。"""
    return json.dumps(list(entries))


class TestAutoDisablePlugins:
    """`_auto_disable_plugins` の単体テスト。

    ユーザーが使わない公式プラグインを `claude plugin disable` で無効化するが、
    未インストール・既に disabled の場合は CLI を呼ばずスキップすることが中心。
    """

    # 代表対象として _AUTO_DISABLED_PLUGIN_IDS に含まれるプラグインを1件選ぶ。
    # 他の対象も同じロジックで動くため、単体テストは1つで十分。
    _TARGET = "serena@claude-plugins-official"

    def test_noop_when_not_installed(self, monkeypatch: pytest.MonkeyPatch):
        """対象プラグインが未インストールなら disable CLI を呼ばない。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {})

        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_disable_plugins([])
        assert [c for c in calls if c[:3] == ["claude", "plugin", "disable"]] == []

    def test_noop_when_already_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """enabledPlugins で既に false なら disable CLI を呼ばない。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {self._TARGET: False})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_disable_plugins(raw_data)
        assert [c for c in calls if c[:3] == ["claude", "plugin", "disable"]] == []

    def test_disables_when_installed_and_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ enabledPlugins=true なら `disable --scope user` を呼ぶ。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {self._TARGET: True})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_disable_plugins(raw_data)
        disable_calls = [c for c in calls if c[:3] == ["claude", "plugin", "disable"]]
        assert disable_calls == [["claude", "plugin", "disable", self._TARGET, "--scope", "user"]]

    def test_disables_when_enabled_plugins_key_missing(self, monkeypatch: pytest.MonkeyPatch):
        """enabledPlugins に対象キーが無い (デフォルト有効) なら disable を呼ぶ。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_disable_plugins(raw_data)
        assert any(c[:3] == ["claude", "plugin", "disable"] and self._TARGET in c for c in calls)

    def test_disables_when_settings_missing(self, monkeypatch: pytest.MonkeyPatch):
        """settings.json 自体が無い環境では disable を呼んで確実に無効化する。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: None)

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_disable_plugins(raw_data)
        assert any(c[:3] == ["claude", "plugin", "disable"] and self._TARGET in c for c in calls)

    def test_disable_failure_does_not_raise(self, monkeypatch: pytest.MonkeyPatch):
        """disable CLI 失敗時も例外を送出せず続行する (post-apply を落とさない)。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "plugin", "disable"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {self._TARGET: True})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_disable_plugins(raw_data)  # 例外が飛ばなければ成功


class TestAutoInstallAndEnablePlugins:
    """`_auto_install_and_enable_plugins` の単体テスト。

    未インストールなら install、インストール済みでも `enabledPlugins=false` なら
    enable で有効化する。既に有効 (true または未設定) ならスキップ。

    `_AUTO_ENABLED_PLUGIN_IDS` の具体値に依存する `enable`・`noop` 系テストは、
    定数を `{_TARGET}` 1 件に差し替えて対象の追加に強いようにしている。
    `test_installs_when_missing` だけは定数の現行集合すべてについて install が
    呼ばれることを確認したいので差し替えない。
    """

    _TARGET = "context7@claude-plugins-official"

    def test_installs_when_missing(self, monkeypatch: pytest.MonkeyPatch):
        """未インストールなら `claude plugin install <id> --scope user` を呼ぶ。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {})

        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_install_and_enable_plugins([])
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        # 全ての自動有効化対象について `<id> --scope user` 形式で install が呼ばれる
        # pylint: disable-next=protected-access
        for plugin_id in _install_claude_plugins._AUTO_ENABLED_PLUGIN_IDS:
            assert ["claude", "plugin", "install", plugin_id, "--scope", "user"] in install_calls

    def test_enables_when_explicitly_disabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins=false` なら enable を呼ぶ。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._TARGET}))
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {self._TARGET: False})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_install_and_enable_plugins(raw_data)
        enable_calls = [c for c in calls if c[:3] == ["claude", "plugin", "enable"]]
        assert enable_calls == [["claude", "plugin", "enable", self._TARGET, "--scope", "user"]]
        # 既にインストール済みなので install は呼ばれない
        assert [c for c in calls if c[:3] == ["claude", "plugin", "install"]] == []

    def test_noop_when_enabled(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins=true` なら CLI を呼ばない。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._TARGET}))
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {self._TARGET: True})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_install_and_enable_plugins(raw_data)
        assert not [c for c in calls if c[:3] in (["claude", "plugin", "install"], ["claude", "plugin", "enable"])]

    def test_noop_when_enabled_plugins_key_missing(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みかつ `enabledPlugins` に対象キーが無い (デフォルト有効) なら CLI を呼ばない。"""
        monkeypatch.setattr(_install_claude_plugins, "_AUTO_ENABLED_PLUGIN_IDS", frozenset({self._TARGET}))
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {})

        raw_data = [{"id": self._TARGET, "scope": "user", "version": "1.0.0"}]
        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_install_and_enable_plugins(raw_data)
        assert not [c for c in calls if c[:3] in (["claude", "plugin", "install"], ["claude", "plugin", "enable"])]

    def test_install_failure_does_not_raise(self, monkeypatch: pytest.MonkeyPatch):
        """install CLI 失敗時も例外を送出せず続行する (post-apply を落とさない)。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)
        monkeypatch.setattr(_install_claude_plugins, "_read_enabled_plugins_from_file", lambda: {})

        # pylint: disable-next=protected-access
        _install_claude_plugins._auto_install_and_enable_plugins([])  # 例外が飛ばなければ成功


class TestRunAutoManagedInvocationOrder:
    """`run()` 末尾で auto-install-and-enable → auto-disable の順で呼ばれることを検証する。"""

    def test_both_auto_managers_invoked_in_order(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0", "sample-plugin": "1.0.0"}, set()),
        )
        # ファイル直接読み取りを無効化し、CLI フォールバック経由で stdout を検証する
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_install_claude_plugins, "_check_marketplace_from_file", lambda: None)

        order: list[str] = []

        def fake_install_and_enable(_raw: object) -> None:
            order.append("auto_install_and_enable")

        def fake_disable(_raw: object) -> None:
            order.append("auto_disable")

        monkeypatch.setattr(_install_claude_plugins, "_auto_install_and_enable_plugins", fake_install_and_enable)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", fake_disable)

        # install/update が走らない最小モック (全プラグイン最新 + marketplace 登録済み)
        def fake_run(cmd, **_kwargs):  # noqa: ANN001
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

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        _install_claude_plugins.run()
        assert order == ["auto_install_and_enable", "auto_disable"]
