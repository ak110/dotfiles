"""pytools._internal.install_claude_plugins のテスト。

`claude plugin list` 出力フォーマット解析・marketplace CLI フォールバック・
旧 GitHub 型マイグレーション・marketplace.json 読み込みを検証する。
"""

# pylint: disable=protected-access

import json
import logging
import pathlib
import sys

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import claude_plugin_references as _claude_plugin_references
from pytools._internal import claude_plugin_settings
from pytools._internal import common as _common
from pytools._internal import install_claude_plugins as _install_claude_plugins

from ._test_helpers import (
    ResidentProcesses,
    _FakeResult,
    command_matches,
    make_fresh_install_fake,
    plugin_management_response,
)
from ._test_helpers import _fake_target_info as _fake_target_info  # pylint: disable=useless-import-alias
from ._test_helpers import _fake_which_present as _fake_which_present  # pylint: disable=useless-import-alias

_REAL_SCAN = _claude_plugin_references.scan


def test_plugin_mutations_use_management_commands_only(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def run(arguments: list[str], **_kwargs: object) -> _FakeResult:
        calls.append(arguments)
        return _FakeResult()

    monkeypatch.setattr(_claude_common, "run_claude", run)
    assert _install_claude_plugins._install_plugin("sample")
    assert _install_claude_plugins._update_plugin("sample")
    assert claude_plugin_settings.disable_plugin("sample@example") is None
    assert calls == [
        ["plugin", "install", "sample@ak110-dotfiles", "--scope=user", "-y"],
        ["plugin", "update", "sample@ak110-dotfiles", "--scope=user"],
        ["plugin", "disable", "sample@example", "--scope=user"],
    ]


@pytest.fixture(autouse=True)
def _isolate_plugin_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_install_claude_plugins.external_web_guidance, "run", lambda _host: False)
    monkeypatch.setattr(_install_claude_plugins.external_pstack, "run", lambda: False)
    monkeypatch.setattr(_install_claude_plugins, "_remove_unused_plugins", lambda: False)
    monkeypatch.setattr(_install_claude_plugins, "_verify_target_plugins", lambda _targets, _failed, _disabled: None)
    monkeypatch.setattr(_claude_plugin_references, "scan", lambda: ())


def test_installed_plugins_are_read_after_marketplace_is_ensured(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(_install_claude_plugins, "_prerequisites_ok", lambda: True)
    monkeypatch.setattr(_install_claude_plugins, "_remove_unused_plugins", lambda: False)
    monkeypatch.setattr(_common, "find_dotfiles_root", lambda: tmp_path)
    monkeypatch.setattr(_install_claude_plugins, "_read_target_info", lambda _root: ({}, {"legacy"}))

    def ensure_marketplace() -> bool:
        events.append("ensure")
        return True

    def read_installed_plugins() -> list[object]:
        events.append("read")
        return []

    monkeypatch.setattr(_claude_marketplace, "ensure_marketplace", ensure_marketplace)
    monkeypatch.setattr(
        _install_claude_plugins,
        "_read_installed_plugins_from_file",
        read_installed_plugins,
    )
    monkeypatch.setattr(_install_claude_plugins, "_uninstall_deprecated", lambda _name, _raw: False)
    monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
    monkeypatch.setattr(claude_plugin_settings, "read_enabled_plugins", lambda: {})
    monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
    monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])

    _install_claude_plugins.run()

    assert events == ["ensure", "read"]


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
        monkeypatch.setattr(_common, "resolve_executable", lambda name, **_kwargs: pathlib.Path(name))
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

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if command_matches(cmd, ["claude", "plugin", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        [{"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "user"}],
                        ensure_ascii=False,
                    ),
                )
            if command_matches(cmd, ["claude", "plugin", "marketplace", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed

        assert changed is False

    def test_project_scope_entry_is_ignored(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if command_matches(cmd, ["claude", "plugin", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        [{"id": "agent-toolkit@ak110-dotfiles", "version": "0.2.0", "scope": "project"}],
                        ensure_ascii=False,
                    ),
                )
            return plugin_management_response(cmd, (("install",), ("uninstall",)))

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed

        assert changed is True
        assert any("agent-toolkit@ak110-dotfiles" in c for c in calls if command_matches(c, ["claude", "plugin", "install"]))

    def test_plugins_key_format_is_parsed(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if command_matches(cmd, ["claude", "plugin", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        {"plugins": [{"name": "agent-toolkit", "version": "0.2.0", "scope": "user"}]},
                        ensure_ascii=False,
                    ),
                )
            if command_matches(cmd, ["claude", "plugin", "marketplace", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed

        assert changed is False
        assert not any(command_matches(c, ["claude", "plugin", "install"]) for c in calls)

    def test_version_missing_treated_as_outdated(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if command_matches(cmd, ["claude", "plugin", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps(
                        [{"id": "agent-toolkit@ak110-dotfiles", "scope": "user"}],
                        ensure_ascii=False,
                    ),
                )
            return plugin_management_response(cmd, (("marketplace", "update"), ("update",)))

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed
        assert changed is True
        assert any(command_matches(c, ["claude", "plugin", "update"]) for c in calls)

    def test_empty_list_results_in_full_install(self, monkeypatch: pytest.MonkeyPatch):
        calls: list[list[str]] = []
        fake_run = make_fresh_install_fake(calls)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed
        assert changed is True
        assert any(command_matches(c, ["claude", "plugin", "install"]) for c in calls)


@pytest.mark.usefixtures("fake_which_present")
class TestEnsureMarketplaceCliPath:
    def test_already_registered_by_name_skips_add(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):

        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", tmp_path / "known.json")
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if command_matches(cmd, ["claude", "plugin", "marketplace", "list"]):
                return _FakeResult(
                    returncode=0,
                    stdout=json.dumps([{"name": _claude_common.MARKETPLACE_NAME}], ensure_ascii=False),
                )
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        assert _claude_marketplace.ensure_marketplace() is True
        assert [c for c in calls if command_matches(c, ["claude", "plugin", "marketplace", "remove"])] == []
        assert [c for c in calls if command_matches(c, ["claude", "plugin", "marketplace", "add"])] == []

    def test_not_registered_calls_add_with_dotfiles_absolute_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):

        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", tmp_path / "known.json")
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", tmp_path / "settings.json")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if command_matches(cmd, ["claude", "plugin", "marketplace", "list"]):
                return _FakeResult(returncode=0, stdout="[]")
            if command_matches(cmd, ["claude", "plugin", "marketplace", "add"]):
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        assert _claude_marketplace.ensure_marketplace() is True
        add_calls = [c for c in calls if command_matches(c, ["claude", "plugin", "marketplace", "add"])]
        assert len(add_calls) == 1
        dotfiles_root = _common.find_dotfiles_root()
        assert dotfiles_root is not None
        assert command_matches(
            add_calls[0],
            ["claude", "plugin", "marketplace", "add", str(dotfiles_root), "--scope=user"],
        )


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

        assert _claude_marketplace.is_directory_type_registered() is False

        # CLI remove+add は settings 側を更新しない再現環境として成功のみ返す
        monkeypatch.setattr(
            _common,
            "run_subprocess",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        assert _claude_marketplace.ensure_marketplace() is True

        dotfiles_root = _common.find_dotfiles_root()
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
        tmp_path: pathlib.Path,
    ):
        marketplace = tmp_path / ".claude-plugin/marketplace.json"
        marketplace.parent.mkdir()
        marketplace.write_text(json.dumps({"plugins": [{"name": "agent-toolkit", "version": "0.2.0"}]}), encoding="utf-8")
        installed = tmp_path / "claude/plugins/installed_plugins.json"
        installed.parent.mkdir(parents=True)
        installed.write_text('{"plugins": {}}', encoding="utf-8")
        settings = tmp_path / "claude/settings.json"
        settings.write_text("{}", encoding="utf-8")
        monkeypatch.setattr(_common, "find_dotfiles_root", lambda: tmp_path)
        monkeypatch.setattr(_claude_common, "INSTALLED_PLUGINS_PATH", installed)
        monkeypatch.setattr(_claude_common, "SETTINGS_JSON_PATH", settings)
        monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", settings)
        monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", installed.with_name("known_marketplaces.json"))
        # ファイル直接読み取りは無効化して CLI フォールバックを通す
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_claude_marketplace, "_check_marketplace_from_file", lambda: None)  # noqa: SLF001  # pylint: disable=protected-access  # 引数注入では到達不能（グローバル状態の差し替え）
        monkeypatch.setattr(_claude_marketplace, "is_directory_type_registered", lambda: False)
        monkeypatch.setattr(_install_claude_plugins, "_auto_disable_plugins", lambda _raw, _enabled: (0, 0))
        monkeypatch.setattr(_install_claude_plugins, "compute_recommended_commands", lambda _raw, _enabled: [])
        monkeypatch.setattr(_common, "resolve_executable", lambda name, **_kwargs: pathlib.Path(name))
        calls: list[list[str]] = []
        fake_run = make_fresh_install_fake(calls)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed
        assert changed is True
        install_calls = [c for c in calls if command_matches(c, ["claude", "plugin", "install"])]

        assert any("agent-toolkit@ak110-dotfiles" in c for c in install_calls)
        assert len(install_calls) == 1

    def test_missing_file_skips_without_cli_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
    ):
        """marketplace.json がない場合は対象 plugin なしでスキップし、claude CLI を一切呼ばない。"""
        monkeypatch.setattr(_install_claude_plugins, "_read_installed_plugins_from_file", lambda: None)
        monkeypatch.setattr(_common, "resolve_executable", lambda name, **_kwargs: pathlib.Path(name))
        # dotfiles_root を marketplace.json が存在しない tmp_path に差し替える
        monkeypatch.setattr(_common, "find_dotfiles_root", lambda: tmp_path)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        changed = _install_claude_plugins.run().changed
        assert changed is False
        assert not calls


class TestUninstallDeprecated:
    _NAME = "old-plugin"

    def test_uninstall_keeps_plugin_data(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`--keep-data`を付けて除去し、稼働中のセッションのhook・MCPが使う永続データ領域を残す。

        `claude plugin uninstall`は導入先の実体を孤立として残し、永続データ領域だけを削除する
        （Claude Code 2.1.303の隔離環境での観測）。
        """
        raw_data = [{"id": f"{self._NAME}@{_claude_common.MARKETPLACE_NAME}", "version": "1.0.0", "scope": "user"}]
        calls: list[list[str]] = []

        def fake_run_claude(args: list[str], **_kwargs: object) -> _FakeResult:
            calls.append(args)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common, "run_claude", fake_run_claude)

        assert _install_claude_plugins._uninstall_deprecated(self._NAME, raw_data) is True
        assert calls == [["plugin", "uninstall", f"{self._NAME}@{_claude_common.MARKETPLACE_NAME}", "--keep-data"]]


class TestEnsurePluginCacheComplete:
    _NAME = "agent-toolkit"

    def _prepare(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, *, install_path: pathlib.Path, name: str | None = None
    ) -> None:
        name = name or self._NAME
        installed = tmp_path / "plugins" / "installed_plugins.json"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_text(
            json.dumps(
                {
                    "version": 2,
                    "plugins": {
                        f"{name}@{_claude_common.MARKETPLACE_NAME}": [
                            {"scope": "user", "version": "1.0.0", "installPath": str(install_path)}
                        ]
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(_claude_common, "INSTALLED_PLUGINS_PATH", installed)

    def _write_cache(self, cache_dir: pathlib.Path, *, name: str | None = None, missing: tuple[str, ...] = ()) -> None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for relative in _install_claude_plugins._plugin_cache_required_files(name or self._NAME):
            if relative in missing:
                continue
            target = cache_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")

    def _cache_dir(self, tmp_path: pathlib.Path) -> pathlib.Path:
        return tmp_path / "plugins" / "cache" / "ak110-dotfiles" / self._NAME / "1.0.0"

    def test_complete_cache_does_not_reinstall(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        cache_dir = self._cache_dir(tmp_path)
        self._prepare(monkeypatch, tmp_path, install_path=cache_dir)
        self._write_cache(cache_dir)
        monkeypatch.setattr(
            _install_claude_plugins,
            "_install_plugin",
            lambda _name: pytest.fail("必須ファイルが揃う状態で再インストールを起動した"),
        )

        assert _install_claude_plugins._ensure_plugin_cache_complete(self._NAME) == "unchanged"

    def test_skill_only_cache_does_not_require_python_project(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        name = "agent-basics"
        cache_dir = tmp_path / "plugins" / "cache" / "ak110-dotfiles" / name / "1.0.0"
        self._prepare(monkeypatch, tmp_path, install_path=cache_dir, name=name)
        self._write_cache(cache_dir, name=name)
        monkeypatch.setattr(
            _install_claude_plugins,
            "_install_plugin",
            lambda _name: pytest.fail("スキル専用pluginのcache修復を起動した"),
        )

        assert _install_claude_plugins._ensure_plugin_cache_complete(name) == "unchanged"

    def test_missing_file_triggers_repair_install(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        cache_dir = self._cache_dir(tmp_path)
        self._prepare(monkeypatch, tmp_path, install_path=cache_dir)
        self._write_cache(cache_dir, missing=("scripts/launch.py.lock",))
        calls: list[str] = []

        def fake_install(name: str) -> bool:
            calls.append(name)
            assert not cache_dir.exists()
            self._write_cache(cache_dir)
            return True

        monkeypatch.setattr(_install_claude_plugins, "_install_plugin", fake_install)

        assert _install_claude_plugins._ensure_plugin_cache_complete(self._NAME) == "repaired"
        assert calls == [self._NAME]
        assert _install_claude_plugins._missing_plugin_cache_files(self._NAME, cache_dir) == []

    @pytest.mark.skipif(sys.platform != "linux", reason="実行名をsymlinkで与える常駐プロセスはLinuxで起動する")
    @pytest.mark.parametrize("holder", ["mcp-cwd", "claude-session"])
    def test_referenced_cache_defers_repair_until_reference_ends(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        holder: str,
    ) -> None:
        """欠落した導入先を参照するプロセスか稼働中のClaude Codeがある間は削除せず延期し、終了後の再実行で修復する。

        修復は導入先を削除してから再導入するため、参照中に進むと残っているhook・MCPの実体まで消える。
        """
        monkeypatch.setattr(_claude_plugin_references, "scan", _REAL_SCAN)
        cache_dir = self._cache_dir(tmp_path)
        self._prepare(monkeypatch, tmp_path, install_path=cache_dir)
        self._write_cache(cache_dir, missing=("scripts/launch.py.lock",))
        (cache_dir / "agent_toolkit").mkdir()
        (cache_dir / "agent_toolkit" / "hook.py").write_text("in use", encoding="utf-8")
        calls: list[str] = []

        def fake_install(name: str) -> bool:
            calls.append(name)
            assert not cache_dir.exists()
            self._write_cache(cache_dir)
            return True

        monkeypatch.setattr(_install_claude_plugins, "_install_plugin", fake_install)
        with ResidentProcesses(tmp_path, monkeypatch) as residents:
            if holder == "mcp-cwd":
                residents.start("python3", cwd=cache_dir / "agent_toolkit")
            else:
                residents.start("claude", cwd=tmp_path)
            with caplog.at_level(logging.WARNING):
                assert _install_claude_plugins._ensure_plugin_cache_complete(self._NAME) == "deferred"
            assert not calls
            assert (cache_dir / "agent_toolkit" / "hook.py").read_text(encoding="utf-8") == "in use"
            assert "scripts/launch.py.lock" in caplog.text
            assert str(cache_dir.relative_to(tmp_path)) in caplog.text
            assert "更新担当はセッションの終了を待ち" in caplog.text
        assert "`update-dotfiles`を再実行する" in caplog.text

        assert _install_claude_plugins._ensure_plugin_cache_complete(self._NAME) == "repaired"
        assert calls == [self._NAME]
        assert _install_claude_plugins._missing_plugin_cache_files(self._NAME, cache_dir) == []

    def test_still_missing_after_repair_fails_with_file_names(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
    ) -> None:
        """再インストール後も欠落する場合は、欠落ファイル名を含む失敗で終える。"""
        cache_dir = self._cache_dir(tmp_path)
        self._prepare(monkeypatch, tmp_path, install_path=cache_dir)
        self._write_cache(cache_dir, missing=("scripts/launch.py",))
        calls: list[str] = []

        def fake_install(name: str) -> bool:
            calls.append(name)
            self._write_cache(cache_dir, missing=("scripts/launch.py",))
            return True

        monkeypatch.setattr(_install_claude_plugins, "_install_plugin", fake_install)

        with pytest.raises(RuntimeError, match="scripts/launch.py"):
            _install_claude_plugins._ensure_plugin_cache_complete(self._NAME)
        assert calls == [self._NAME]

    def test_failed_repair_install_fails(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
        """修復の再インストール自体が失敗した場合も、成功として扱わない。"""
        cache_dir = self._cache_dir(tmp_path)
        self._prepare(monkeypatch, tmp_path, install_path=cache_dir)
        self._write_cache(cache_dir, missing=("scripts/launch.py.lock",))
        monkeypatch.setattr(_install_claude_plugins, "_install_plugin", lambda _name: False)

        with pytest.raises(RuntimeError, match="修復インストールに失敗"):
            _install_claude_plugins._ensure_plugin_cache_complete(self._NAME)

    def test_unresolvable_install_path_skips_check(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        installed = tmp_path / "plugins" / "installed_plugins.json"
        installed.parent.mkdir(parents=True, exist_ok=True)
        installed.write_text(json.dumps({"version": 2, "plugins": {}}), encoding="utf-8")
        monkeypatch.setattr(_claude_common, "INSTALLED_PLUGINS_PATH", installed)
        monkeypatch.setattr(
            _install_claude_plugins,
            "_install_plugin",
            lambda _name: pytest.fail("導入先を解決できない状態で再インストールを起動した"),
        )

        with caplog.at_level(logging.WARNING):
            assert _install_claude_plugins._ensure_plugin_cache_complete(self._NAME) == "unchanged"

        assert str(installed) in caplog.text

    def test_install_path_outside_cache_is_not_deleted(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level("INFO")
        outside = tmp_path / "elsewhere" / "1.0.0"
        self._prepare(monkeypatch, tmp_path, install_path=outside)
        self._write_cache(outside, missing=("scripts/launch.py.lock",))
        monkeypatch.setattr(
            _install_claude_plugins,
            "_install_plugin",
            lambda _name: pytest.fail("cache の外にある導入先で再インストールを起動した"),
        )

        with caplog.at_level(logging.INFO):
            assert _install_claude_plugins._ensure_plugin_cache_complete(self._NAME) == "unchanged"

        assert outside.is_dir()
        assert str(outside) in caplog.text
