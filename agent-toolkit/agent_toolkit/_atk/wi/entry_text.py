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
        instance = super().__new__(cls, text)
        instance.parsed = frontmatter.parse_frontmatter(text)
        return instance


def parse_frontmatter(text: str) -> ParsedFrontmatter:
    return text.parsed if isinstance(text, EntryText) else frontmatter.parse_frontmatter(text)


class EntryTexts:
    def __init__(self) -> None:
        self._texts: dict[pathlib.Path, EntryText] = {}

    def read(self, path: pathlib.Path) -> EntryText:
        text = path.read_text(encoding="utf-8")
        previous = self._texts.get(path)
        if previous is not None and previous == text:
            return previous
        current = EntryText(text)
        self._texts[path] = current
        return current
