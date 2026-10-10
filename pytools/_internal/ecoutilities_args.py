"""EcoUtilitiesの引数を、ファイル処理やサイト設定モデルの読み込み前に解釈する。"""

import argparse

from pytools._internal.cli import enable_completion


def parse_args() -> argparse.Namespace:
    """既存のサブコマンドと引数を解釈する。"""
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
    p_rl = sub.add_parser("RandomList", help="ランダム抽出してリストを出力する（省略時はcp932）。")
    p_rl.add_argument("dir", help="対象フォルダー。")
    p_rl.add_argument("list_path", help="出力先リストファイル。")
    p_rl.add_argument("--encoding", default="cp932", help="出力エンコーディング（省略時: cp932）。")
    p_rm = sub.add_parser("RandomM3U8", help="ランダム抽出してm3u8を出力する（省略時はBOM付きUTF-8）。")
    p_rm.add_argument("dir", help="対象フォルダー。")
    p_rm.add_argument("list_path", help="出力先リストファイル。")
    p_rm.add_argument("--encoding", default="utf-8-sig", help="出力エンコーディング（省略時: utf-8-sig）。")
    p_dc = sub.add_parser("DownloadCustom", help="設定ファイルに従いサイトをクロールしてダウンロードする。")
    p_dc.add_argument("sites_file", nargs="?", default="Sites.xml", help="サイト設定XML（省略時: Sites.xml）。")
    enable_completion(parser)
    return parser.parse_args()
