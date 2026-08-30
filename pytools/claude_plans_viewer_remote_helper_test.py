"""pytools.claude_plans_viewer._remote_helper のテスト。

リモートホスト側ヘルパーはSSH経由でファイル内容ごと転送・実行される運用のため、
既存のリモート連携テスト（`claude_plans_viewer_remote_watcher_test.py`等）は
手組みJSONペイロードの注入で`RemoteWatcher`側の受信処理のみを検証しており、
本ファイル自身の関数`_host_info`を直接importして検証するテストが無かった。
`_host_info`の`root`パス区切り正規化漏れ（Windowsリモートホストで`\\`混入）はこの欠落により
自動テストで検出できなかったため、本ファイルで直接契約を固定する。
`_ctime_epoch`は`os.stat_result`の`st_birthtime`有無というプラットフォーム依存の分岐を持ち、
既存の`claude_plans_viewer_test.py`側でも同様の理由で直接テストせず関数差し替えで扱っているため、
本ファイルでも同じ方針を踏襲し対象外とする。
"""

import base64
import hashlib
import json
import os
import pathlib

import pytest

from pytools.claude_plans_viewer import _remote_helper


class TestHostInfo:
    """`_host_info`の戻り値契約を検証する。"""

    def test_root_uses_forward_slash_regardless_of_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """`root`は`_local.local_host_info`と同様に`/`区切りへ正規化される。

        Windows実機無しでバックスラッシュ混入経路を再現するため、`ROOT.resolve()`が
        バックスラッシュ区切り文字列を返す状況を`pathlib.PureWindowsPath`スタブで模擬する。
        """

        class _FakeRoot:
            def resolve(self) -> pathlib.PureWindowsPath:
                return pathlib.PureWindowsPath(r"C:\Users\example\.claude\plans")

        monkeypatch.setattr(_remote_helper, "ROOT", _FakeRoot())

        info = _remote_helper._host_info()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert "\\" not in info["root"]
        assert info["root"] == "C:/Users/example/.claude/plans"

    def test_os_type_and_os_name_reflect_current_platform(self) -> None:
        """`os_type`・`os_name`は現在の`os.name`値をそのまま反映する。"""
        info = _remote_helper._host_info()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert info["os_type"] == os.name
        assert info["os_name"] == os.name

    def test_home_matches_pathlib_home_with_forward_slash(self) -> None:
        """`home`は`pathlib.Path.home()`を`/`区切りへ正規化した値と一致する。"""
        info = _remote_helper._host_info()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert "\\" not in info["home"]
        assert info["home"] == str(pathlib.Path.home()).replace("\\", "/")


class TestListedPath:
    """`_is_target_path`・`_is_listed_path`・`_scan_entries`の付属計画除外契約を検証する。"""

    def test_detail_file_target_but_not_listed(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`.detail.md`・`.bugs.md`は対象だが一覧には載らない。"""
        monkeypatch.setattr(_remote_helper, "ROOT", tmp_path)
        main = tmp_path / "plan.md"
        detail = tmp_path / "plan.detail.md"
        bugs = tmp_path / "plan.bugs.md"
        main.write_text("x", encoding="utf-8")
        detail.write_text("x", encoding="utf-8")
        bugs.write_text("x", encoding="utf-8")

        for attached in (detail, bugs):
            assert _remote_helper._is_target_path(attached)  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため
            assert not _remote_helper._is_listed_path(attached)  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため
        assert _remote_helper._is_listed_path(main)  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

    def test_scan_entries_excludes_detail_file(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """`_scan_entries`（一覧経路）は付属計画を返さない。"""
        monkeypatch.setattr(_remote_helper, "ROOT", tmp_path)
        monkeypatch.setattr(_remote_helper, "_CREATION_TIME_INDEX_PATH", tmp_path / "creation-times" / "index.json")
        (tmp_path / "plan.md").write_text("x", encoding="utf-8")
        (tmp_path / "plan.detail.md").write_text("x", encoding="utf-8")
        (tmp_path / "plan.bugs.md").write_text("x", encoding="utf-8")

        entries = _remote_helper._scan_entries()  # pylint: disable=protected-access  # noqa: SLF001  # モジュール内部契約を直接固定するテストのため

        assert {entry["path"] for entry in entries} == {"plan.md"}

    def test_multiple_roots_include_source_ids_in_snapshot_and_search(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """リモートhelperも同一相対パスをrootごとのsource IDで区別する。"""
        new_root = tmp_path / "new"
        legacy_root = tmp_path / "legacy"
        new_root.mkdir()
        legacy_root.mkdir()
        (new_root / "same.md").write_text("新rootのneedle", encoding="utf-8")
        (legacy_root / "same.md").write_text("旧rootのneedle", encoding="utf-8")
        monkeypatch.setattr(  # pylint: disable=protected-access
            _remote_helper,
            "ROOT",
            _remote_helper._DEFAULT_ROOT,  # pylint: disable=protected-access
        )
        monkeypatch.setattr(
            _remote_helper,
            "ROOTS",
            [
                _remote_helper._RootSpec(  # pylint: disable=protected-access
                    "new-source", new_root, "$(atk config get private_notes)/plans"
                ),
                _remote_helper._RootSpec(  # pylint: disable=protected-access
                    "legacy-source", legacy_root, "~/.claude/plans"
                ),
            ],
        )
        monkeypatch.setattr(_remote_helper, "_CREATION_TIME_INDEX_PATH", tmp_path / "creation-times" / "index.json")

        entries = _remote_helper._scan_entries()  # pylint: disable=protected-access  # noqa: SLF001
        assert {(entry["source_id"], entry["path"]) for entry in entries} == {
            ("new-source", "same.md"),
            ("legacy-source", "same.md"),
        }

        query = base64.b64encode(b"needle").decode("ascii")
        payload = _remote_helper._search_payload(query)  # pylint: disable=protected-access  # noqa: SLF001
        assert {(item["source_id"], item["path"]) for item in payload["matches"]} == {
            ("new-source", "same.md"),
            ("legacy-source", "same.md"),
        }

        source = base64.b64encode(b"new-source").decode("ascii")
        rel = base64.b64encode(b"same.md").decode("ascii")
        read_payload = _remote_helper._read_payload(source, rel)  # pylint: disable=protected-access  # noqa: SLF001
        assert base64.b64decode(read_payload["data"]).decode() == "新rootのneedle"

    def test_only_legacy_root_migrates_matching_legacy_ctime(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """既定二rootの同名パスでは旧rootだけがroot無し旧形式を取り込む。"""
        new_root = tmp_path / "new"
        legacy_root = tmp_path / "legacy"
        new_root.mkdir()
        legacy_root.mkdir()
        for root in (new_root, legacy_root):
            path = root / "same.md"
            path.write_text("x", encoding="utf-8")
            os.utime(path, (2_000.0, 2_000.0))
        monkeypatch.setattr(_remote_helper, "ROOT", _remote_helper._DEFAULT_ROOT)  # pylint: disable=protected-access
        monkeypatch.setattr(
            _remote_helper,
            "ROOTS",
            [
                _remote_helper._RootSpec(  # pylint: disable=protected-access
                    _remote_helper.NEW_SOURCE_ID, new_root, _remote_helper.NEW_PORTABLE_ROOT
                ),
                _remote_helper._RootSpec(  # pylint: disable=protected-access
                    _remote_helper.LEGACY_SOURCE_ID, legacy_root, _remote_helper.LEGACY_PORTABLE_ROOT
                ),
            ],
        )
        index_path = tmp_path / "creation-times" / "index.json"
        monkeypatch.setattr(_remote_helper, "_CREATION_TIME_INDEX_PATH", index_path)
        monkeypatch.setattr(_remote_helper, "_ctime_epoch", lambda st: float(st.st_mtime))
        host = _remote_helper.socket.gethostname()
        digest = hashlib.sha256(f"{host}\0same.md".encode()).hexdigest()
        legacy_cache = index_path.parent / f"{digest}.json"
        legacy_cache.parent.mkdir(parents=True)
        legacy_cache.write_text(
            json.dumps({"host": host, "path": "same.md", "ctime_epoch": 500.0}),
            encoding="utf-8",
        )

        entries = _remote_helper._scan_entries()  # pylint: disable=protected-access  # noqa: SLF001

        ctimes = {entry["source_id"]: entry["ctime_epoch"] for entry in entries}
        assert ctimes == {
            _remote_helper.NEW_SOURCE_ID: 2_000.0,
            _remote_helper.LEGACY_SOURCE_ID: 500.0,
        }
        assert not legacy_cache.exists()

    def test_deduped_new_root_retains_legacy_ctime_migration(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """新旧既定rootが同一実体でも単一表示のまま旧形式ctimeを取り込む。"""
        root = tmp_path / "shared"
        root.mkdir()
        path = root / "same.md"
        path.write_text("x", encoding="utf-8")
        os.utime(path, (2_000.0, 2_000.0))
        specs = _remote_helper._dedupe_roots(  # pylint: disable=protected-access  # noqa: SLF001
            (
                _remote_helper._RootSpec(  # pylint: disable=protected-access
                    _remote_helper.NEW_SOURCE_ID,
                    root,
                    _remote_helper.NEW_PORTABLE_ROOT,
                    migrate_legacy_ctime=False,
                ),
                _remote_helper._RootSpec(  # pylint: disable=protected-access
                    _remote_helper.LEGACY_SOURCE_ID,
                    root,
                    _remote_helper.LEGACY_PORTABLE_ROOT,
                    migrate_legacy_ctime=True,
                ),
            )
        )
        monkeypatch.setattr(_remote_helper, "ROOT", _remote_helper._DEFAULT_ROOT)  # pylint: disable=protected-access
        monkeypatch.setattr(_remote_helper, "ROOTS", specs)
        index_path = tmp_path / "creation-times" / "index.json"
        monkeypatch.setattr(_remote_helper, "_CREATION_TIME_INDEX_PATH", index_path)
        monkeypatch.setattr(_remote_helper, "_ctime_epoch", lambda st: float(st.st_mtime))
        host = _remote_helper.socket.gethostname()
        digest = hashlib.sha256(f"{host}\0same.md".encode()).hexdigest()
        legacy_cache = index_path.parent / f"{digest}.json"
        legacy_cache.parent.mkdir(parents=True)
        legacy_cache.write_text(
            json.dumps({"host": host, "path": "same.md", "ctime_epoch": 500.0}),
            encoding="utf-8",
        )

        entries = _remote_helper._scan_entries()  # pylint: disable=protected-access  # noqa: SLF001

        assert len(specs) == 1
        assert specs[0].source_id == _remote_helper.NEW_SOURCE_ID
        assert [(entry["source_id"], entry["ctime_epoch"]) for entry in entries] == [(_remote_helper.NEW_SOURCE_ID, 500.0)]
        assert not legacy_cache.exists()
