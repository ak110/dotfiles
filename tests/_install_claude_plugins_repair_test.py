"""pytools._install_claude_plugins の marketplace 修復ロジックのテスト。

update-dotfiles 実行後に settings.json 側だけに相対パスが残留して
``Marketplace file not found`` エラーになる問題に対応する修復関数群を検証する。
基本的な ``_check_marketplace_from_file`` の動作は _install_claude_plugins_test.py
側に残しており、本ファイルでは 2 ファイル同時検査・修復に関するケースを扱う。
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


class TestExtractEntryAbsPath:
    """_extract_entry_abs_path の単体テスト。"""

    @pytest.mark.parametrize(
        ("entry", "expected"),
        [
            # source.path が絶対パス
            ({"source": {"path": "/abs/path"}}, "/abs/path"),
            # source.path が相対パス → 壊れたエントリ扱い
            ({"source": {"path": "rel/path"}}, None),
            # installLocation が絶対パス
            ({"installLocation": "/abs/install"}, "/abs/install"),
            # installLocation が相対パス
            ({"installLocation": "rel/install"}, None),
            # トップレベル path
            ({"path": "/abs/top"}, "/abs/top"),
            ({"path": "rel/top"}, None),
            # 欠落
            ({}, None),
            # github 型は path を持たないため None
            ({"source": {"source": "github", "repo": "ak110/dotfiles"}}, None),
            # 複数存在時は source.path が最優先
            ({"source": {"path": "/a"}, "installLocation": "/b"}, "/a"),
        ],
    )
    def test_various(self, entry: dict[str, object], expected: str | None):
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._extract_entry_abs_path(entry) == expected


class TestIsEntryHealthy:
    """_is_entry_healthy の単体テスト。"""

    def test_both_fields_match(self):
        entry: dict[str, object] = {"source": {"path": "/x"}, "installLocation": "/x"}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry, "/x") is True

    def test_only_source_present_and_match(self):
        entry: dict[str, object] = {"source": {"path": "/x"}}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry, "/x") is True

    def test_only_install_location_present_and_match(self):
        entry: dict[str, object] = {"installLocation": "/x"}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry, "/x") is True

    def test_one_field_mismatch(self):
        """一方が一致していてももう一方が不一致なら False。"""
        entry: dict[str, object] = {"source": {"path": "/x"}, "installLocation": "/y"}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry, "/x") is False

    def test_relative_path_is_unhealthy(self):
        entry: dict[str, object] = {"source": {"path": "x"}}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry, "/x") is False

    def test_github_type_is_unhealthy(self):
        entry: dict[str, object] = {"source": {"source": "github", "repo": "ak110/dotfiles"}}
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._is_entry_healthy(entry, "/x") is False


class TestCheckMarketplaceFromFile:
    """_check_marketplace_from_file の 2 ファイル同時検査ケース。"""

    def test_known_relative_path_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """known 側 source.path が相対パスなら False（本修復の主対象ケース）。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, {"source": {"source": "directory", "path": "home/shimoyama/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/home/shimoyama/dotfiles")) is False

    def test_settings_relative_path_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """settings 側 source.path が相対パスなら False（CLI が settings を更新しないケース）。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "home/shimoyama/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/home/shimoyama/dotfiles")) is False

    def test_github_type_entry_unhealthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """github 型エントリは壊れた登録として False。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/any")) is False

    def test_both_files_healthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """両ファイルとも絶対パス一致なら True。"""
        known, settings = marketplace_paths
        _write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": "/home/aki/dotfiles"},
                "installLocation": "/home/aki/dotfiles",
            },
        )
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "/home/aki/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/home/aki/dotfiles")) is True

    def test_only_known_registered_and_healthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """片方のみ登録で残りが健全なら True（settings 未初期化の正常環境）。"""
        known, _settings = marketplace_paths
        _write_known_entry(known, {"source": {"source": "directory", "path": "/home/aki/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/home/aki/dotfiles")) is True

    def test_only_settings_registered_and_healthy(self, marketplace_paths: tuple[pathlib.Path, pathlib.Path]):
        """known 未登録でも settings 側が健全なら True。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "/home/aki/dotfiles"}})
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/home/aki/dotfiles")) is True

    def test_install_location_broken_with_sound_source(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """source.path は健全でも installLocation が相対パスなら False。"""
        known, _settings = marketplace_paths
        _write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": "/home/aki/dotfiles"},
                "installLocation": "home/aki/dotfiles",
            },
        )
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._check_marketplace_from_file(pathlib.Path("/home/aki/dotfiles")) is False


class TestRepairMarketplace:
    """_repair_marketplace のテスト。CLI remove+add → 再検証 → 直接書き換えの多段修復。"""

    def test_cli_success_updates_both_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """CLI add が両ファイルを正常化すれば、直接書き換えには進まず True を返す。"""
        known, settings = marketplace_paths
        new_root = pathlib.Path("/new/dotfiles")

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                _write_known_entry(
                    known,
                    {
                        "source": {"source": "directory", "path": str(new_root)},
                        "installLocation": str(new_root),
                    },
                )
                _write_settings_entry(settings, {"source": {"source": "directory", "path": str(new_root)}})
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fake_run)

        # os.replace が呼ばれたら回帰。CLI 経路のみで完結するはず
        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called when CLI succeeds")

        monkeypatch.setattr(_install_claude_plugins.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace(new_root) is True

    def test_cli_noop_triggers_direct_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """CLI が settings を更新しないケースでも直接書き換えで修復する。"""
        known, settings = marketplace_paths
        new_root = pathlib.Path("/home/shimoyama/dotfiles")

        # 初期状態: settings 側が相対パスで破損
        _write_settings_entry(settings, {"source": {"source": "directory", "path": "home/shimoyama/dotfiles"}})

        # CLI は成功を返すがファイルには触れない (問題の再現環境)
        monkeypatch.setattr(
            _install_claude_plugins.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace(new_root) is True
        # 両ファイルとも絶対パスへ更新されている
        known_data = json.loads(known.read_text(encoding="utf-8"))
        # pylint: disable-next=protected-access
        assert known_data[_install_claude_plugins._MARKETPLACE_NAME]["source"]["path"] == str(new_root)
        settings_data = json.loads(settings.read_text(encoding="utf-8"))
        assert (
            # pylint: disable-next=protected-access
            settings_data["extraKnownMarketplaces"][_install_claude_plugins._MARKETPLACE_NAME]["source"]["path"]
            == str(new_root)
        )

    def test_direct_write_preserves_other_marketplace_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """原子的置換で known_marketplaces.json の他キーが保持される。"""
        known, _settings = marketplace_paths
        # 壊れたエントリ + 無関係な他キー
        known.write_text(
            json.dumps(
                {
                    "claude-plugins-official": {"source": {"source": "directory", "path": "/some/path"}},
                    # pylint: disable-next=protected-access
                    _install_claude_plugins._MARKETPLACE_NAME: {
                        "source": {"source": "directory", "path": "broken/path"},
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

        new_root = pathlib.Path("/new/dotfiles")
        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace(new_root) is True
        data = json.loads(known.read_text(encoding="utf-8"))
        # 他キーは保持
        assert data["claude-plugins-official"]["source"]["path"] == "/some/path"
        # 対象キーは新しい絶対パスへ差し替わる
        # pylint: disable-next=protected-access
        assert data[_install_claude_plugins._MARKETPLACE_NAME]["source"]["path"] == str(new_root)
        # pylint: disable-next=protected-access
        assert data[_install_claude_plugins._MARKETPLACE_NAME]["installLocation"] == str(new_root)

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """原子的置換が失敗した場合 False を返す。"""
        known, _settings = marketplace_paths
        # 相対パスで破損した状態
        _write_known_entry(known, {"source": {"source": "directory", "path": "rel"}})
        monkeypatch.setattr(
            _install_claude_plugins.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=0),
        )

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_install_claude_plugins.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._repair_marketplace(pathlib.Path("/new/dotfiles")) is False


class TestEnsureMarketplaceHealthy:
    """_ensure_marketplace の健全状態時の挙動を検証する（回帰防止）。"""

    def test_healthy_state_no_repair_calls(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """両ファイル正常なら subprocess も os.replace も呼ばれない。"""
        known, settings = marketplace_paths
        root = pathlib.Path("/home/aki/dotfiles")
        _write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": str(root)},
                "installLocation": str(root),
            },
        )
        _write_settings_entry(settings, {"source": {"source": "directory", "path": str(root)}})

        def fail_run(cmd, **_kwargs):  # noqa: ANN001
            raise AssertionError(f"subprocess.run should not be called: {cmd}")

        monkeypatch.setattr(_install_claude_plugins.subprocess, "run", fail_run)

        def fail_replace(*_args, **_kwargs):
            raise AssertionError("os.replace should not be called in healthy state")

        monkeypatch.setattr(_install_claude_plugins.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        assert _install_claude_plugins._ensure_marketplace(root) is True
