"""pytools._internal.claude_marketplace の marketplace 修復ロジックのテスト。

install-claude.sh bootstrap が残した旧 GitHub 型登録を directory 型へ
自動マイグレーションする挙動を検証する。基本的な ``_check_marketplace_from_file``
の動作は install_claude_plugins_test.py 側に残しており、本ファイルでは 2 ファイル同時検査・
修復に関するケースを扱う。
"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace

from .helpers import _FakeResult


def _write_known_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """known_marketplaces.json に対象 marketplace のエントリを書き出す。"""
    path.write_text(
        json.dumps({_claude_common.MARKETPLACE_NAME: entry}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_settings_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """settings.json.extraKnownMarketplaces に対象 marketplace のエントリを書き出す。"""
    path.write_text(
        json.dumps({"extraKnownMarketplaces": {_claude_common.MARKETPLACE_NAME: entry}}, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.fixture(name="dotfiles_root")
def _dotfiles_root() -> pathlib.Path:
    """本リポジトリの dotfiles ルート (directory 型 path のテスト期待値)。"""
    # pylint: disable-next=protected-access
    root = _claude_marketplace._find_dotfiles_root()
    assert root is not None, "dotfiles ルートが検出できない環境ではテストを実行できない"
    return root


@pytest.fixture(name="marketplace_paths")
def _marketplace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    """known_marketplaces.json と settings.json のパスを tmp_path へ差し替える。

    2 ファイルを同時に点検・修復するため、両方をテスト専用パスへ差し替えて
    実環境の ``~/.claude/settings.json`` に依存しない状態を作る。
    """
    known = tmp_path / "known_marketplaces.json"
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", known)
    monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", settings)
    return known, settings


class TestIsEntryHealthy:
    """_is_entry_healthy の単体テスト。directory 型 + dotfiles 絶対パスの時だけ健全。"""

    def test_directory_type_with_dotfiles_path(self, dotfiles_root: pathlib.Path):
        """directory 型 + dotfiles 絶対パスなら True。"""
        entry: dict[str, object] = {
            "source": {"source": "directory", "path": str(dotfiles_root)},
            "installLocation": str(dotfiles_root),
        }
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy(entry) is True

    def test_github_type_is_legacy_unhealthy(self):
        """旧 GitHub 型エントリ (ak110/dotfiles) は旧形式として False。"""
        entry: dict[str, object] = {"source": {"source": "github", "repo": "ak110/dotfiles"}}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy(entry) is False

    def test_directory_type_with_other_path(self):
        """別 path の directory 型は False。"""
        entry: dict[str, object] = {"source": {"source": "directory", "path": "/somewhere/else"}}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy(entry) is False

    def test_directory_type_with_relative_path(self):
        """相対パスの directory 型は False (dotfiles 絶対パスと不一致)。"""
        entry: dict[str, object] = {"source": {"source": "directory", "path": "dotfiles"}}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy(entry) is False

    def test_missing_source(self):
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy({}) is False

    def test_source_not_dict(self):
        """source が文字列など非 dict の場合は False。"""
        entry: dict[str, object] = {"source": "directory"}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy(entry) is False

    def test_missing_path(self):
        entry: dict[str, object] = {"source": {"source": "directory"}}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._is_entry_healthy(entry) is False


class TestCheckMarketplaceFromFile:
    """_check_marketplace_from_file の 2 ファイル同時検査ケース。"""

    def test_known_github_type_is_legacy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """known 側が旧 GitHub 型エントリならマイグレーション対象として False。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is False

    def test_settings_github_type_is_legacy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings 側が旧 GitHub 型エントリなら False。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is False

    def test_relative_path_entry_unhealthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """相対パスを含む directory 型は False。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "home/aki/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is False

    def test_both_files_healthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """両ファイルとも directory 型 + dotfiles 絶対パスなら True。"""
        known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}
        _write_known_entry(known, {**entry, "installLocation": str(dotfiles_root)})
        _write_settings_entry(settings, entry)
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is True

    def test_only_known_registered_and_healthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """片方のみ登録で残りが健全なら True (settings 未初期化の正常環境)。"""
        known, _settings = marketplace_paths
        _write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": str(dotfiles_root)},
                "installLocation": str(dotfiles_root),
            },
        )
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is True

    def test_only_settings_registered_and_healthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """known 未登録でも settings 側が健全なら True。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "directory", "path": str(dotfiles_root)}})
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is True

    def test_other_path_entry_unhealthy(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """directory 型でも別 path を指していれば False。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, {"source": {"source": "directory", "path": "/elsewhere"}})
        # pylint: disable-next=protected-access
        assert _claude_marketplace._check_marketplace_from_file() is False


class TestRepairMarketplace:
    """_repair_marketplace のテスト。CLI remove+add → 再検証 → 直接書き換え + refresh の多段修復。"""

    def test_cli_success_updates_both_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """CLI add が両ファイルを directory 型に正常化すれば、直接書き換えには進まず True を返す。"""
        known, settings = marketplace_paths
        healthy_entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                _write_known_entry(known, {**healthy_entry, "installLocation": str(dotfiles_root)})
                _write_settings_entry(settings, healthy_entry)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called when CLI succeeds")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        assert _claude_marketplace.repair_marketplace() is True

    def test_cli_uses_dotfiles_absolute_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """CLI add には dotfiles の絶対パスと ``--scope user`` が渡される。``remove`` には ``--scope`` は付かない。"""
        _known, _settings = marketplace_paths
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        _claude_marketplace.repair_marketplace()
        add_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]]
        assert add_calls, f"marketplace add が呼ばれていない: {calls}"
        assert add_calls[0] == [
            "claude",
            "plugin",
            "marketplace",
            "add",
            str(dotfiles_root),
            "--scope",
            "user",
        ]
        remove_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "remove"]]
        assert remove_calls, f"marketplace remove が呼ばれていない: {calls}"
        # remove には --scope オプションは存在しない
        assert "--scope" not in remove_calls[0]

    def test_cli_noop_triggers_direct_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """CLI が settings を更新しないケースでも直接書き換えで修復し、直後に refresh を呼ぶ。"""
        known, settings = marketplace_paths

        # 初期状態: settings 側が旧 GitHub 型で破損
        _write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})

        # CLI は全て成功を返すがファイルには触れない (問題の再現環境)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _claude_marketplace.repair_marketplace() is True
        # 両ファイルとも directory 型 + dotfiles 絶対パスへ更新されている
        known_data = json.loads(known.read_text(encoding="utf-8"))
        entry = known_data[_claude_common.MARKETPLACE_NAME]
        assert entry["source"] == {"source": "directory", "path": str(dotfiles_root)}
        assert entry["installLocation"] == str(dotfiles_root)
        # lastUpdated は ISO 8601 の Z 形式
        assert isinstance(entry["lastUpdated"], str)
        assert entry["lastUpdated"].endswith("Z")
        settings_data = json.loads(settings.read_text(encoding="utf-8"))
        settings_entry = settings_data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]
        # settings 側は installLocation を持たない
        assert settings_entry == {"source": {"source": "directory", "path": str(dotfiles_root)}}
        # メタデータ整合確認のため marketplace update が呼ばれる
        refresh_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "update"]]
        assert refresh_calls, f"marketplace update が呼ばれていない: {calls}"

    def test_direct_write_preserves_other_marketplace_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """原子的置換で known_marketplaces.json の他キーが保持される。"""
        known, _settings = marketplace_paths
        # 壊れたエントリ + 無関係な他キー (他キーは触らない)
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
        monkeypatch.setattr(
            _claude_common.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        assert _claude_marketplace.repair_marketplace() is True
        data = json.loads(known.read_text(encoding="utf-8"))
        # 他キーは保持
        assert data["claude-plugins-official"]["source"]["repo"] == "anthropics/claude-plugins-official"
        assert (
            data["claude-plugins-official"]["installLocation"]
            == "/home/aki/.claude/plugins/marketplaces/claude-plugins-official"
        )
        # 対象キーは directory 型エントリへ差し替わる
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
        _write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        monkeypatch.setattr(
            _claude_common.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        assert _claude_marketplace.repair_marketplace() is False


class TestEnsureMarketplaceHealthy:
    """_ensure_marketplace の健全状態時の挙動を検証する (回帰防止)。"""

    def test_healthy_state_no_repair_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """両ファイル directory 型で正常なら subprocess も os.replace も呼ばれない。"""
        known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}
        _write_known_entry(known, {**entry, "installLocation": str(dotfiles_root)})
        _write_settings_entry(settings, entry)

        def fail_run(cmd, **_kwargs):  # noqa: ANN001
            raise AssertionError(f"subprocess.run should not be called: {cmd}")

        monkeypatch.setattr(_claude_common.subprocess, "run", fail_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called in healthy state")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        assert _claude_marketplace.ensure_marketplace() is True


class TestIsDirectoryTypeRegistered:
    """``is_directory_type_registered`` 公開 API のテスト。"""

    def test_healthy_directory_type(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """両ファイル directory 型で健全なら True。"""
        known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": str(dotfiles_root)}}
        _write_known_entry(known, {**entry, "installLocation": str(dotfiles_root)})
        _write_settings_entry(settings, entry)
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_legacy_github_type(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """旧 GitHub 型が残存している環境では False (マイグレーション前)。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_unregistered(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """未登録なら False。"""
        del marketplace_paths  # 2 ファイルが存在しない状態のまま
        assert _claude_marketplace.is_directory_type_registered() is False
