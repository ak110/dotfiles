"""pytools._install_claude_plugins の marketplace 修復ロジックのテスト。

update-dotfiles 実行後に過去の directory 型エントリが残留して
``Marketplace file not found`` エラーになる問題に対応する GitHub 型への
マイグレーションを検証する。基本的な ``_check_marketplace_from_file`` の動作は
_install_claude_plugins_test.py 側に残しており、本ファイルでは 2 ファイル同時検査・
修復に関するケースを扱う。
"""

import json
import pathlib

import pytest

from pytools import _install_claude_plugins


class _FakeResult:
    """subprocess.CompletedProcess の軽量な代替。"""

    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _write_known_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """known_marketplaces.json に対象 marketplace のエントリを書き出す。"""
    path.write_text(
        # pylint: disable-next=protected-access
        json.dumps({_install_claude_plugins._MARKETPLACE_NAME: entry}),
        encoding="utf-8",
    )


def _write_settings_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """settings.json.extraKnownMarketplaces に対象 marketplace のエントリを書き出す。"""
    path.write_text(
        # pylint: disable-next=protected-access
        json.dumps({"extraKnownMarketplaces": {_install_claude_plugins._MARKETPLACE_NAME: entry}}),
        encoding="utf-8",
    )


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
    monkeypatch.setattr(_install_claude_plugins, "_KNOWN_MARKETPLACES_PATH", known)
    monkeypatch.setattr(_install_claude_plugins, "_SETTINGS_JSON_PATH", settings)
    return known, settings


class TestIsEntryHealthy:
    """_is_entry_healthy の単体テスト。GitHub 型 + 対象 repo の時だけ健全と判定する。"""

    def test_github_type_with_target_repo(self):
        entry: dict[str, object] = {"source": {"source": "github", "repo": "ak110/dotfiles"}}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry) is True

    def test_directory_type_is_unhealthy(self):
        """directory 型エントリ (過去の登録) は壊れたエントリとして False。"""
        entry: dict[str, object] = {
            "source": {"source": "directory", "path": "/home/aki/dotfiles"},
            "installLocation": "/home/aki/dotfiles",
        }
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry) is False

    def test_github_type_with_other_repo(self):
        """別 repo の GitHub 型は False。"""
        entry: dict[str, object] = {"source": {"source": "github", "repo": "anthropics/claude-plugins-official"}}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry) is False

    def test_missing_source(self):
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy({}) is False

    def test_source_not_dict(self):
        """source が文字列など非 dict の場合は False。"""
        entry: dict[str, object] = {"source": "directory"}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry) is False

    def test_missing_repo(self):
        entry: dict[str, object] = {"source": {"source": "github"}}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry) is False


_GITHUB_ENTRY: dict[str, object] = {"source": {"source": "github", "repo": "ak110/dotfiles"}}


class TestCheckMarketplaceFromFile:
    """_check_marketplace_from_file の 2 ファイル同時検査ケース。"""

    def test_known_directory_type_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """known 側が過去の directory 型エントリなら False（マイグレーション対象）。"""
        known, _settings = marketplace_paths
        _write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": "/home/aki/dotfiles"},
                "installLocation": "/home/aki/dotfiles",
            },
        )
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is False

    def test_settings_directory_type_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """settings 側が directory 型エントリなら False（CLI が settings を更新しないケース）。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "/home/aki/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is False

    def test_relative_path_entry_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """相対パスを含む古い directory 型も False。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "home/aki/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is False

    def test_both_files_healthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """両ファイルとも GitHub 型 + 対象 repo なら True。"""
        known, settings = marketplace_paths
        _write_known_entry(known, _GITHUB_ENTRY)
        _write_settings_entry(settings, _GITHUB_ENTRY)
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is True

    def test_only_known_registered_and_healthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """片方のみ登録で残りが健全なら True（settings 未初期化の正常環境）。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, _GITHUB_ENTRY)
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is True

    def test_only_settings_registered_and_healthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """known 未登録でも settings 側が健全なら True。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, _GITHUB_ENTRY)
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is True

    def test_other_repo_entry_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """GitHub 型でも別 repo を指していれば False。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, {"source": {"source": "github", "repo": "someone/else"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file() is False


class TestRepairMarketplace:
    """_repair_marketplace のテスト。CLI remove+add → 再検証 → 直接書き換え + refresh の多段修復。"""

    def test_cli_success_updates_both_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """CLI add が両ファイルを GitHub 型に正常化すれば、直接書き換えには進まず True を返す。"""
        known, settings = marketplace_paths

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                _write_known_entry(known, _GITHUB_ENTRY)
                _write_settings_entry(settings, _GITHUB_ENTRY)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called when CLI succeeds")

        monkeypatch.setattr(_install_claude_plugins.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace() is True

    def test_cli_uses_github_shorthand(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """CLI add には ak110/dotfiles の GitHub ショートハンドが渡される。"""
        _known, _settings = marketplace_paths
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        # pylint: disable-next=protected-access
        _install_claude_plugins._repair_marketplace()
        add_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]]
        # pylint: disable-next=protected-access
        assert add_calls and add_calls[0][-1] == _install_claude_plugins._MARKETPLACE_REPO

    def test_cli_noop_triggers_direct_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """CLI が settings を更新しないケースでも直接書き換えで修復し、直後に refresh を呼ぶ。"""
        known, settings = marketplace_paths

        # 初期状態: settings 側が過去の directory 型で破損
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "/home/aki/dotfiles"}})

        # CLI は全て成功を返すがファイルには触れない (問題の再現環境)
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace() is True
        # 両ファイルとも GitHub 型へ更新されている
        known_data = json.loads(known.read_text(encoding="utf-8"))
        # pylint: disable-next=protected-access
        entry = known_data[_install_claude_plugins._MARKETPLACE_NAME]
        assert entry["source"] == {"source": "github", "repo": "ak110/dotfiles"}
        # pylint: disable-next=protected-access
        assert entry["installLocation"] == str(_install_claude_plugins._MARKETPLACE_INSTALL_LOCATION)
        # lastUpdated は ISO 8601 の Z 形式
        assert isinstance(entry["lastUpdated"], str)
        assert entry["lastUpdated"].endswith("Z")
        settings_data = json.loads(settings.read_text(encoding="utf-8"))
        # pylint: disable-next=protected-access
        settings_entry = settings_data["extraKnownMarketplaces"][_install_claude_plugins._MARKETPLACE_NAME]
        assert settings_entry == {"source": {"source": "github", "repo": "ak110/dotfiles"}}
        # installLocation の実体欠落対策として marketplace update が呼ばれる
        refresh_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "update"]]
        assert refresh_calls, f"marketplace update が呼ばれていない: {calls}"

    def test_direct_write_preserves_other_marketplace_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
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
                    # pylint: disable-next=protected-access
                    _install_claude_plugins._MARKETPLACE_NAME: {
                        "source": {"source": "directory", "path": "/home/aki/dotfiles"},
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(
            _install_claude_plugins.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace() is True
        data = json.loads(known.read_text(encoding="utf-8"))
        # 他キーは保持
        assert data["claude-plugins-official"]["source"]["repo"] == "anthropics/claude-plugins-official"
        assert (
            data["claude-plugins-official"]["installLocation"]
            == "/home/aki/.claude/plugins/marketplaces/claude-plugins-official"
        )
        # 対象キーは GitHub 型エントリへ差し替わる
        # pylint: disable-next=protected-access
        assert data[_install_claude_plugins._MARKETPLACE_NAME]["source"] == {
            "source": "github",
            "repo": "ak110/dotfiles",
        }
        # pylint: disable-next=protected-access
        assert data[_install_claude_plugins._MARKETPLACE_NAME]["installLocation"] == str(
            # pylint: disable-next=protected-access
            _install_claude_plugins._MARKETPLACE_INSTALL_LOCATION,
        )

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """原子的置換が失敗した場合 False を返す。"""
        known, _settings = marketplace_paths
        # 過去の directory 型エントリで破損した状態
        _write_known_entry(known, {"source": {"source": "directory", "path": "/home/aki/dotfiles"}})
        monkeypatch.setattr(
            _install_claude_plugins.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_install_claude_plugins.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace() is False


class TestEnsureMarketplaceHealthy:
    """_ensure_marketplace の健全状態時の挙動を検証する（回帰防止）。"""

    def test_healthy_state_no_repair_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """両ファイル GitHub 型で正常なら subprocess も os.replace も呼ばれない。"""
        known, settings = marketplace_paths
        _write_known_entry(known, _GITHUB_ENTRY)
        _write_settings_entry(settings, _GITHUB_ENTRY)

        def fail_run(cmd, **_kwargs):  # noqa: ANN001
            raise AssertionError(f"subprocess.run should not be called: {cmd}")

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fail_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called in healthy state")

        monkeypatch.setattr(_install_claude_plugins.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._ensure_marketplace() is True
