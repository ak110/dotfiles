r"""所有者だけが使うディレクトリの作成。

POSIXでは0700で作成する。Windowsでは親から継承するACLに任せる。

Windowsの`os.mkdir(mode=0o700)`と、0o700を固定で渡す`tempfile.mkdtemp`は、
`D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;FA;;;OW)`の保護付きDACLを付ける。
OWNER RIGHTS（OW）は対象の所有者を指し、昇格したプロセスが作ったファイルの所有者は`BUILTIN\Administrators`になる。
非昇格のtokenでは`BUILTIN\Administrators`が拒否専用のため、同じユーザーの非昇格のプロセスはそのファイルを読めない
（2026-11-19、SSHで昇格して実行した`update-dotfiles`が書き直した管理サービスの世代索引を、非昇格の旧世代が読めずに停止した）。
状態を置く`%LOCALAPPDATA%`などのユーザーprofile配下は、継承するACLでユーザー・SYSTEM・Administratorsに限られるため、
Windowsでは継承に任せても所有者以外へ公開しない。
呼び出し元が指定した親（`pytools/repack_archive.py`の作業ディレクトリなど）では、親のACLの範囲に公開する。
"""

from __future__ import annotations

import contextlib
import errno
import os
import pathlib
import secrets
import shutil
import stat
import tempfile
from collections.abc import Generator

MODE = 0o777 if os.name == "nt" else 0o700
"""`Path.mkdir`・`os.mkdir`へ渡すmode。Windowsは0o700以外のmodeを無視して継承するACLで作成する。"""


def make_temporary(parent: pathlib.Path | str, prefix: str) -> pathlib.Path:
    """`parent`直下へ`prefix`で始まる一意な名前のディレクトリを作成し、絶対パスを返す。"""
    if os.name != "nt":
        return pathlib.Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    directory = pathlib.Path(os.path.abspath(parent))
    for _ in range(tempfile.TMP_MAX):
        path = directory / f"{prefix}{secrets.token_hex(4)}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return path
    raise FileExistsError(errno.EEXIST, "一意な名前のディレクトリを作成できません", str(directory))


@contextlib.contextmanager
def temporary(parent: pathlib.Path | str, prefix: str) -> Generator[pathlib.Path]:
    """`make_temporary`で作成したディレクトリを、読取専用属性の付いた中身ごと終了時に削除する。"""
    if os.name != "nt":
        with tempfile.TemporaryDirectory(prefix=prefix, dir=parent) as name:
            yield pathlib.Path(name)
        return
    path = make_temporary(parent, prefix)
    try:
        yield path
    finally:
        shutil.rmtree(path, onexc=_remove_readonly)


def _remove_readonly(function, path: str, error: BaseException) -> None:
    # `tempfile.TemporaryDirectory`と同じく、本体が先に削除した項目は削除済みとして扱う。
    if isinstance(error, FileNotFoundError):
        return
    if not isinstance(error, PermissionError):
        raise error
    os.chmod(path, stat.S_IWRITE)
    function(path)
