"""pytools._internal.install_claude_plugins のテスト。

`claude plugin list` 出力フォーマット解析・marketplace CLI フォールバック・
旧 GitHub 型マイグレーション・marketplace.json 読み込みを検証する。
"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import _FakeResult, make_fresh_install_fake


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


class TestExtractPluginVersionMap:
    """`claude plugin list --json` のパース結果が run() の install/update 判定に正しく反映される。

    各形式の出力を ``_read_installed_plugins_from_file`` を None 返しにして CLI フォールバックから
    注入し、実際の install/update/skip 挙動で検証する。
    """

    @pytest.fixture(autouse=True)
    def _setup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """共通の前提設定: which は通し、ファイル読み取りは無効化し、自動管理は no-op にする。"""
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        # テストを実際の marketplace.json から切り離す
        monkeypatch.setattr(
            _install_claude_plugins,
            "_read_target_info",
            lambda _root: ({"agent-toolkit": "0.2.0"}, set()),
        )

    def test_user_scope_entry_is_installed(self, monkeypatch: pytest.MonkeyPatch):
        """user scope のエントリが installed として認識され、version 一致で update されない。"""

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        [{"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"}],
                        ensure_ascii=False,
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        # version 一致のため changed は False
        assert changed is False

    def test_project_scope_entry_is_ignored(self, monkeypatch: pytest.MonkeyPatch):
        """project scope のエントリは user scope 管理の installed として扱われず install される。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        [{"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "project"}],
                        ensure_ascii=False,
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

        changed, _ = _install_claude_plugins.run()
        # project scope は user scope 用のインストール判定から外れるため install が発行される
        assert changed is True
        assert any("agent-toolkit@ak110-dotfiles" in c for c in calls if c[:3] == ["claude", "plugin", "install"])

    def test_plugins_key_format_is_parsed(self, monkeypatch: pytest.MonkeyPatch):
        """{plugins: [...]} の入れ子形式は name で agent-toolkit を認識し install しない。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                # {"plugins": [{"name": ..., "version": ..., "scope": "user"}]} 形式
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        {"plugins": [{"name": "agent-toolkit", "version": "0.2.0", "scope": "user"}]},
                        ensure_ascii=False,
                    ),
                )
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        # version 一致のため install は発行されない
        assert changed is False
        assert not any(c[:3] == ["claude", "plugin", "install"] for c in calls)

    def test_version_missing_treated_as_outdated(self, monkeypatch: pytest.MonkeyPatch):
        """version フィールドが無いエントリは空文字列扱いとなり target と不一致で update される。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:3] == ["claude", "plugin", "list"]:
                # version なし (空文字列扱い) で scope=user
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        [{"id": "agent-toolkit@ak110-dotfiles", "scope": "user"}],
                        ensure_ascii=False,
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

        changed, _ = _install_claude_plugins.run()
        assert changed is True
        assert any(c[:3] == ["claude", "plugin", "update"] for c in calls)

    def test_empty_list_results_in_full_install(self, monkeypatch: pytest.MonkeyPatch):
        """空リストは全プラグイン未インストール扱いとなり install が発行される。"""
        calls: list[list[str]] = []
        fake_run = make_fresh_install_fake(calls)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        assert changed is True
        assert any(c[:3] == ["claude", "plugin", "install"] for c in calls)


class TestEnsureMarketplaceCliPath:
    """ensure_marketplace の CLI フォールバックパス (ファイル検査が None の場合)。

    両ファイルが存在しない状態で CLI 経由の marketplace 登録フローを検証する。
    """

    def test_already_registered_by_name_skips_add(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """marketplace list で name が検出できれば CLI add を呼ばない。"""
        # 両ファイルが存在しない → _check_marketplace_from_file が None を返す
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", tmp_path / "known.json")
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _claude_marketplace.ensure_marketplace() is True
        assert [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "remove"]] == []
        assert [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]] == []

    def test_not_registered_calls_add_with_dotfiles_absolute_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """marketplace list が空なら dotfiles 絶対パス + --scope=user で add を呼ぶ。"""
        # 両ファイルが存在しない → _check_marketplace_from_file が None を返す
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", tmp_path / "known.json")
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _claude_marketplace.ensure_marketplace() is True
        add_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]]
        assert len(add_calls) == 1
        dotfiles_root = _claude_common.find_dotfiles_root()
        assert dotfiles_root is not None
        assert add_calls[0] == [
            "claude",
            "plugin",
            "marketplace",
            "add",
            str(dotfiles_root),
            "--scope=user",
        ]


class TestLegacyGithubTypeMigration:
    """install-claude.sh bootstrap が残した旧 GitHub 型エントリが directory 型へ
    自動マイグレーションされることを検証する。

    ``_check_marketplace_from_file`` が旧形式として検出し、修復フローで
    ``known_marketplaces.json`` と ``settings.json.extraKnownMarketplaces`` を
    directory 型 (dotfiles 絶対パス) へ書き換えることを検証する。
    """

    def test_legacy_github_entry_is_unhealthy_and_repaired(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        known = tmp_path / "known_marketplaces.json"
        settings = tmp_path / "settings.json"
        # install-claude.sh bootstrap 直後の状態 (旧 GitHub 型)
        known.write_text(
            json.dumps(
                {
                    _claude_common.MARKETPLACE_NAME: {
                        "source": {"source": "github", "repo": "ak110/dotfiles"},
                        "lastUpdated": "2026-01-01T00:00:00.000Z",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        settings.write_text(
            json.dumps(
                {
                    "extraKnownMarketplaces": {
                        _claude_common.MARKETPLACE_NAME: {
                            "source": {"source": "github", "repo": "ak110/dotfiles"},
                        },
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", known)
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", settings)

        # 検査は旧形式として破損判定されるはず (is_directory_type_registered は False を返す)
        assert _claude_marketplace.is_directory_type_registered() is False

        # CLI remove+add は settings 側を更新しない再現環境として成功のみ返す
        monkeypatch.setattr(
            _claude_common.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        assert _claude_marketplace.ensure_marketplace() is True

        dotfiles_root = _claude_common.find_dotfiles_root()
        assert dotfiles_root is not None

        known_data = json.loads(known.read_text(encoding="utf-8"))
        entry = known_data[_claude_common.MARKETPLACE_NAME]
        assert entry["source"] == {"source": "directory", "path": str(dotfiles_root)}
        assert entry["installLocation"] == str(dotfiles_root)

        settings_data = json.loads(settings.read_text(encoding="utf-8"))
        assert settings_data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME] == {
            "source": {"source": "directory", "path": str(dotfiles_root)},
        }


class TestReadTargetInfo:
    """marketplace.json の読み込みが run() の install 対象選択に反映されること。"""

    def test_reads_actual_marketplace_json(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """本リポジトリ配下の marketplace.json が読み取られ agent-toolkit が install 対象に含まれる。

        fake_target_info フィクスチャを使わず実ファイルを読ませる。
        """
        # ファイル直接読み取りは無効化して CLI フォールバックを通す
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        calls: list[list[str]] = []
        fake_run = make_fresh_install_fake(calls)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        assert changed is True
        install_calls = [c for c in calls if c[:3] == ["claude", "plugin", "install"]]
        # 実際の marketplace.json の agent-toolkit が install 対象として現れる
        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)

    def test_missing_file_skips_without_cli_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """marketplace.json がない場合は対象 plugin なしでスキップし、claude CLI を一切呼ばない。"""
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_install_claude_plugins.shutil, "which", lambda name: f"/usr/bin/{name}")
        # dotfiles_root を marketplace.json が存在しない tmp_path に差し替える
        monkeypatch.setattr(_claude_common, "find_dotfiles_root", lambda: tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        changed, _ = _install_claude_plugins.run()
        assert changed is False
        assert not calls
