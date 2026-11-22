"""pytools._internal.claude_marketplace の marketplace 修復ロジックのテスト。

install-claude.sh bootstrap が残した旧 GitHub 型登録を directory 型へ
自動マイグレーションする挙動を検証する。基本的な marketplace ファイル検査の
動作は install_claude_plugins_test.py 側に残しており、本ファイルでは 2 ファイル同時検査・
修復に関するケースを扱う。
"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace
from pytools._internal import common as _common

from ._test_helpers import _FakeResult, command_matches, write_known_entry, write_settings_entry


@pytest.fixture(autouse=True)
def _resolve_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    """Claude CLIをテスト用のコマンド名へ固定する。"""
    monkeypatch.setattr(_common, "resolve_executable", lambda name, **_kwargs: pathlib.Path(name))


@pytest.fixture(name="dotfiles_root")
def _dotfiles_root() -> pathlib.Path:
    root = _common.find_dotfiles_root()
    assert root is not None, "dotfiles ルートが検出できない環境ではテストを実行できない"
    return root


@pytest.fixture(name="marketplace_paths")
def _marketplace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    """known_marketplaces.json と settings.json のパスを tmp_path へ差し替える。

    2 ファイルを同時に点検・修復するため、両方をテスト専用パスへ差し替えて
    実環境の ``~/.claude/settings.json`` を参照しない状態を構築する。
    """
    known = tmp_path / "known_marketplaces.json"
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", known)
    monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", settings)
    return known, settings


class TestIsEntryHealthy:
    """marketplace エントリの健全性が is_directory_type_registered() の結果に反映されること。

    directory 型 + dotfiles 絶対パスの時だけ健全 (True) であることを、
    known_marketplaces.json に各エントリ形式を書いて確認する。
    """

    def test_github_type_is_legacy_unhealthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """旧 GitHub 型エントリ (ak110/dotfiles) は旧形式として is_directory_type_registered が False。"""
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_directory_type_with_other_path(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "directory", "path": "/somewhere/else"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_directory_type_with_relative_path(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "directory", "path": "dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_missing_source(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        known, _settings = marketplace_paths
        write_known_entry(known, {})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_source_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": "directory"})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_missing_path(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "directory"}})
        assert _claude_marketplace.is_directory_type_registered() is False


class TestCheckMarketplaceFromFile:
    """marketplace ファイル検査の 2 ファイル同時検査ケース。

    各ファイル状態が is_directory_type_registered() / ensure_marketplace() の結果に正しく反映されることを確認する。
    """

    def test_known_github_type_is_legacy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """known 側が旧 GitHub 型エントリなら is_directory_type_registered が False。"""
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_settings_github_type_is_legacy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings 側が旧 GitHub 型エントリなら is_directory_type_registered が False。"""
        _known, settings = marketplace_paths
        write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_relative_path_entry_unhealthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        _known, settings = marketplace_paths
        write_settings_entry(settings, {"source": {"source": "directory", "path": "home/aki/dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_both_files_healthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}
        write_known_entry(known, {**entry, "installLocation": str(dotfiles_root)})
        write_settings_entry(settings, entry)
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_only_known_registered_and_healthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        known, _settings = marketplace_paths
        write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": str(dotfiles_root)},
                "installLocation": str(dotfiles_root),
            },
        )
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_only_settings_registered_and_healthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        _known, settings = marketplace_paths
        write_settings_entry(settings, {"source": {"source": "directory", "path": str(dotfiles_root)}})
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_other_path_entry_unhealthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "directory", "path": "/elsewhere"}})
        assert _claude_marketplace.is_directory_type_registered() is False


class TestRepairMarketplace:
    def test_cli_success_updates_both_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        known, settings = marketplace_paths
        healthy_entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if command_matches(cmd, ["claude", "plugin", "marketplace", "add"]):
                write_known_entry(known, {**healthy_entry, "installLocation": str(dotfiles_root)})
                write_settings_entry(settings, healthy_entry)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called when CLI succeeds")

        monkeypatch.setattr(_common.os, "replace", fail_replace)

        assert _claude_marketplace.repair_marketplace() is True

    def test_cli_uses_dotfiles_absolute_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        _known, _settings = marketplace_paths
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        _claude_marketplace.repair_marketplace()
        add_calls = [c for c in calls if command_matches(c, ["claude", "plugin", "marketplace", "add"])]
        assert add_calls, f"marketplace add が呼ばれていない: {calls}"
        assert add_calls[0] == [
            "claude",
            "plugin",
            "marketplace",
            "add",
            str(dotfiles_root),
            "--scope=user",
        ]
        remove_calls = [c for c in calls if command_matches(c, ["claude", "plugin", "marketplace", "remove"])]
        assert remove_calls, f"marketplace remove が呼ばれていない: {calls}"

        assert not any(c.startswith("--scope") for c in remove_calls[0])

    def test_cli_noop_triggers_direct_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        known, settings = marketplace_paths

        # 初期状態: settings 側が旧 GitHub 型で破損
        write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})

        # CLI は全て成功を返すがファイルには触れない (問題の再現環境)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_common, "run_subprocess", fake_run)

        assert _claude_marketplace.repair_marketplace() is True

        known_data = json.loads(known.read_text(encoding="utf-8"))
        entry = known_data[_claude_common.MARKETPLACE_NAME]
        assert entry["source"] == {"source": "directory", "path": str(dotfiles_root)}
        assert entry["installLocation"] == str(dotfiles_root)

        assert isinstance(entry["lastUpdated"], str)
        assert entry["lastUpdated"].endswith("Z")
        settings_data = json.loads(settings.read_text(encoding="utf-8"))
        settings_entry = settings_data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]

        assert settings_entry == {"source": {"source": "directory", "path": str(dotfiles_root)}}

        refresh_calls = [c for c in calls if command_matches(c, ["claude", "plugin", "marketplace", "update"])]
        assert refresh_calls, f"marketplace update が呼ばれていない: {calls}"

    def test_direct_write_preserves_other_marketplace_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """原子的置換で known_marketplaces.json の他キーが保持される。"""
        known, _settings = marketplace_paths

        known.write_text(
            json.dumps(
                {
                    "claude-plugins-official": {
                        "source": {"source": "github", "repo": "anthropics/claude-plugins-official"},
                        "installLocation": "/home/aki/.claude/plugins/marketplaces/claude-plugins-official",
                    },
                    _claude_common.MARKETPLACE_NAME: {
                        "source": {"source": "github", "repo": "ak110/dotfiles"},
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        original_other = json.loads(known.read_text(encoding="utf-8"))["claude-plugins-official"]
        monkeypatch.setattr(
            _common,
            "run_subprocess",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        assert _claude_marketplace.repair_marketplace() is True
        data = json.loads(known.read_text(encoding="utf-8"))
        assert data["claude-plugins-official"] == original_other

        assert data["claude-plugins-official"]["source"]["repo"] == "anthropics/claude-plugins-official"
        assert (
            data["claude-plugins-official"]["installLocation"]
            == "/home/aki/.claude/plugins/marketplaces/claude-plugins-official"
        )

        assert data[_claude_common.MARKETPLACE_NAME]["source"] == {
            "source": "directory",
            "path": str(dotfiles_root),
        }
        assert data[_claude_common.MARKETPLACE_NAME]["installLocation"] == str(dotfiles_root)

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """原子的置換が失敗した場合 False を返す。"""
        known, _settings = marketplace_paths
        # 旧 GitHub 型エントリで破損した状態
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        monkeypatch.setattr(
            _common,
            "run_subprocess",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_common.os, "replace", fail_replace)

        assert _claude_marketplace.repair_marketplace() is False


class TestEnsureMarketplaceHealthy:
    def test_healthy_state_no_repair_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}
        write_known_entry(known, {**entry, "installLocation": str(dotfiles_root)})
        write_settings_entry(settings, entry)

        def fail_run(cmd, **_kwargs):  # noqa: ANN001
            raise AssertionError(f"subprocess.run should not be called: {cmd}")

        monkeypatch.setattr(_common, "run_subprocess", fail_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called in healthy state")

        monkeypatch.setattr(_common.os, "replace", fail_replace)

        assert _claude_marketplace.ensure_marketplace() is True


class TestIsDirectoryTypeRegistered:
    def test_healthy_directory_type(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}
        write_known_entry(known, {**entry, "installLocation": str(dotfiles_root)})
        write_settings_entry(settings, entry)
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_legacy_github_type(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """旧 GitHub 型が残存している環境では False (マイグレーション前)。"""
        known, _settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_unregistered(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        del marketplace_paths
        assert _claude_marketplace.is_directory_type_registered() is False
