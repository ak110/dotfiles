"""ecoutilitiesモジュールのテスト。"""

import datetime
import os
from pathlib import Path

import httpx
import pytest

from pytools.ecoutilities import (
    Site,
    delete_empty_folders,
    dispose_tc_bookmarks,
    download_custom_sites,
    fix_gvb_by_file_name,
    load_sites,
    random_list,
    run_download_custom,
    year_month_folder_256,
)


def _make_file(path: Path, when: datetime.datetime) -> None:
    """指定の更新日時を持つファイルを作成する。"""
    path.write_bytes(b"x")
    ts = when.timestamp()
    os.utime(path, (ts, ts))


class TestYearMonthFolder256:
    """更新日時順に256件単位で年月フォルダーへ移動する。"""

    @pytest.mark.parametrize(("count", "expected_folders"), [(255, 1), (256, 1), (257, 2)])
    def test_block_split(self, tmp_path: Path, count: int, expected_folders: int):
        """256件の境界でブロックが分割され、全ファイルが移動する。"""
        base = tmp_path / "src"
        base.mkdir()
        start = datetime.datetime(2020, 1, 1)
        for i in range(count):
            _make_file(base / f"f{i:04d}.bin", start + datetime.timedelta(seconds=i))

        year_month_folder_256([str(base)])

        year_dir = base / "2020"
        folders = sorted(p for p in year_dir.iterdir() if p.is_dir())
        assert len(folders) == expected_folders
        moved = [p for p in year_dir.rglob("*") if p.is_file()]
        assert len(moved) == count

    def test_month_reset(self, tmp_path: Path):
        """ブロック先頭の年月が変わると連番がリセットされる。"""
        base = tmp_path / "src"
        base.mkdir()
        jan = datetime.datetime(2020, 1, 1)
        feb = datetime.datetime(2020, 2, 1)
        for i in range(256):
            _make_file(base / f"a{i:04d}.bin", jan + datetime.timedelta(seconds=i))
        for i in range(256):
            _make_file(base / f"b{i:04d}.bin", feb + datetime.timedelta(seconds=i))

        year_month_folder_256([str(base)])

        names = sorted(p.name for p in (base / "2020").iterdir() if p.is_dir())
        assert names == ["2020-01-00", "2020-02-00"]

    def test_no_move_when_already_in_place(self, tmp_path: Path):
        """ファイルが既に目的フォルダーにある場合は移動せず保持する。"""
        base = tmp_path / "src"
        target = base / "2020" / "2020-01-00"
        target.mkdir(parents=True)
        existing = target / "x.bin"
        _make_file(existing, datetime.datetime(2020, 1, 1))

        year_month_folder_256([str(base)])

        assert existing.exists()


def test_delete_empty_folders(tmp_path: Path):
    """空ディレクトリと入れ子の空ディレクトリを削除し、非空は残す。"""
    base = tmp_path / "src"
    (base / "empty").mkdir(parents=True)
    (base / "keep").mkdir()
    (base / "keep" / "f.txt").write_bytes(b"x")
    (base / "nested" / "deep").mkdir(parents=True)

    delete_empty_folders([str(base)])

    assert not (base / "empty").exists()
    assert (base / "keep").exists()
    assert not (base / "nested").exists()


class TestFixGvb:
    """gvb内の絶対パス行を実在パスへ付け替える。"""

    @pytest.mark.parametrize(("stem_len", "replaced"), [(31, False), (32, True)])
    def test_replace_by_name_length(self, tmp_path: Path, stem_len: int, replaced: bool):
        """ファイル名長が32+4文字以上のときのみ置換対象になる。"""
        gvb_dir = tmp_path / "gvb"
        gvb_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        name = "a" * stem_len + ".jpg"
        data_file = data_dir / name
        data_file.write_bytes(b"x")
        old = "C:\\old\\" + name
        gvb = gvb_dir / "t.gvb"
        gvb.write_text(old, encoding="utf-16")

        fix_gvb_by_file_name(str(gvb_dir), str(data_dir))

        result = gvb.read_text(encoding="utf-16").splitlines()
        assert result[0] == (str(data_file) if replaced else old)

    def test_skips_zip_relative_existing(self, tmp_path: Path):
        """相対パス・`.zip\\`を含む行・既存ファイル行は対象外とする。"""
        gvb_dir = tmp_path / "gvb"
        gvb_dir.mkdir()
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        name = "a" * 32 + ".jpg"
        data_file = data_dir / name
        data_file.write_bytes(b"x")
        lines = [
            "C:\\old\\" + name,
            "relative\\" + name,
            "C:\\a.zip\\" + name,
            str(data_file),
        ]
        gvb = gvb_dir / "t.gvb"
        gvb.write_text("\r\n".join(lines), encoding="utf-16")

        fix_gvb_by_file_name(str(gvb_dir), str(data_dir))

        result = gvb.read_text(encoding="utf-16").splitlines()
        assert result[0] == str(data_file)
        assert result[1] == "relative\\" + name
        assert result[2] == "C:\\a.zip\\" + name
        assert result[3] == str(data_file)

    def test_usage_when_not_directory(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]):
        """引数が既存ディレクトリでない場合はUsageを表示する。"""
        fix_gvb_by_file_name(str(tmp_path / "nonexist"), str(tmp_path))

        assert "Usage" in capsys.readouterr().out


def test_dispose_tc_bookmarks(tmp_path: Path):
    """`-m`接尾の項目ごとに同一接頭辞のファイルをまとめて移動する。"""
    base = tmp_path / "bm"
    base.mkdir()
    for name in ["abc-1.gvb", "abc-m.gvb", "xyz-m.gvb", "xyz-2.gvb"]:
        (base / name).write_bytes(b"x")

    dispose_tc_bookmarks(str(base))

    dest = base / "00" / "000"
    moved = sorted(p.name for p in dest.iterdir())
    assert moved == ["abc-1.gvb", "abc-m.gvb", "xyz-2.gvb", "xyz-m.gvb"]


def test_dispose_tc_bookmarks_count_boundary(tmp_path: Path):
    """連番が10に達すると2階層目のサブフォルダーが繰り上がる。"""
    base = tmp_path / "bm"
    base.mkdir()
    for i in range(11):
        (base / f"{i:02d}x-m.gvb").write_bytes(b"x")

    dispose_tc_bookmarks(str(base))

    assert (base / "00" / "000").is_dir()
    assert (base / "00" / "001").is_dir()


class TestRandomList:
    """ランダム抽出してリストを出力する。"""

    @pytest.mark.parametrize(("count", "expected"), [(0, 0), (63, 63), (64, 64), (100, 64)])
    def test_count(self, tmp_path: Path, count: int, expected: int):
        """抽出件数は最大64件で頭打ちになる。"""
        src = tmp_path / "src"
        src.mkdir()
        for i in range(count):
            (src / f"f{i:03d}.txt").write_bytes(b"x")
        out = tmp_path / "list.txt"

        random_list(str(src), str(out), "utf-8")

        text = out.read_text(encoding="utf-8")
        assert len([line for line in text.splitlines() if line]) == expected

    def test_bom_utf8_sig(self, tmp_path: Path):
        """utf-8-sig指定時はBOMが付与される。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_bytes(b"x")
        out = tmp_path / "list.m3u8"

        random_list(str(src), str(out), "utf-8-sig")

        assert out.read_bytes().startswith(b"\xef\xbb\xbf")

    def test_cp932(self, tmp_path: Path):
        """cp932指定時はcp932で読み込める内容を出力する。"""
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_bytes(b"x")
        out = tmp_path / "list.txt"

        random_list(str(src), str(out), "cp932")

        assert "a.txt" in out.read_text(encoding="cp932")


def test_sites_xml_roundtrip(tmp_path: Path):
    """サンプル出力したXMLを読み戻すと同じ設定になる。"""
    path = tmp_path / "Sites.xml"
    run_download_custom(str(path))

    sites = load_sites(path)

    assert len(sites) == 1
    site = sites[0]
    assert site.download_folder_path == r"C:\example\dummy"
    assert site.first_url == "http://dummy.example.com/dummy.jsp"
    assert site.base_url == "http://dummy.example.com/"
    assert site.link_patterns == [r"/dummy/content/.*\.jpg"]
    assert site.exclude_patterns == [r"\.avi$"]
    assert site.flat_directory is False


def test_run_download_custom_writes_sample(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    """設定ファイルが無い場合はサンプルを出力する。"""
    path = tmp_path / "Sites.xml"

    run_download_custom(str(path))

    assert path.exists()
    assert "Sites.xml" in capsys.readouterr().out


def _client(handler) -> httpx.Client:
    """MockTransportで擬似応答を返すHTTPクライアントを生成する。"""
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_crawl_single_pattern(tmp_path: Path):
    """単一パターンでBaseURL配下のリンクのみダウンロードする。"""
    dl = tmp_path / "dl"
    base = "http://example.com/"
    body = '<a href="http://example.com/img/a.jpg">x</a><a href="http://other.com/b.jpg">y</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/index.html":
            return httpx.Response(200, text=body)
        if request.url.path == "/img/a.jpg":
            return httpx.Response(200, content=b"JPEGDATA")
        return httpx.Response(404)

    site = Site(
        download_folder_path=str(dl),
        first_url=base + "index.html",
        base_url=base,
        link_patterns=[r"/img/.*\.jpg"],
    )
    download_custom_sites([site], _client(handler))

    assert (dl / "img" / "a.jpg").read_bytes() == b"JPEGDATA"
    assert not (dl / "b.jpg").exists()


def test_crawl_two_patterns(tmp_path: Path):
    """2段パターンで一覧から個別ページを辿ってダウンロードする。"""
    dl = tmp_path / "dl"
    base = "http://example.com/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/list.html":
            return httpx.Response(200, text='<a href="http://example.com/page/1.html">p</a>')
        if request.url.path == "/page/1.html":
            return httpx.Response(200, text='<a href="http://example.com/img/x.jpg">i</a>')
        if request.url.path == "/img/x.jpg":
            return httpx.Response(200, content=b"IMG")
        return httpx.Response(404)

    site = Site(
        download_folder_path=str(dl),
        first_url=base + "list.html",
        base_url=base,
        link_patterns=[r"/page/.*\.html", r"/img/.*\.jpg"],
    )
    download_custom_sites([site], _client(handler))

    assert (dl / "img" / "x.jpg").read_bytes() == b"IMG"


def test_crawl_exclude_pattern(tmp_path: Path):
    """除外パターンに一致するURLは保存しない。"""
    dl = tmp_path / "dl"
    base = "http://example.com/"
    body = '<a href="http://example.com/a.jpg">1</a><a href="http://example.com/skip.avi">2</a>'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/index.html":
            return httpx.Response(200, text=body)
        return httpx.Response(200, content=b"DATA")

    site = Site(
        download_folder_path=str(dl),
        first_url=base + "index.html",
        base_url=base,
        link_patterns=[r"\.(jpg|avi)$"],
        exclude_patterns=[r"\.avi$"],
    )
    download_custom_sites([site], _client(handler))

    assert (dl / "a.jpg").exists()
    assert not (dl / "skip.avi").exists()


def test_crawl_flat_directory(tmp_path: Path):
    """FlatDirectory指定時は区切り文字を置換して平坦化する。"""
    dl = tmp_path / "dl"
    base = "http://example.com/"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/index.html":
            return httpx.Response(200, text='<a href="http://example.com/sub/deep/a.jpg">x</a>')
        return httpx.Response(200, content=b"D")

    site = Site(
        download_folder_path=str(dl),
        first_url=base + "index.html",
        base_url=base,
        link_patterns=[r"\.jpg$"],
        flat_directory=True,
    )
    download_custom_sites([site], _client(handler))

    assert (dl / "sub_deep_a.jpg").exists()


def test_crawl_skips_existing_file(tmp_path: Path):
    """既存ファイルは再取得しない。"""
    dl = tmp_path / "dl"
    dl.mkdir()
    base = "http://example.com/"
    existing = dl / "a.jpg"
    existing.write_bytes(b"OLD")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/index.html":
            return httpx.Response(200, text='<a href="http://example.com/a.jpg">x</a>')
        return httpx.Response(200, content=b"NEW")

    site = Site(
        download_folder_path=str(dl),
        first_url=base + "index.html",
        base_url=base,
        link_patterns=[r"\.jpg$"],
    )
    download_custom_sites([site], _client(handler))

    assert existing.read_bytes() == b"OLD"
    assert "/a.jpg" not in requested
