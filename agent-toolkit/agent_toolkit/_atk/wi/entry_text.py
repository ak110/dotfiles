"""取得済みWI本文と、その本文に対応するfrontmatter解析結果。"""

import pathlib
import typing

from agent_toolkit._atk.wi import frontmatter

type ParsedFrontmatter = tuple[dict[str, typing.Any], str] | None


class EntryText(str):
    """文字列の消費契約を保ち、取得時の解析結果を同じ本文へ結び付ける。

    文字列の加工は通常のstrを返すため、加工後の本文は新しい解析の対象となる。
    """

    parsed: ParsedFrontmatter

    def __new__(cls, text: str) -> EntryText:
        """本文を1回解析して保持する。"""
        instance = super().__new__(cls, text)
        instance.parsed = frontmatter.parse_frontmatter(text)
        return instance


def parse_frontmatter(text: str) -> ParsedFrontmatter:
    """取得済み本文の解析結果を返し、通常の文字列は共通パーサーへ渡す。"""
    return text.parsed if isinstance(text, EntryText) else frontmatter.parse_frontmatter(text)


class EntryTexts:
    """1操作で取得した本文を共有する。各読取時に内容の一致を確かめる。"""

    def __init__(self) -> None:
        """操作に閉じた取得結果を初期化する。"""
        self._texts: dict[pathlib.Path, EntryText] = {}

    def read(self, path: pathlib.Path) -> EntryText:
        """最新の本文を読み、同じ内容の取得済み本文を再利用する。"""
        text = path.read_text(encoding="utf-8")
        previous = self._texts.get(path)
        if previous is not None and previous == text:
            return previous
        current = EntryText(text)
        self._texts[path] = current
        return current
