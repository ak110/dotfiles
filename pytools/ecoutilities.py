# PYTHON_ARGCOMPLETE_OK
"""C#製EcoUtilities.exeの移植。

ファイル整理・gvbパス修正・ランダム抽出・サイトクロールのサブコマンドを提供する。
C#版のサブコマンド名・引数・出力を維持し、`EcoUtilities`コマンドの差し替えで流用できるようにする。
"""

import argparse
import datetime
import errno
import html
import ntpath
import os
import random
import re
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx
import pydantic

from pytools._internal.cli import enable_completion

_RANDOM_TAKE = 64
_BLOCK_SIZE = 256
_GVB_ENCODING = "utf-16"
_RE_QUOTED = re.compile(r"""(href|src)\s*=\s*["']([^"']+)["']""")
_HTTP_TIMEOUT = 30.0


def main() -> None:
    """サブコマンドを解釈して各処理へ委譲するエントリポイント。"""
    parser = argparse.ArgumentParser(prog="EcoUtilities", description="ファイル整理・クロール用のユーティリティ集。")
    sub = parser.add_subparsers(dest="mode", metavar="mode")

    p_ymf = sub.add_parser("YearMonthFolder256", help="年月ごと＋256ファイル単位でフォルダーへ振り分ける。")
    p_ymf.add_argument("dirs", nargs="+", help="対象ディレクトリ。")

    p_def = sub.add_parser("DeleteEmptyFolders", help="空フォルダーを削除する。")
    p_def.add_argument("dirs", nargs="+", help="対象ディレクトリ。")

    p_fix = sub.add_parser("FixGVBByFileName", help="gvb内のパスをファイル名一致で付け替える。")
    p_fix.add_argument("gvb_dir", help="gvbファイルのフォルダー。")
    p_fix.add_argument("data_dir", help="データフォルダー。")

    p_dtc = sub.add_parser("DisposeTCBookmarks", help="ブックマークを接頭辞ごとに整理する。")
    p_dtc.add_argument("path", help="対象フォルダー。")

    p_rl = sub.add_parser("RandomList", help="ランダム抽出してリストを出力する（既定cp932）。")
    p_rl.add_argument("dir", help="対象フォルダー。")
    p_rl.add_argument("list_path", help="出力先リストファイル。")
    p_rl.add_argument("--encoding", default="cp932", help="出力エンコーディング（既定: cp932）。")

    p_rm = sub.add_parser("RandomM3U8", help="ランダム抽出してm3u8を出力する（既定BOM付きUTF-8）。")
    p_rm.add_argument("dir", help="対象フォルダー。")
    p_rm.add_argument("list_path", help="出力先リストファイル。")
    p_rm.add_argument("--encoding", default="utf-8-sig", help="出力エンコーディング（既定: utf-8-sig）。")

    p_dc = sub.add_parser("DownloadCustom", help="設定ファイルに従いサイトをクロールしてダウンロードする。")
    p_dc.add_argument("sites_file", nargs="?", default="Sites.xml", help="サイト設定XML（既定: Sites.xml）。")

    enable_completion(parser)
    args = parser.parse_args()

    if args.mode is None:
        print("Usage: EcoUtilities mode ...")
        sys.exit(1)

    if args.mode == "YearMonthFolder256":
        year_month_folder_256(args.dirs)
    elif args.mode == "DeleteEmptyFolders":
        delete_empty_folders(args.dirs)
    elif args.mode == "FixGVBByFileName":
        fix_gvb_by_file_name(args.gvb_dir, args.data_dir)
    elif args.mode == "DisposeTCBookmarks":
        dispose_tc_bookmarks(args.path)
    elif args.mode in ("RandomList", "RandomM3U8"):
        random_list(args.dir, args.list_path, args.encoding)
    elif args.mode == "DownloadCustom":
        run_download_custom(args.sites_file)
    sys.exit(0)


def year_month_folder_256(dirs: list[str]) -> None:
    """各ディレクトリ配下の全ファイルを更新日時順に256件ずつ年月フォルダーへ移動する。"""
    for dir_str in dirs:
        base = Path(dir_str)
        files = sorted(
            (p for p in base.rglob("*") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        block_count = (len(files) + _BLOCK_SIZE - 1) // _BLOCK_SIZE
        last_year = 0
        last_month = 0
        same_date_count = 0
        for i in range(block_count):
            first = _mtime(files[i * _BLOCK_SIZE])
            if last_year == first.year and last_month == first.month:
                same_date_count += 1
            else:
                same_date_count = 0
                last_year = first.year
                last_month = first.month
            folder = base / str(first.year) / f"{first.year}-{first.month:02d}-{same_date_count:02d}"
            folder.mkdir(parents=True, exist_ok=True)
            block = files[i * _BLOCK_SIZE : (i + 1) * _BLOCK_SIZE]
            for src in block:
                dst = folder / src.name
                if str(src).casefold() != str(dst).casefold():
                    shutil.move(str(src), str(dst))
                print(dst)


def delete_empty_folders(dirs: list[str]) -> None:
    """各ディレクトリ配下の空サブディレクトリを削除する。"""
    for dir_str in dirs:
        base = Path(dir_str)
        subdirs = [p for p in base.rglob("*") if p.is_dir()]
        subdirs.sort(key=str, reverse=True)
        for d in subdirs:
            try:
                d.rmdir()
                print(d)
            except OSError as e:
                # 非空ディレクトリ（ENOTEMPTY）は想定内のため無視し、それ以外は標準エラーへ出力する（C#の二段catch相当）。
                if e.errno != errno.ENOTEMPTY:
                    print(e, file=sys.stderr)


def fix_gvb_by_file_name(gvb_dir: str, data_dir: str) -> None:
    """データフォルダーのファイル名一致で、gvb内の絶対パス行を実在パスへ付け替える。"""
    gvb_base = Path(gvb_dir)
    data_base = Path(data_dir)
    if not (gvb_base.is_dir() and data_base.is_dir()):
        print("Usage: EcoUtilities.exe FixGVBByFileName gvbファイルフォルダ データフォルダ")
        return

    name_to_path: dict[str, str] = {}
    for path in data_base.rglob("*"):
        if path.is_file() and len(path.name) >= 32 + 4:
            name_to_path[path.name] = str(path)

    for gvb_file in gvb_base.rglob("*.gvb"):
        updated = False
        out_lines: list[str] = []
        for line in gvb_file.read_text(encoding=_GVB_ENCODING).splitlines():
            new_path = name_to_path.get(ntpath.basename(line))
            if ntpath.isabs(line) and ".zip\\" not in line and not Path(line).exists() and new_path is not None:
                print(new_path)
                out_lines.append(new_path)
                updated = True
            else:
                out_lines.append(line)
        if updated:
            content = "".join(line + os.linesep for line in out_lines)
            gvb_file.write_text(content, encoding=_GVB_ENCODING, newline="")
            print("更新：    " + str(gvb_file))
        else:
            print("スキップ：" + str(gvb_file))


def dispose_tc_bookmarks(path_str: str) -> None:
    """gvbを接頭辞3文字ごとにまとめ、連番のサブフォルダー階層へ移動する。"""
    base = Path(path_str)
    files = sorted(
        base.rglob("*.gvb"),
        key=lambda p: _prefix(p) + _mtime(p).strftime("%Y%m%d%H%M%S"),
    )
    count = 0
    for marker in files:
        if not marker.stem.casefold().endswith("-m"):
            continue
        prefix = _prefix(marker)
        dest_dir = base / f"{count // 100:02d}" / f"{count // 10:03d}"
        dest_dir.mkdir(parents=True, exist_ok=True)
        for file in files:
            if file.stem.casefold().startswith(prefix.casefold()):
                dst = dest_dir / file.name
                if file.exists() and not dst.exists():
                    shutil.move(str(file), str(dst))
        count += 1


def random_list(dir_str: str, list_path: str, encoding: str) -> None:
    """ディレクトリ配下から最大64件をランダム抽出し、改行区切りで出力する。"""
    files = [str(p) for p in Path(dir_str).rglob("*") if p.is_file()]
    selected = random.sample(files, min(_RANDOM_TAKE, len(files)))
    Path(list_path).write_text(os.linesep.join(selected), encoding=encoding, newline="")


class Site(pydantic.BaseModel):
    """DownloadCustomのサイト設定。C#のDownloadCustom.Site構造体に対応する。"""

    download_folder_path: str
    first_url: str
    base_url: str
    link_patterns: list[str] = pydantic.Field(default_factory=list)
    exclude_patterns: list[str] = pydantic.Field(default_factory=list)
    flat_directory: bool = False


def run_download_custom(sites_file: str) -> None:
    """設定ファイルを読み込み、存在しなければサンプルを出力し、存在すれば全サイトをクロールする。"""
    path = Path(sites_file)
    if not path.exists():
        _write_sample_sites(path)
        print("Sites.xmlを出力しました。")
        return
    sites = load_sites(path)
    with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
        download_custom_sites(sites, client)


def download_custom_sites(sites: list[Site], client: httpx.Client) -> None:
    """与えられたサイト群を順にクロールする。HTTPクライアントは呼び出し側が注入する。"""
    visited: set[str] = set()
    for site in sites:
        print(f"処理開始: {site.first_url} => {site.download_folder_path}")
        link_patterns = [re.compile(p) for p in site.link_patterns]
        exclude_patterns = [re.compile(p) for p in site.exclude_patterns]
        _download_by_pattern(site, client, site.first_url, link_patterns, exclude_patterns, visited)


def _download_by_pattern(
    site: Site,
    client: httpx.Client,
    url: str,
    patterns: list[re.Pattern[str]],
    exclude_patterns: list[re.Pattern[str]],
    visited: set[str],
) -> None:
    """リンクパターンを段階適用し、BaseURL配下のリンクを辿ってダウンロードする。"""
    if url in visited:
        return
    visited.add(url)
    try:
        print("ダウンロード中: " + url)
        response = client.get(url)
        response.raise_for_status()
        data = response.text
        first_pattern = patterns[0]
        second_pattern = patterns[1] if len(patterns) >= 2 else None
        for m in _RE_QUOTED.finditer(data):
            child_url = html.unescape(m.group(2))
            if child_url == "#":
                continue
            download_uri = urllib.parse.urljoin(url, child_url)
            if not download_uri.startswith(site.base_url):
                continue
            if first_pattern.search(download_uri):
                if len(patterns) == 1:
                    _download_data(site, client, download_uri, exclude_patterns, visited)
                else:
                    _download_by_pattern(site, client, download_uri, patterns, exclude_patterns, visited)
            elif second_pattern is not None and second_pattern.search(download_uri):
                if len(patterns) == 2:
                    _download_data(site, client, download_uri, exclude_patterns, visited)
                else:
                    _download_by_pattern(site, client, download_uri, patterns[1:], exclude_patterns, visited)
    except Exception as e:  # noqa: BLE001  # クロール継続のため個別URLの失敗を捕捉する（C#のcatch相当）。
        print("ダウンロードに失敗: " + url)
        print(e)


def _download_data(
    site: Site,
    client: httpx.Client,
    download_uri: str,
    exclude_patterns: list[re.Pattern[str]],
    visited: set[str],
) -> None:
    """単一URLのデータを保存先パスへダウンロードする。"""
    try:
        if download_uri in visited:
            return
        visited.add(download_uri)
        if any(p.search(download_uri) for p in exclude_patterns):
            return
        rel = download_uri[len(site.base_url) :]
        if site.flat_directory:
            for c in '\\/?:*"><|':
                rel = rel.replace(c, "_")
        else:
            for c in '?:*"><|':
                rel = rel.replace(c, "_")
            rel = rel.replace("/", os.sep)
        download_path = Path(site.download_folder_path) / rel
        if not download_path.exists():
            download_path.parent.mkdir(parents=True, exist_ok=True)
            print("データのダウンロード中: " + download_uri)
            response = client.get(download_uri)
            response.raise_for_status()
            download_path.write_bytes(response.content)
    except Exception as e:  # noqa: BLE001  # クロール継続のため個別URLの失敗を捕捉する（C#のcatch相当）。
        print("データのダウンロードに失敗: " + download_uri)
        print(e)


def load_sites(path: Path) -> list[Site]:
    """C#のXmlSerializer互換XMLからサイト設定を読み込む。"""
    root = ET.parse(path).getroot()
    sites: list[Site] = []
    for el in root.findall("Site"):
        sites.append(
            Site(
                download_folder_path=el.findtext("DownloadFolderPath", default=""),
                first_url=el.findtext("FirstURL", default=""),
                base_url=el.findtext("BaseURL", default=""),
                link_patterns=_strings(el, "LinkPatterns"),
                exclude_patterns=_strings(el, "ExcludePatterns"),
                flat_directory=el.findtext("FlatDirectory", default="false").strip().lower() == "true",
            )
        )
    return sites


def _write_sample_sites(path: Path) -> None:
    """C#のサンプルと同じダミー設定をXmlSerializer互換XMLで出力する。"""
    root = ET.Element("ArrayOfSite")
    site = ET.SubElement(root, "Site")
    ET.SubElement(site, "DownloadFolderPath").text = r"C:\example\dummy"
    ET.SubElement(site, "FirstURL").text = "http://dummy.example.com/dummy.jsp"
    ET.SubElement(site, "BaseURL").text = "http://dummy.example.com/"
    link = ET.SubElement(site, "LinkPatterns")
    ET.SubElement(link, "string").text = r"/dummy/content/.*\.jpg"
    exclude = ET.SubElement(site, "ExcludePatterns")
    ET.SubElement(exclude, "string").text = r"\.avi$"
    ET.SubElement(site, "FlatDirectory").text = "false"
    tree = ET.ElementTree(root)
    ET.indent(tree)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _strings(parent: ET.Element, container: str) -> list[str]:
    """配列要素（`<container><string>...</string></container>`）の文字列リストを返す。"""
    node = parent.find(container)
    if node is None:
        return []
    return [child.text or "" for child in node.findall("string")]


def _mtime(path: Path) -> datetime.datetime:
    """ファイルの最終更新時刻をローカル時刻で返す。"""
    return datetime.datetime.fromtimestamp(path.stat().st_mtime)


def _prefix(path: Path) -> str:
    """ファイル名（拡張子込み）の先頭3文字を返す。"""
    return path.name[:3]


if __name__ == "__main__":
    main()
