"""pytools._internal.claude_marketplace の単体テスト。

本ファイルでは以下の観点を網羅する。

- 健全性検証: ensure_marketplace / is_directory_type_registered の各分岐
- 修復ロジック: repair_marketplace の CLI 成功・直接書き換えフォールバック・失敗経路
- 設定ファイル直書きの境界条件:
    ファイル不在・書き込み失敗・既存キー保持などのケースを
    repair_marketplace / ensure_marketplace の振る舞いで確認する
- marketplace list 出力の各パース形式の許容: ensure_marketplace の CLI 経路で確認
- ensure_marketplace の各分岐 (file_check True/False/None)
- refresh_marketplace の成功・失敗

install_claude_plugins_repair_test.py と install_claude_plugins_test.py の
TestCheckMarketplaceFromFile・TestLegacyGithubTypeMigration・TestRepairMarketplace などとの
重複を避け、本ファイルではそれらで扱っていない分岐・パラメーター化ケースを追加する。
"""

import json
import pathlib

import pytest

from pytools._internal import claude_common as _claude_common
from pytools._internal import claude_marketplace as _claude_marketplace

from ._test_helpers import _FakeResult, write_known_entry, write_settings_entry

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


# --- TestMarketplaceAlreadyRegistered ---


class TestMarketplaceAlreadyRegistered:
    """marketplace list 出力の各パース形式が ensure_marketplace() の登録判定に正しく反映されること。

    `marketplace list --json` の出力形式はバージョンによって異なるため、
    複数の形式を許容していることを ensure_marketplace() の CLI 経路で確認する。
    両ファイルが存在しない状態 (file_check が None) で CLI 経路のみを通す。
    """

    @pytest.fixture(autouse=True)
    def _no_files(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ) -> None:
        """両ファイルを存在しない状態にして file_check が None になるよう設定する。"""
        del marketplace_paths  # ファイルを生成しない (存在しないパスが設定済み)

    def _ensure_with_list_output(
        self,
        monkeypatch: pytest.MonkeyPatch,
        list_output: object,
    ) -> tuple[bool, list[list[str]]]:
        """marketplace list に指定の出力を返す fake_run で ensure_marketplace を実行し結果を返す。"""
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):  # noqa: ANN001
            calls.append(cmd)
            if cmd[:4] == ["claude", "plugin", "marketplace", "list"]:
                return _FakeResult(returncode=0, stdout=json.dumps(list_output, ensure_ascii=False))
            if cmd[:4] == ["claude", "plugin", "marketplace", "add"]:
                return _FakeResult(returncode=0)
            return _FakeResult(returncode=1)

        monkeypatch.setattr(_claude_common.subprocess, "run", fake_run)
        result = _claude_marketplace.ensure_marketplace()
        return result, calls

    def test_list_with_name_key_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        """リスト形式: name キーが一致すれば登録済みと判断して add を呼ばない。"""
        data = [{"name": _claude_common.MARKETPLACE_NAME}]
        result, calls = self._ensure_with_list_output(monkeypatch, data)
        assert result is True
        assert not any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)

    def test_list_without_target_calls_add(self, monkeypatch: pytest.MonkeyPatch):
        """リスト形式: 対象 name が含まれなければ未登録として add を呼ぶ。"""
        data = [{"name": "other-marketplace"}]
        result, calls = self._ensure_with_list_output(monkeypatch, data)
        assert result is True
        assert any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)

    def test_empty_list_calls_add(self, monkeypatch: pytest.MonkeyPatch):
        """空リストは未登録として add を呼ぶ。"""
        result, calls = self._ensure_with_list_output(monkeypatch, [])
        assert result is True
        assert any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)

    def test_dict_with_marketplaces_key_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        """{marketplaces: [...]} の入れ子形式は再帰的にパースし登録済みと判断する。"""
        data = {"marketplaces": [{"name": _claude_common.MARKETPLACE_NAME}]}
        result, calls = self._ensure_with_list_output(monkeypatch, data)
        assert result is True
        assert not any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)

    def test_flat_dict_contains_name_skips_add(self, monkeypatch: pytest.MonkeyPatch):
        """フラット dict 形式: トップレベルのキーが name と一致すれば登録済みと判断する。"""
        data: dict[str, object] = {_claude_common.MARKETPLACE_NAME: {}}
        result, calls = self._ensure_with_list_output(monkeypatch, data)
        assert result is True
        assert not any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)

    def test_flat_dict_no_target_calls_add(self, monkeypatch: pytest.MonkeyPatch):
        """フラット dict 形式: 対象キーが無ければ未登録として add を呼ぶ。"""
        data: dict[str, object] = {"other": {}}
        result, calls = self._ensure_with_list_output(monkeypatch, data)
        assert result is True
        assert any(c[:4] == ["claude", "plugin", "marketplace", "add"] for c in calls)


# --- TestRewriteKnownMarketplacesEntry ---


class TestRewriteKnownMarketplacesEntry:
    """known_marketplaces.json 書き換えの境界条件を repair_marketplace 経由で確認する。

    直接書き換え経路 (``_rewrite_known_marketplaces_entry``) に到達させるには、
    CLI add の後も ``_check_marketplace_from_file()`` が False を返すことが必要。
    そのため各テストでは known に旧 GitHub 型エントリを置いておく
    (CLI の fake_run はファイルを更新しないため recheck == False が継続する)。
    """

    def test_overwrites_legacy_entry_with_directory_type(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """旧 GitHub 型エントリを直接書き換えて directory 型に修復する。"""
        known, _settings = marketplace_paths
        # 旧 GitHub 型 → CLI add 後も recheck が False のまま → 直接書き換え経路へ
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        result = _claude_marketplace.repair_marketplace()

        assert result is True
        data = json.loads(known.read_text(encoding="utf-8"))
        entry = data[_claude_common.MARKETPLACE_NAME]
        assert entry["source"] == {"source": "directory", "path": str(dotfiles_root)}
        assert entry["installLocation"] == str(dotfiles_root)
        assert isinstance(entry["lastUpdated"], str) and entry["lastUpdated"].endswith("Z")

    def test_preserves_other_keys(
        self,
        monkeypatch: pytest.MonkeyPatch,
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
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        result = _claude_marketplace.repair_marketplace()

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
    ):
        """atomic write が失敗すると repair_marketplace が False を返す。"""
        known, _settings = marketplace_paths
        # 旧 GitHub 型 → recheck=False → 直接書き換え経路へ
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        assert _claude_marketplace.repair_marketplace() is False

    def test_invalid_json_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """known_marketplaces.json が不正 JSON の場合は repair_marketplace が False を返す。

        不正 JSON は ``_load_known_marketplace_entry`` で None を返すため
        ``_check_marketplace_from_file`` 的には「ファイル不在」と同等の None 扱いになる。
        直接書き換え経路では ``load_json_dict`` が None を返すため False となる。
        recheck=None + settings の不正 JSON で直接書き換え経路に到達させる。
        """
        known, settings = marketplace_paths
        # known: 旧 GitHub 型 → recheck=False → 直接書き換えへ
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        # settings: 不正 JSON → _rewrite_settings_extra_known_entry が False を返す
        settings.write_text("{invalid", encoding="utf-8")
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        assert _claude_marketplace.repair_marketplace() is False


# --- TestRewriteSettingsExtraKnownEntry ---


class TestRewriteSettingsExtraKnownEntry:
    """settings.json 書き換えの境界条件を repair_marketplace 経由で確認する。

    直接書き換え経路に到達させるため、各テストでは known に旧 GitHub 型エントリを置く。
    settings 側の挙動を孤立して確認するには、known の書き換えを成功させておく必要がある。
    """

    def test_settings_not_exists_returns_true_without_write(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json が存在しない場合は何も書かず、known のみ修復して True を返す。"""
        known, settings = marketplace_paths
        # known を旧 GitHub 型にして直接書き換え経路へ誘導する
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        assert not settings.exists()
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        result = _claude_marketplace.repair_marketplace()

        assert result is True
        assert not settings.exists()

    def test_overwrites_existing_entry(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """既存の旧形式エントリを directory 型に上書きする。"""
        known, settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        write_settings_entry(settings, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        result = _claude_marketplace.repair_marketplace()

        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        entry = data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]
        assert entry == {"source": {"source": "directory", "path": str(dotfiles_root)}}
        # settings 側は installLocation を持たない
        assert "installLocation" not in entry

    def test_creates_extra_known_key_when_absent(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json が存在するが extraKnownMarketplaces がない場合は追加する。"""
        known, settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        settings.write_text(json.dumps({"otherSetting": True}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        result = _claude_marketplace.repair_marketplace()

        assert result is True
        data = json.loads(settings.read_text(encoding="utf-8"))
        assert "otherSetting" in data
        assert data["extraKnownMarketplaces"][_claude_common.MARKETPLACE_NAME]["source"]["source"] == "directory"

    def test_write_failure_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """atomic write が失敗すると repair_marketplace が False を返す。"""
        known, settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        settings.write_text(json.dumps({"extraKnownMarketplaces": {}}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        def fail_replace(*_args, **_kwargs):
            raise OSError("permission denied")

        monkeypatch.setattr(_claude_common.os, "replace", fail_replace)

        assert _claude_marketplace.repair_marketplace() is False

    def test_invalid_json_returns_false(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json が不正 JSON の場合は repair_marketplace が False を返す。"""
        known, settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        settings.write_text("{invalid", encoding="utf-8")
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        assert _claude_marketplace.repair_marketplace() is False

    def test_extra_known_marketplaces_not_dict(
        self,
        monkeypatch: pytest.MonkeyPatch,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces が dict でない場合は初期化して directory 型を書き込む。"""
        known, settings = marketplace_paths
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})
        settings.write_text(json.dumps({"extraKnownMarketplaces": []}, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(_claude_common.subprocess, "run", lambda *_a, **_k: _FakeResult(returncode=0))

        result = _claude_marketplace.repair_marketplace()

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
        write_known_entry(known, {"source": {"source": "github", "repo": "ak110/dotfiles"}})

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
    """known_marketplaces.json の読み込み分岐が is_directory_type_registered() に正しく反映されること。"""

    def test_returns_entry_when_exists(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """known_marketplaces.json に健全な directory 型エントリがあれば True。"""
        known, _settings = marketplace_paths
        write_known_entry(
            known,
            {
                "source": {"source": "directory", "path": str(dotfiles_root)},
                "installLocation": str(dotfiles_root),
            },
        )
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_returns_false_when_file_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """ファイルが存在しない場合は is_directory_type_registered が False。"""
        del marketplace_paths  # 2 ファイルが存在しない状態のまま
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_key_missing(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """ファイルに対象キーが無い場合は is_directory_type_registered が False。"""
        known, _settings = marketplace_paths
        known.write_text(json.dumps({"other": {}}, ensure_ascii=False), encoding="utf-8")
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_entry_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """エントリが dict でない場合は is_directory_type_registered が False。"""
        known, _settings = marketplace_paths
        known.write_text(
            json.dumps({_claude_common.MARKETPLACE_NAME: "not-a-dict"}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_invalid_json(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """JSON 解析失敗の場合は is_directory_type_registered が False。"""
        known, _settings = marketplace_paths
        known.write_text("{broken", encoding="utf-8")
        assert _claude_marketplace.is_directory_type_registered() is False


# --- TestLoadExtraKnownMarketplaceEntry ---


class TestLoadExtraKnownMarketplaceEntry:
    """settings.json の読み込み分岐が is_directory_type_registered() に正しく反映されること。"""

    def test_returns_entry_when_exists(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
        dotfiles_root: pathlib.Path,
    ):
        """settings.json に健全な directory 型エントリがあれば True。"""
        _known, settings = marketplace_paths
        write_settings_entry(settings, {"source": {"source": "directory", "path": str(dotfiles_root)}})
        assert _claude_marketplace.is_directory_type_registered() is True

    def test_returns_false_when_file_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json が存在しない場合は is_directory_type_registered が False。"""
        del marketplace_paths
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_extra_key_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """settings.json に extraKnownMarketplaces が無い場合は is_directory_type_registered が False。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({"otherSetting": True}, ensure_ascii=False), encoding="utf-8")
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_extra_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces が dict 以外の場合は is_directory_type_registered が False。"""
        _known, settings = marketplace_paths
        settings.write_text(json.dumps({"extraKnownMarketplaces": "string"}, ensure_ascii=False), encoding="utf-8")
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_marketplace_key_absent(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces に対象キーが無い場合は is_directory_type_registered が False。"""
        _known, settings = marketplace_paths
        settings.write_text(
            json.dumps({"extraKnownMarketplaces": {"other": {}}}, ensure_ascii=False),
            encoding="utf-8",
        )
        assert _claude_marketplace.is_directory_type_registered() is False

    def test_returns_false_when_entry_not_dict(
        self,
        marketplace_paths: tuple[pathlib.Path, pathlib.Path],
    ):
        """extraKnownMarketplaces[target] が dict でない場合は is_directory_type_registered が False。"""
        _known, settings = marketplace_paths
        settings.write_text(
            json.dumps(
                {"extraKnownMarketplaces": {_claude_common.MARKETPLACE_NAME: "not-a-dict"}},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        assert _claude_marketplace.is_directory_type_registered() is False
