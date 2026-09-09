"""工程境界で全文読解を要求する文書の絶対パスを解決する。"""

import os
import pathlib
from typing import Any

DOCUMENT_NAME = "judgment-details"
DOCUMENT_PARTS = ("skills", "review-standards", "references", "judgment-details.md")


def plugin_root() -> pathlib.Path:
    """稼働中のフックが読み込まれたplugin rootを返す。"""
    return pathlib.Path(__file__).resolve().parents[2]


# 公開関数と注入用キーワード引数は、計画で同じ名前を持つ。
# pylint: disable=redefined-outer-name
def document_path(*, plugin_root: str | None = None, path_module: Any = os.path) -> str:
    """対象文書のplugin root配下の絶対パスを返す。"""
    root = str(globals()["plugin_root"]()) if plugin_root is None else plugin_root
    return path_module.join(root, *DOCUMENT_PARTS)


# pylint: disable=redefined-outer-name
def matches_document(file_path: str, *, plugin_root: str | None = None, path_module: Any = os.path) -> bool:
    """対象が指定したplugin root配下の全文読解対象と一致するか判定する。"""
    try:
        expected = document_path(plugin_root=plugin_root, path_module=path_module)

        def normalize(value):
            return path_module.normcase(path_module.normpath(path_module.expanduser(value)))

        return normalize(file_path) == normalize(expected)
    except (OSError, ValueError):
        return False
