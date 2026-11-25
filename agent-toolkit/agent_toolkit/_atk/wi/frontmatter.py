import pathlib
import typing

from agent_toolkit._common import frontmatter
from agent_toolkit._common.frontmatter import normalize_newlines, serialize_frontmatter

__all__ = [
    "decode_entry_text",
    "normalize_newlines",
    "parse_frontmatter",
    "serialize_frontmatter",
    "write_entry_text",
]

_TYPED_KEYS = frozenset({"queue_schedule", "important"})


def parse_frontmatter(text: str) -> tuple[dict[str, typing.Any], str] | None:
    return frontmatter.parse_frontmatter(text, typed_keys=_TYPED_KEYS)


def decode_entry_text(data: bytes) -> str:
    """キュー項目のbytesをUTF-8でデコードし、改行をLFへ正規化して返す。

    読み取り時にテキストIOのユニバーサル改行処理を経ないため、Windowsで保存したCRLFの本文が
    frontmatterの区切りの一致確認と読み直した本文との一致判定に失敗する。デコードの時点で正規化して両者をそろえる。
    追記で保存bytesをそのまま保つ場合は、保存する内容の組み立てへ本関数を使わない。
    """
    return normalize_newlines(data.decode("utf-8"))


def write_entry_text(path: pathlib.Path, content: str) -> None:
    """キュー項目の本文をLF改行のUTF-8で保存する。

    テキストモードは指定が無ければプラットフォームの改行へ変換するため、Windowsで保存した本文がCRLFとなり、
    bytesで読み直す処理がfrontmatterを解析できなくなる。本文をLFへ正規化したうえでテキストモードを
    経ずに符号化して書き込み、保存形式を実行環境から独立させる。
    """
    path.write_bytes(normalize_newlines(content).encode("utf-8"))
