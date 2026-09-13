"""パス構成要素としての `scratchpad` 判定を共有するヘルパー。

`permissionrequest.py`の自動許可判定と`pretooluse.py`のgit検査除外判定が
同じscratchpad判定を必要とするため、本モジュールへ集約する。
"""

import pathlib


def is_under(target: pathlib.Path, base: pathlib.Path) -> bool:
    """`target` が `base` 配下（`base` 自身は含まない）か判定する。"""
    try:
        rel = target.relative_to(base)
    except ValueError:
        return False
    return bool(rel.parts)


def is_scratchpad_path(target: pathlib.Path) -> bool:
    """パス構成要素として `scratchpad` を含み、かつ `/tmp/` またはホームディレクトリ配下か判定する。

    「パス構成要素として含む」とは `target.parts` 内に `"scratchpad"` が要素として
    現れることを指す。ファイル名の一部（`scratchpad-notes.md` 等）は対象外とする。

    ここでの `/tmp` は scratchpad 判定の対象範囲を限定する境界条件として用いる
    リテラル指定であり、`/tmp` 全許可判定用の別定数（呼び出し側でテスト時に差し替え可能な値）
    とは別概念として意図的に分離する。
    """
    if "scratchpad" not in target.parts:
        return False
    try:
        tmp_root = pathlib.Path("/tmp").resolve(strict=False)
        home_root = pathlib.Path.home().resolve(strict=False)
    except (ValueError, OSError, RuntimeError):
        return False
    return is_under(target, tmp_root) or is_under(target, home_root)
