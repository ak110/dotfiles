"""repack_archiveのconversionの試験。"""

# pylint: disable=protected-access

import logging
import pathlib
import shutil
import zipfile

import pytest

from pytools import repack_archive
from pytools._internal.repack_archive_test_helpers import (
    _make_input,
    _make_pdf,
    _make_tar,
    _make_zip,
    _png_bytes,
    _png_bytes_with_corrupt_text,
    _run_main,
    _snapshot,
    _zip_entries,
)


class TestImageConversionBackupRetention:
    """画像変換の警告・失敗で原本バックアップが保持されることの検証。"""

    def test_corrupt_text_png_warning_keeps_backup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """tEXt CRC 不整合 PNG は寛容モードで変換されつつバックアップが保持される。"""
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("img.png", _png_bytes_with_corrupt_text())
            # 単一ルート平坦化に巻き込まれないよう別エントリも置く
            zf.writestr("readme.txt", b"hello")
        exit_code = _run_main(monkeypatch, [str(archive)])
        assert exit_code == 0
        # 出力 ZIP には JPEG が含まれる
        entries = _zip_entries(tmp_path / "sample.zip")
        assert "img.jpg" in entries
        # 警告があったためバックアップは保持され send2trash も呼ばれない
        assert (tmp_path / "bk" / "sample.zip").exists()
        assert not trashed

    def test_completely_broken_image_is_skipped_as_non_image(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """完全に破損した PNG はシグネチャ判定で非画像と扱われ、削除・失敗集計の対象外となる。

        `PIL.Image.open()` のシグネチャ判定で読み込めないファイルは
        非画像扱いでイベント非記録・削除なしとなる。
        失敗集計が空になるためバックアップは `send2trash` でゴミ箱送りされる。
        """
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("img.png", b"not a PNG file at all")
            zf.writestr("readme.txt", b"hello")
        exit_code = _run_main(monkeypatch, [str(archive)])
        assert exit_code == 0
        # 失敗が無いためバックアップはゴミ箱送りされる
        assert len(trashed) == 1
        assert trashed[0].endswith("sample.zip")
        # 非画像扱いで削除されないため、原本のバイト列がそのまま出力 ZIP に含まれる
        entries = _zip_entries(tmp_path / "sample.zip")
        assert "readme.txt" in entries
        assert "img.png" in entries

    def test_clean_run_trashes_backup(
        self,
        tmp_path: pathlib.Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """警告・失敗が無い場合はバックアップがゴミ箱送りされる (回帰防止)。"""
        trashed: list[str] = []
        monkeypatch.setattr(
            "pytools.repack_archive.send2trash.send2trash",
            lambda path: trashed.append(str(path)),
        )
        archive = tmp_path / "sample.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("img.png", _png_bytes())
            zf.writestr("readme.txt", b"hello")
        exit_code = _run_main(monkeypatch, [str(archive)])
        assert exit_code == 0
        # 通常の変換成功ではバックアップが send2trash 経由で送られる
        assert len(trashed) == 1
        assert trashed[0].endswith("sample.zip")


class TestMainFailureSummary:
    """複数 target の失敗集約と終了コードの検証。"""

    def test_failure_summary_lists_failed_targets(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        good = tmp_path / "good.zip"
        _make_zip(good, {"a.txt": b"a"})
        # ZIP の体をなさないファイル。`_preflight_check` は存在チェックのみ通過し、
        # libarchive がフォーマットエラーで例外を送出する。
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip file at all, totally garbage contents")

        monkeypatch.setattr("sys.argv", ["repack-archive", "--no-trash", str(good), str(bad)])
        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        with pytest.raises(SystemExit) as exc_info:
            repack_archive.main()
        assert exc_info.value.code == 1

        # 良い target は出力 ZIP が残り、失敗 target はサマリーに列挙される
        assert (tmp_path / "good.zip").exists()
        summary = "\n".join(r.getMessage() for r in caplog.records)
        assert "失敗したターゲット" in summary
        assert str(bad) in summary


class TestZeroTargetAfterFilter:
    """フィルタ後に対象ファイルが 0 件となった入力を ``ValueError`` でスキップする挙動の検証。

    3経路 (``_extract_zip`` / ``_extract_with_libarchive`` / ``_copy_filtered``) それぞれで、
    全エントリが ignore 対象になり展開・コピー対象が 0 件になるケースを再現する。
    ``main()`` の try/except が ``ValueError`` を捕捉して ``failed_targets`` に集約し、
    後続 target の処理を継続することを、良好な target との同時実行で検証する。
    """

    def test_extract_zip_all_ignored_raises(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_extract_zip`` 経路: ZIP 内の全エントリが ignore_files に一致し 0 件になる。"""
        good = tmp_path / "good.zip"
        _make_zip(good, {"keep.txt": b"k"})
        empty = tmp_path / "empty.zip"
        _make_zip(empty, {"drop.pdf": b"p"})
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_files: ['*.pdf']\n", encoding="utf-8")

        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(good), str(empty)],
        )
        assert exit_code == 1
        # 良好な target は処理が継続され出力 ZIP が残る
        assert _zip_entries(tmp_path / "good.zip") == {"keep.txt"}
        # 対象 0 件の target は failed_targets に集約される
        summary = "\n".join(r.getMessage() for r in caplog.records)
        assert "対象ファイルが0件のためスキップします" in summary
        assert str(empty) in summary

    def test_extract_with_libarchive_all_ignored_raises(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_extract_with_libarchive`` 経路: tar 内の全エントリが ignore_files に一致し 0 件になる。"""
        good = tmp_path / "good.zip"
        _make_zip(good, {"keep.txt": b"k"})
        empty = tmp_path / "empty.tar"
        _make_tar(empty, {"drop.pdf": b"p"})
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_files: ['*.pdf']\n", encoding="utf-8")

        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(good), str(empty)],
        )
        assert exit_code == 1
        assert _zip_entries(tmp_path / "good.zip") == {"keep.txt"}
        summary = "\n".join(r.getMessage() for r in caplog.records)
        assert "対象ファイルが0件のためスキップします" in summary
        assert str(empty) in summary

    def test_copy_filtered_all_ignored_raises(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_copy_filtered`` 経路: ディレクトリ内の全ファイルが ignore_files に一致し 0 件になる。"""
        good = tmp_path / "good.zip"
        _make_zip(good, {"keep.txt": b"k"})
        empty_dir = tmp_path / "empty_book"
        empty_dir.mkdir()
        (empty_dir / "drop.pdf").write_bytes(b"p")
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_files: ['*.pdf']\n", encoding="utf-8")

        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        exit_code = _run_main(
            monkeypatch,
            ["--no-trash", "--config", str(config_yaml), str(good), str(empty_dir)],
        )
        assert exit_code == 1
        assert _zip_entries(tmp_path / "good.zip") == {"keep.txt"}
        summary = "\n".join(r.getMessage() for r in caplog.records)
        assert "対象ファイルが0件のためスキップします" in summary
        assert str(empty_dir) in summary

    def test_ignore_dirs_removal_to_zero_restores_original(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """ignore_dirs除去後に対象0件となるZIPでは原本を復元する。"""
        archive = tmp_path / "empty.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("root/NoText/drop.txt", b"drop")
            zf.writestr("root/No_Text/also-drop.txt", b"drop")
        original = archive.read_bytes()
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")

        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(archive)])

        assert exit_code == 1
        assert archive.read_bytes() == original
        assert archive.exists()
        assert "対象ファイルが0件のためスキップします" in "\n".join(r.getMessage() for r in caplog.records)

    @pytest.mark.parametrize("archive_kind", ["tar", "directory"])
    def test_ignore_dirs_removal_to_zero_restores_original_for_other_inputs(
        self,
        tmp_path: pathlib.Path,
        caplog: pytest.LogCaptureFixture,
        monkeypatch: pytest.MonkeyPatch,
        archive_kind: str,
    ) -> None:
        """libarchive・ディレクトリ経路でもignore_dirs除去後の原本復元を行う。"""
        config_yaml = tmp_path / "repack-archive.yaml"
        config_yaml.write_text("ignore_dirs: ['No[-_ ]?Te?xt']\n", encoding="utf-8")
        if archive_kind == "tar":
            target = tmp_path / "empty.tar"
            _make_tar(
                target,
                {
                    "root/NoText/drop.txt": b"drop",
                    "root/No_Text/also-drop.txt": b"drop",
                },
            )
        else:
            target = tmp_path / "empty_book"
            (target / "root" / "NoText").mkdir(parents=True)
            (target / "root" / "No_Text").mkdir()
            (target / "root" / "NoText" / "drop.txt").write_bytes(b"drop")
            (target / "root" / "No_Text" / "also-drop.txt").write_bytes(b"drop")
        original = (
            target.read_bytes()
            if target.is_file()
            else sorted(
                (path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file()
            )
        )

        caplog.set_level(logging.WARNING, logger=repack_archive.logger.name)
        exit_code = _run_main(monkeypatch, ["--no-trash", "--config", str(config_yaml), str(target)])

        assert exit_code == 1
        if target.is_file():
            assert target.read_bytes() == original
        else:
            assert target.is_dir()
            assert (
                sorted((path.relative_to(target).as_posix(), path.read_bytes()) for path in target.rglob("*") if path.is_file())
                == original
            )
        assert "対象ファイルが0件のためスキップします" in "\n".join(r.getMessage() for r in caplog.records)


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="Poppler (pdftoppm) 未導入")
class TestProcessPdf:
    """PDF 直接入力をページ画像へ再パックする挙動の検証。"""

    def test_pdf_input_repacks_pages(self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """複数ページ PDF が連番 JPEG として自然順で再パックされ、バックアップが残る。"""
        pdf = tmp_path / "book.pdf"
        # 縦長 (幅が max_width 以内) と横長 (幅が max_width 超過で縮小経路) を混在させる
        _make_pdf(pdf, [((600, 800), "white"), ((1200, 400), "red")])
        exit_code = _run_main(monkeypatch, ["--no-trash", str(pdf)])
        assert exit_code == 0
        output = tmp_path / "book.zip"
        assert output.exists()
        with zipfile.ZipFile(output) as zf:
            ordered = [info.filename for info in zf.infolist()]
        # 出力形式を指定しない場合は jpeg。ページはゼロ埋め連番で自然順に並ぶ
        assert ordered == ["0001.jpg", "0002.jpg"]
        # バックアップは元の PDF 名のまま保持される
        assert (tmp_path / "bk" / "book.pdf").exists()


class TestPreparationFailureRestoresOriginal:
    """原本の退避後、作業領域の準備段で失敗しても公開`main`の後に原本が元のパスへ戻る。

    退避と復元の保護区間が分かれていると、準備段の例外は原本をバックアップ先だけに残す。
    """

    @pytest.mark.parametrize("kind", ["zip", "directory", "pdf"])
    def test_workdir_creation_failure_restores_original(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, kind: str
    ) -> None:
        target = _make_input(tmp_path, kind)
        original = _snapshot(target)

        def fail_mkdtemp(*args: object, **kwargs: object) -> str:
            del args, kwargs
            raise OSError(36, "File name too long")

        monkeypatch.setattr(repack_archive.tempfile, "mkdtemp", fail_mkdtemp)
        exit_code = _run_main(monkeypatch, ["--no-trash", str(target)])

        assert exit_code == 1
        assert target.exists()
        assert _snapshot(target) == original
        assert not (tmp_path / "bk" / target.name).exists()

    @pytest.mark.parametrize(
        ("kind", "stage"),
        [("zip", "_extract_archive"), ("directory", "_copy_filtered"), ("pdf", "_render_pdf")],
    )
    def test_format_specific_preparation_failure_restores_original(
        self, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, kind: str, stage: str
    ) -> None:
        target = _make_input(tmp_path, kind)
        original = _snapshot(target)

        def fail_stage(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("準備段の失敗")

        monkeypatch.setattr(repack_archive, stage, fail_stage)
        exit_code = _run_main(monkeypatch, ["--no-trash", str(target)])

        assert exit_code == 1
        assert _snapshot(target) == original
        assert not (tmp_path / "bk" / target.name).exists()
        assert not list(tmp_path.glob(f"{repack_archive._WORKDIR_PREFIX}*"))  # pylint: disable=protected-access
