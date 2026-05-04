"""pytools._internal.claude_marketplace の単体テスト。

本ファイルでは以下の観点を網羅する。

- 健全性検証: _check_marketplace_from_file / _is_entry_healthy の各分岐
- 修復ロジック: repair_marketplace の CLI 成功・直接書き換えフォールバック・失敗経路
- 設定ファイル直書きの境界条件:
    _rewrite_known_marketplaces_entry / _rewrite_settings_extra_known_entry の
    ファイル不在・書き込み失敗・既存キー保持などのケース
- _marketplace_already_registered のパース分岐
- ensure_marketplace の各分岐 (file_check True/False/None)
- refresh_marketplace の成功・失敗

install_claude_plugins_repair_test.py と install_claude_plugins_test.py の
TestCheckMarketplaceFromFile・TestLegacyGithubTypeMigration・TestRepairMarketplace などとの
重複を避け、本ファイルではそれらで扱っていない分岐・パラメーター化ケースを追加する。
"""

import datetime
import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace

from ._test_helpers import _FakeResult

# --- fixtures ---


@pytest.fixture(name="dotfiles_root")
def _dotfiles_root() -> pathlib.Path:
    """本リポジトリの dotfiles ルート (directory 型 path のテスト期待値)。"""
    root = _claude_common.find_dotfiles_root()
    assert root is not None, "dotfiles ルートが検出できない環境ではテストを実行できない"
    return root


@pytest.fixture(name="marketplace_paths")
def _marketplace_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: pathlib.Path,
) -> tuple[pathlib.Path, pathlib.Path]:
    """known_marketplaces.json と settings.json のパスを tmp_path へ差し替える。"""
    known = tmp_path / "known_marketplaces.json"
    settings = tmp_path / "settings.json"
    monkeypatch.setattr(_claude_marketplace, "_KNOWN_MARKETPLACES_PATH", known)
    monkeypatch.setattr(_claude_marketplace, "_SETTINGS_JSON_PATH", settings)
    return known, settings


# --- helpers ---


def _write_known_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """known_marketplaces.json に対象 marketplace のエントリを保存する。"""
    path.write_text(
        json.dumps({_claude_common.MARKETPLACE_NAME: entry}, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_settings_entry(path: pathlib.Path, entry: dict[str, object]) -> None:
    """settings.json.extraKnownMarketplaces に対象 marketplace のエントリを保存する。"""
    path.write_text(
        json.dumps({"extraKnownMarketplaces": {_claude_common.MARKETPLACE_NAME: entry}}, ensure_ascii=False),
        encoding="utf-8",
    )


# --- TestMarketplaceAlreadyRegistered ---


class TestMarketplaceAlreadyRegistered:
    """_marketplace_already_registered の各パース形式テスト。

    `marketplace list --json` の出力形式はバージョンによって異なるため、
    複数の形式を許容していることを検証する。
    """

    def test_list_with_name_key(self):
        """リスト形式: name キーが一致すれば True。"""
        data = [{"name": _claude_common.MARKETPLACE_NAME}]
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(data) is True

    def test_list_without_target(self):
        """リスト形式: 対象 name が含まれなければ False。"""
        data = [{"name": "other-marketplace"}]
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(data) is False

    def test_empty_list(self):
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered([]) is False

    def test_dict_with_marketplaces_key(self):
        """{marketplaces: [...]} の入れ子形式を再帰的にパースできる。"""
        data = {"marketplaces": [{"name": _claude_common.MARKETPLACE_NAME}]}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(data) is True

    def test_flat_dict_contains_name(self):
        """フラット dict 形式: トップレベルのキーが name と一致すれば True。"""
        data: dict[str, object] = {_claude_common.MARKETPLACE_NAME: {}}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(data) is True

    def test_flat_dict_no_target(self):
        """フラット dict 形式: 対象キーが無ければ False。"""
        data: dict[str, object] = {"other": {}}
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(data) is False

    def test_unexpected_type_returns_false(self):
        """int など未知の型は False。"""
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(42) is False
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered(None) is False
        # pylint: disable-next=protected-access
        assert _claude_marketplace._marketplace_already_registered("name") is False


# --- TestNowIsoMillis ---


class TestNowIsoMillis:
    """_now_iso_millis の出力形式テスト。"""

    def test_returns_utc_z_format(self):
        """UTC + Z 末尾の ISO 8601 ミリ秒形式を返す。"""
        # pylint: disable-next=protected-access
        result = _claude_marketplace._now_iso_millis()
        assert result.endswith("Z"), f"Z 末尾でない: {result}"
        # ミリ秒精度のため小数点以下3桁が含まれる
        assert "." in result

    def test_format_is_parseable(self):
        """返された文字列が datetime.fromisoformat で復元できる。"""
        # pylint: disable-next=protected-access
        result = _claude_marketplace._now_iso_millis()
        # Z を +00:00 に変換してから解析
        dt = datetime.datetime.fromisoformat(result.replace("Z", "+00:00"))
        assert dt.tzinfo is not None


# --- TestRewriteKnownMarketplacesEntry ---


class TestRewriteKnownMarketplacesEntry:
    """_rewrite_known_marketplaces_entry の境界条件テスト。"""

    def test_creates_new_file_when_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """ファイルが存在しない場合は新規作成し directory 型エントリを書き込む。"""
        known, _settings = marketplace_paths
        assert not known.exists()

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_known_marketplaces_entry(dotfiles_root)

        assert result is True
        data = json.loads(known.read_text(encoding="utf-8"))
        entry = data[_claude_common.MARKETPLACE_NAME]
        assert entry["source"] == {"source": "directory", "path": str(dotfiles_root)}
        assert entry["installLocation"] == str(dotfiles_root)
        assert isinstance(entry["lastUpdated"], str) and entry["lastUpdated"].endswith("Z")

    def test_preserves_other_keys(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """他の marketplace キーを保持したまま対象キーだけ上書きする。"""
        known, _settings = marketplace_paths
        known.write_text(
            json.dumps(
                {
                    "claude-plugins-official": {"source": {"source": "github", "repo": "anthropics/claude-plugins-official"}},
                    _claude_common.MARKETPLACE_NAME: {"source": {"source": "github", "repo": "ak110/dotfiles"}},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_known_marketplaces_entry(dotfiles_root)

        assert result is True
        data = json.loads(known.read_text(encoding="utf-8"))
        # 他キーが保持されている
        assert "claude-plugins-official" in data
        # 対象キーが directory 型に書き換わっている
        assert data[_claude_common.MARKETPLACE_NAME]["source"] == {
            "source": "directory",
            "path": str(dotfiles_root),
        }

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """atomic write が失敗すると False を返す。"""
        _known, _settings = marketplace_paths

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_known_marketplaces_entry(dotfiles_root)

        assert result is False

    def test_invalid_json_returns_false(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """既存ファイルが不正 JSON の場合は False を返す (書き込まない)。"""
        known, _settings = marketplace_paths
        known.write_text("{invalid json", encoding="utf-8")

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_known_marketplaces_entry(dotfiles_root)

        assert result is False


# --- TestRewriteSettingsExtraKnownEntry ---


class TestRewriteSettingsExtraKnownEntry:
    """_rewrite_settings_extra_known_entry の境界条件テスト。"""

    def test_settings_not_exists_returns_true_without_write(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """settings.json が存在しない場合は何も書かず True を返す (勝手に新規作成しない)。"""
        _known, settings = marketplace_paths
        assert not settings.exists()

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_settings_extra_known_entry(dotfiles_root)

        assert result is True
        assert not settings.exists()

    def test_overwrites_existing_entry(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """既存の旧形式エントリを directory 型に上書きする。"""
        _known, settings = marketplace_paths
        _write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_settings_extra_known_entry(dotfiles_root)

        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        entry = data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]
        assert entry == {"source": {"source": "directory", "path": str(dotfiles_root)}}
        # settings 側は installLocation を持たない
        assert "installLocation" not in entry

    def test_creates_extra_known_key_when_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """settings.json が存在するが extraKnownMarketplaces がない場合は追加する。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({"otherSetting": True}, ensure_ascii=False), encoding="utf-8")

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_settings_extra_known_entry(dotfiles_root)

        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "otherSetting" in data
        assert data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]["source"]["source"] == "directory"

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """atomic write が失敗すると False を返す。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({}, ensure_ascii=False), encoding="utf-8")

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_settings_extra_known_entry(dotfiles_root)

        assert result is False

    def test_invalid_json_returns_false(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """settings.json が不正 JSON の場合は False を返す (書き込まない)。"""
        _known, settings = marketplace_paths
        settings.write_text("{invalid", encoding="utf-8")

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_settings_extra_known_entry(dotfiles_root)

        assert result is False

    def test_extra_known_marketplaces_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """extraKnownMarketplaces が dict でない場合は初期化して書き込む。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({"extraKnownMarketplaces": []}, ensure_ascii=False), encoding="utf-8")

        # pylint: disable-next=protected-access
        result = _claude_marketplace._rewrite_settings_extra_known_entry(dotfiles_root)

        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]["source"]["source"] == "directory"


# --- TestRefreshMarketplace ---


class TestRefreshMarketplace:
    """refresh_marketplace の成功・失敗経路テスト。"""

    def test_success(self, monkeypatch: pytest.MonkeyPatch):
        """CLI が成功すれば True を返す。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            return _FakeResult(returncode=0)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _claude_marketplace.refresh_marketplace() is True
        update_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "update"]]
        assert update_calls, "marketplace update が呼ばれていない"
        assert _claude_common.MARKETPLACE_NAME in update_calls[0]

    def test_cli_failure_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """CLI が失敗しても例外を発生させず False を返す (best-effort)。"""
        monkeypatch.setattr(
            _claude_common.subprocess,
            "run",
            lambda *_a, **_k: _FakeResult(returncode=1, stderr="update failed"),
        )

        assert _claude_marketplace.refresh_marketplace() is False

    def test_cli_unavailable_returns_false(self, monkeypatch: pytest.MonkeyPatch):
        """CLI 実行そのものが失敗 (None 返) しても False を返す。"""
        monkeypatch.setattr(_claude_common, "run_claude", lambda *_a, **_k: None)
        assert _claude_marketplace.refresh_marketplace() is False


# --- TestEnsureMarketplace ---


class TestEnsureMarketplace:
    """ensure_marketplace の各 file_check 分岐テスト。

    file_check == True (健全状態) のケースは
    install_claude_plugins_repair_test.py::TestEnsureMarketplaceHealthy でカバー済み。
    本クラスでは file_check == False と None の分岐を補完する。
    """

    def test_file_check_false_triggers_repair(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """file_check == False なら repair_marketplace を呼び出す。"""
        known, _settings = marketplace_paths
        # 旧 GitHub 型 → file_check が False になる
        _write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})

        repair_called = []

        def fake_repair():
            repair_called.append(True)
            return True

        monkeypatch.setattr(_claude_marketplace, "repair_marketplace", fake_repair)

        result = _claude_marketplace.ensure_marketplace()
        assert result is True
        assert repair_called, "repair_marketplace が呼ばれていない"

    def test_file_check_none_with_marketplace_list_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """file_check == None + marketplace list 失敗 → add を試みる。"""
        _known, _settings = marketplace_paths
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=1, stderr="error")
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        result = _claude_marketplace.ensure_marketplace()
        assert result is True
        add_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]]
        assert add_calls, "marketplace add が呼ばれていない"
        assert str(dotfiles_root) in add_calls[0]

    def test_file_check_none_marketplace_add_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """file_check == None で marketplace add も失敗すれば False を返す。"""
        _known, _settings = marketplace_paths

        def fake_run(_cmd: list[str], **_kwargs: object) -> _FakeResult:
            return _FakeResult(returncode=1, stderr="fail")

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        assert _claude_marketplace.ensure_marketplace() is False

    def test_dotfiles_root_not_found_skips(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """dotfiles ルートが検出できない場合は add を呼ばず False を返す。"""
        _known, _settings = marketplace_paths
        monkeypatch.setattr(_claude_common, "find_dotfiles_root", lambda: None)

        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            # marketplace list は成功 (空リスト) を返す
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout="[]")
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)

        result = _claude_marketplace.ensure_marketplace()
        assert result is False
        add_calls = [c for c in calls if c[:4] == ["claude", "plugin", "marketplace", "add"]]
        assert not add_calls, "dotfiles ルート不在なのに add が呼ばれた"


# --- TestLoadKnownMarketplaceEntry ---


class TestLoadKnownMarketplaceEntry:
    """_load_known_marketplace_entry の読み込み分岐テスト。"""

    def test_returns_entry_when_exists(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """known_marketplaces.json に対象エントリがあれば返す。"""
        known, _settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": "/somewhere"}}
        _write_known_entry(known, entry)

        # pylint: disable-next=protected-access
        result = _claude_marketplace._load_known_marketplace_entry()
        assert result == entry

    def test_returns_none_when_file_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """ファイルが存在しない場合は None を返す。"""
        del marketplace_paths
        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_known_marketplace_entry() is None

    def test_returns_none_when_key_missing(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """ファイルに対象キーが無い場合は None を返す。"""
        known, _settings = marketplace_paths
        known.write_text(json.dumps({"other": {}}, ensure_ascii=False), encoding="utf-8")

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_known_marketplace_entry() is None

    def test_returns_none_when_entry_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """エントリが dict でない場合は None を返す。"""
        known, _settings = marketplace_paths
        known.write_text(
            json.dumps({_claude_common.MARKETPLACE_NAME: "not-a-dict"}, ensure_ascii=False),
            encoding="utf-8",
        )

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_known_marketplace_entry() is None

    def test_returns_none_when_invalid_json(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """JSON 解析失敗の場合は None を返す。"""
        known, _settings = marketplace_paths
        known.write_text("{broken", encoding="utf-8")

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_known_marketplace_entry() is None


# --- TestLoadExtraKnownMarketplaceEntry ---


class TestLoadExtraKnownMarketplaceEntry:
    """_load_extra_known_marketplace_entry の読み込み分岐テスト。"""

    def test_returns_entry_when_exists(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json に extraKnownMarketplaces[target] があれば返す。"""
        _known, settings = marketplace_paths
        entry: dict[str, object] = {"source": {"source": "directory", "path": "/somewhere"}}
        _write_settings_entry(settings, entry)

        # pylint: disable-next=protected-access
        result = _claude_marketplace._load_extra_known_marketplace_entry()
        assert result == entry

    def test_returns_none_when_file_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json が存在しない場合は None を返す。"""
        del marketplace_paths
        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_extra_known_marketplace_entry() is None

    def test_returns_none_when_extra_key_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json に extraKnownMarketplaces が無い場合は None を返す。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({"otherSetting": True}, ensure_ascii=False), encoding="utf-8")

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_extra_known_marketplace_entry() is None

    def test_returns_none_when_extra_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces が dict 以外の場合は None を返す。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({"extraKnownMarketplaces": "string"}, ensure_ascii=False), encoding="utf-8")

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_extra_known_marketplace_entry() is None

    def test_returns_none_when_marketplace_key_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces に対象キーが無い場合は None を返す。"""
        _known, settings = marketplace_paths
        settings.write_text(
            json.dumps({"extraKnownMarketplaces": {"other": {}}}, ensure_ascii=False),
            encoding="utf-8",
        )

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_extra_known_marketplace_entry() is None

    def test_returns_none_when_entry_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces[target] が dict でない場合は None を返す。"""
        _known, settings = marketplace_paths
        settings.write_text(
            json.dumps(
                {"extraKnownMarketplaces": {_claude_common.MARKETPLACE_NAME: "not-a-dict"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        # pylint: disable-next=protected-access
        assert _claude_marketplace._load_extra_known_marketplace_entry() is None
