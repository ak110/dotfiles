"""アクセストークンの読み書き。"""

import os
import pathlib
import re
import secrets

from pytools._internal import claude_common

# `secrets.token_urlsafe(32)`が返す43文字のURL-safe base64文字列に一致する形式。
# パディング無しで`A-Za-z0-9-_`の組み合わせとなる。
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")


def default_token_path() -> pathlib.Path:
    r"""既定のトークン保存先（`%LOCALAPPDATA%\\dotfiles\\media-remote\\token.txt`）。

    `LOCALAPPDATA`未設定時は`~/AppData/Local`へフォールバックする。
    """
    local = os.environ.get("LOCALAPPDATA")
    base = pathlib.Path(local) if local else pathlib.Path.home() / "AppData" / "Local"
    return base / "dotfiles" / "media-remote" / "token.txt"


def load_or_create_token(path: pathlib.Path) -> str:
    """`path`から有効なトークン文字列を読み込む。

    既存値が無いか不正形式の場合は`secrets.token_urlsafe(32)`で新規生成し
    原子的に保存して返す。トークンはアクセス認証用途のためCSPRNGで生成する。
    """
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing and _TOKEN_PATTERN.fullmatch(existing):
        return existing
    token = secrets.token_urlsafe(32)
    claude_common.atomic_write_text(path, token + "\n", tag="media-remote")
    return token
