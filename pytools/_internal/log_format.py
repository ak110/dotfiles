"""post-apply系モジュールで共有するログフォーマットヘルパー。"""

from pathlib import Path


def format_status(target: str, state: str) -> str:
    """`    <target>: <state>` 形式の詳細行を返す。

    post-applyの`logging.basicConfig`が行頭に "  " を付けるため、
    本関数は追加で4スペースを持たせ、最終的に6スペースインデントの出力になる。
    """
    return f"    {target}: {state}"


def home_short(path: Path, *, home: Path | None = None) -> str:
    """`Path.home()` 配下なら `~/...` に短縮する。配下外はそのまま `str(path)`。

    `Path.home()` 自身は `~` を返す。シンボリックリンク解決は行わない
    （見た目を整えるだけの用途のため、resolveでユーザーの意図しないパスへ
    展開されないよう素のパスで判定する）。
    """
    home = home or Path.home()
    if path == home:
        return "~"
    try:
        return "~/" + path.relative_to(home).as_posix()
    except ValueError:
        return str(path)
