"""pytools._internal.install_claude_plugins のメインフローテスト。

subprocess.run / shutil.which をモックして、前提条件分岐・plugin install / update の
各パスを検証する。
"""

import json
import pathlib
import subprocess

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import _FakeResult, _plugin_list_json


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


class TestPrerequisites:
    """前提条件 (claude / uv の存在) のチェック。"""

    def test_missing_claude_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: None if name == "claude" else "/usr/bin/uv")
        assert _install_claude_plugins.run()[0] is False

    def test_missing_uv_skips(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: None if name == "uv" else "/usr/bin/claude")
        assert _install_claude_plugins.run()[0] is False


@pytest.fixture(name="disable_file_reads")
def _disable_file_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """ファイル直接読み取りを無効化し、CLIフォールバックパスを通す。

    ``is_directory_type_registered`` は既定で False (= 旧 GitHub 型残存環境の挙動)。
    directory 型経路を検証したい個別テストが必要に応じて上書きする。
    """
    monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
    monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
    monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)


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


@pytest.mark.usefixtures("fake_which_present", "fake_target_info", "disable_file_reads", "disable_auto_managed_plugins")
class TestRunFlow:
    """メインフローのテスト (前提条件は満たしている状態、CLIフォールバックパス)。

    既定で ``is_directory_type_registered`` は False (旧 GitHub 型残存環境)。
    directory 型経路の追加検証は ``TestRunFlowDirectoryType`` で行う。
    """

    def test_already_installed_up_to_date_skips(self, monkeypatch: pytest.MonkeyPatch):
        """GitHub 型残存環境: version 一致で update も install も呼ばれない (回帰防止)。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001 -- subprocess.run 互換シグネチャ
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
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "update"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1, stderr="should not be called")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is False
        assert [c for c in calls if c[:3] == ["claude", "plugin", "update"]] == []
        assert [c for c in calls if c[:3] == ["claude", "plugin", "install"]] == []

    def test_already_installed_version_drift_updates(self, monkeypatch: pytest.MonkeyPatch):
        """インストール済みでも version が古ければ refresh + update が実行される。

        片方のプラグインだけ version が古いケース。もう片方は最新のため
        update 呼び出しは古い側にのみ発行されることも併せて検証する。
        """
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.1.0", "scope": "user"},
                        {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "user"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "update"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "update"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        assert any(c[:4] == ["claude", "plugin", "marketplace", "update"] for c in calls)
        update_calls = [c for c in calls if c[:3] == ["claude", "plugin", "update"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in update_calls)
        # --scope=user が渡されていること
        assert any("--scope=user" in c for c in update_calls)
        # 最新である sample-plugin に対しては update を発行しない
        assert not any("sample-plugin@ak110-dotfiles" in c for c in update_calls)
        # refresh が update よりも先に呼ばれていること (marketplace メタデータを反映させてから update)
        refresh_index = next(i for i, c in enumerate(calls) if c[:4] == ["claude", "plugin", "marketplace", "update"])
        update_index = next(i for i, c in enumerate(calls) if c[:3] == ["claude", "plugin", "update"])
        assert refresh_index < update_index

    def test_fresh_install_happy_path(self, monkeypatch: pytest.MonkeyPatch):
        """未インストール + marketplace 未登録の場合、add → 全プラグイン install の順に呼ぶ。"""
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

        assert _install_claude_plugins.run()[0] is True
        # add が呼ばれ、かつ marketplace.json 由来の全プラグインに対し install が呼ばれていること
        assert any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)
        # --scope=user が渡されていること
        for ic in install_calls:
            assert "--scope=user" in ic

    def test_marketplace_already_registered_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        """marketplace が既に登録済みなら add は呼ばず install だけ実行される。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                # MARKETPLACE_NAME が含まれる形で返す
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # add は呼ばれていないこと
        assert [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]] == []
        # 対象プラグインは全て install されていること
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)

    def test_mixed_installed_and_missing(self, monkeypatch: pytest.MonkeyPatch):
        """片方だけインストール済みの混在状態では、未インストール側のみ install される。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "update"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # 既にインストール済みの agent-toolkit は install されない
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert not any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        # 未インストールの sample-plugin は install される
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)
        # 既にインストール済みの plugin が最新であれば update は呼ばれない
        assert [c for c in calls if c[:3] == ["claude", "plugin", "update"]] == []

    def test_empty_target_versions_skips(self, monkeypatch: pytest.MonkeyPatch):
        """marketplace.json に対象 plugin が無ければ claude コマンドを一切呼ばずにスキップする。"""
        monkeypatch.setattr(_install_claude_plugins, "_read_target_info", lambda _root: ({}, set()))
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is False
        assert not calls

    def test_plugin_list_failure_skips(self, monkeypatch: pytest.MonkeyPatch):
        """list が失敗したら後続は何もしない。"""
        seen: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            seen.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=1, stderr="boom")
            return _FakeResult(returncode=0, stdout="[]")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is False
        # list 以外は呼ばれていないこと (失敗で早期 return)
        assert all(c[:3] == ["claude", "plugin", "list"] for c in seen)

    def test_install_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """install が失敗しても例外は出ず False を返す。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=1, stderr="install failed")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is False

    def test_claude_timeout_is_swallowed(self, monkeypatch: pytest.MonkeyPatch):
        """claude CLI のタイムアウトはスキップとして扱う (post-apply を中断させない)。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            raise subprocess.TimeoutExpired(cmd, timeout=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is False

    def test_project_scope_ignored_in_version_check(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """project scope のエントリは install/update 判定に使われず、user scope へ移行される。

        project scope の uninstall は CLI が cwd 依存の判定を行うため、
        エントリの projectPath を cwd にして呼び出す。本テストでは tmp_path を
        projectPath として渡し、uninstall 呼び出し時の cwd が一致することを検証する。
        """
        calls: list[tuple[list[str], object]] = []

        def fake_run(cmd, **kwargs):  # noqa: ANN001
            calls.append((cmd, kwargs.get("cwd")))
            if cmd[:3] == ["claude", "plugin", "list"]:
                # agent-toolkit が project scope にのみ存在
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {
                            "id": "agent-toolkit@ak110-dotfiles",
                            "version": "0.2.0",
                            "scope": "project",
                            "projectPath": str(tmp_path),
                        },
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "uninstall"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # project scope のエントリは無視され、user scope に新規 install される
        install_calls = [cmd for cmd, _cwd in calls if cmd[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        # project scope の清掃 (uninstall) が tmp_path を cwd にして呼ばれること
        uninstall_calls = [(cmd, cwd) for cmd, cwd in calls if cmd[:3] == ["claude", "plugin", "uninstall"]]
        matched = [
            (cmd, cwd) for cmd, cwd in uninstall_calls if "agent-toolkit@ak110-dotfiles" in cmd and "--scope=project" in cmd
        ]
        assert matched, f"project scope 除去の呼び出しが無い: {uninstall_calls}"
        assert all(cwd == tmp_path for _cmd, cwd in matched)

    def test_project_scope_cleanup_skips_missing_dir(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path):
        """projectPath のディレクトリが存在しない場合、uninstall を呼ばずスキップする。"""
        missing = tmp_path / "does-not-exist"
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {
                            "id": "agent-toolkit@ak110-dotfiles",
                            "version": "0.2.0",
                            "scope": "project",
                            "projectPath": str(missing),
                        },
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # 存在しない projectPath に対しては uninstall を呼ばない
        assert not [c for c in calls if c[:3] == ["claude", "plugin", "uninstall"]]

    def test_deprecated_plugin_uninstalled(self, monkeypatch: pytest.MonkeyPatch):
        """deprecated プラグインがインストール済みならアンインストールされる。"""
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, {"old-plugin"}),
        )
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "old-plugin@ak110-dotfiles", "version": "0.5.0", "scope": "user"},
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            if cmd[:3] == ["claude", "plugin", "uninstall"]:
                return _FakeResult(returncode=0)
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # deprecated プラグインのアンインストールが呼ばれること
        uninstall_calls = [c for c in calls if c[:3] == ["claude", "plugin", "uninstall"]]
        assert any("old-plugin@ak110-dotfiles" in c for c in uninstall_calls)
        # 通常プラグインの install も呼ばれること
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)


@pytest.fixture(name="directory_type_env")
def _directory_type_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """directory 型登録が健全な環境を模擬する。

    ``disable_file_reads`` は既定で ``is_directory_type_registered`` を False に差し替えるため、
    directory 型経路を検証したい個別テストではこのフィクスチャで True に上書きする。
    ``disable_file_reads`` より後に適用される必要があるため、``usefixtures`` の列挙順に注意する。
    """
    monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: True)
    # ensure_marketplace はファイル検査経由で True を返すよう直結させる
    monkeypatch.setattr(_claude_marketplace, "ensure_marketplace", lambda: True)


@pytest.mark.usefixtures(
    "fake_which_present",
    "fake_target_info",
    "disable_file_reads",
    "disable_auto_managed_plugins",
    "directory_type_env",
)
class TestRunFlowDirectoryType:
    """directory 型登録が健全な環境での追加検証。

    この環境では version 乖離に依存せず、毎回 ``plugin install <plugin>@<mp> --scope=user`` を
    再実行してキャッシュを最新化する (dotfiles 実体からの同期経路)。
    ``plugin update`` と ``marketplace update`` は version 一致時 no-op になるため呼ばない。
    """

    def test_version_match_triggers_reinstall(self, monkeypatch: pytest.MonkeyPatch):
        """version 一致でも各プラグインに対して install が再実行される (directory 型キャッシュ同期)。"""
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
            if cmd[:3] == ["claude", "plugin", "install"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1, stderr=f"unexpected: {cmd}")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        # 対象プラグイン 2 件に対して install が --scope=user で再実行される
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
        # plugin update / marketplace update は呼ばれない
        assert not any(c[:3] == ["claude", "plugin", "update"] for c in calls)
        assert not any(c[:4] == ["claude", "plugin", "marketplace", "update"] for c in calls)

    def test_version_drift_still_uses_update(self, monkeypatch: pytest.MonkeyPatch):
        """version 乖離時は update 経路を経由する現行挙動の回帰防止。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=_plugin_list_json(
                        {"id": "agent-toolkit@ak110-dotfiles", "version": "0.1.0", "scope": "user"},
                        {"id": "sample-plugin@ak110-dotfiles", "version": "1.0.0", "scope": "user"},
                    ),
                )
            if cmd[:3] in (["claude", "plugin", "update"], ["claude", "plugin", "install"]):
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _install_claude_plugins.run()[0] is True
        # version が乖離している agent-toolkit は update、最新の sample-plugin は install 再実行
        update_calls = [c for c in calls if c[:3] == ["claude", "plugin", "update"]]
        assert any("agent-toolkit@ak110-dotfiles" in c for c in update_calls)
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        assert any("sample-plugin@ak110-dotfiles" in c for c in install_calls)
        # directory 型では refresh (marketplace update) は呼ばない
        assert not any(c[:4] == ["claude", "plugin", "marketplace", "update"] for c in calls)
