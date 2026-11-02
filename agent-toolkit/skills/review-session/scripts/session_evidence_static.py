"""証拠抽出で共有する、同一シェル入力の固定値の静的解決。シェルとファイルシステムは実行しない。"""

import itertools
import re
import shlex
import typing
from collections.abc import Sequence

from agent_toolkit._common import shell_quoting

_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
_ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)(\+?=)(.*)$", re.DOTALL)
_BRACE = re.compile(r"(?<!\$)\{([^{}]*)\}")
_RANGE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$")
_OTHER_SUPPLIERS = frozenset({"read", "mapfile", "readarray", "declare", "local", "export", "readonly"})
SUBSTITUTION_MARK = "\ue000"
_LITERAL_CHARACTERS = {"{": "\ue001", "}": "\ue002", "$": "\ue003"}
_QUOTED_MARK = "\ue004"
_RESTORE_CHARACTERS = str.maketrans({value: key for key, value in _LITERAL_CHARACTERS.items()} | {_QUOTED_MARK: None})


class _Expansion(typing.NamedTuple):
    variables: bool
    braces: bool


def _word_expansions(source: str) -> dict[str, _Expansion]:
    """引用とエスケープの状態から各語で可能な展開を求める。"""
    scanner = shell_quoting.QuotingScanner(source)
    masked = list(source)
    while scanner.index < len(source):
        position = scanner.index
        escaped = scanner.escaped
        literal_variable = scanner.quote == "'" or escaped
        if scanner.consume_quoted():
            if source[position] in "{}" or (source[position] == "$" and literal_variable):
                masked[position] = _LITERAL_CHARACTERS[source[position]]
            if escaped:
                masked[position] += _QUOTED_MARK
        elif source[position] in "\"'":
            masked[position] += _QUOTED_MARK
            scanner.enter_quote(source[position])
        else:
            scanner.index += 1
    lexer = shlex.shlex("".join(masked), posix=True, punctuation_chars=";&|()<>")
    lexer.whitespace_split = True
    try:
        words = set(lexer)
    except ValueError:
        return {}
    words.update(match[3] for word in tuple(words) if (match := _ASSIGNMENT.fullmatch(word)) is not None)
    expansions: dict[str, _Expansion] = {}
    for word in words:
        key = word.translate(_RESTORE_CHARACTERS)
        previous = expansions.get(key, _Expansion(variables=True, braces=True))
        # 語分割後は元の位置を確定できないため、引用条件が異なる同一語は共通部分だけを解決する。
        # 引用と非引用の混在を部分ごとに再現できない展開は、元の語のまま保持する。
        expansions[key] = _Expansion(
            previous.variables and "$" in word and _LITERAL_CHARACTERS["$"] not in word,
            previous.braces and "{" in word and _QUOTED_MARK not in word,
        )
    return expansions


class StaticValues:
    """代入とfor見出しだけから供給された値の和集合を保持する。"""

    def __init__(self, commands: Sequence[tuple[str, ...]], source: str) -> None:
        self._expansions = _word_expansions(source)
        self._supplies: dict[str, list[str]] = {}
        self._invalid: set[str] = set()
        first_supply: dict[str, int] = {}
        first_reference: dict[str, int] = {}
        position = 0
        for words in commands:
            supplier = words[0] if words else ""
            assignment_prefix = True
            for index, word in enumerate(words):
                position += 1
                if self._expansions.get(word, _Expansion(False, False)).variables:
                    for match in _VARIABLE.finditer(word):
                        first_reference.setdefault(match[1] or match[2], position)
                assignment = _ASSIGNMENT.fullmatch(word)
                if assignment and assignment_prefix:
                    name, operator, value = assignment.groups()
                    if operator == "+=":
                        self._invalid.add(name)
                    self._supplies.setdefault(name, []).append(value)
                    first_supply.setdefault(name, position)
                else:
                    assignment_prefix = False
                if supplier in _OTHER_SUPPLIERS and index:
                    name = word.split("=", 1)[0].rstrip("+")
                    if re.fullmatch(r"[A-Za-z_]\w*", name):
                        self._invalid.add(name)
            if len(words) >= 3 and supplier == "for" and words[2] == "in":
                name = words[1]
                self._supplies.setdefault(name, []).extend(words[3:])
                first_supply.setdefault(name, position - len(words) + 2)
        for name, supply in first_supply.items():
            if supply >= first_reference.get(name, position + 1):
                self._invalid.add(name)
        # 関数本体の実行順は単純コマンドの並びから決められない。
        if re.search(r"(?:\bfunction\s+\w+|\b\w+\s*\(\s*\))\s*\{", source):
            self._invalid.update(self._supplies)

    def expand(
        self, word: str, visiting: frozenset[str] = frozenset(), *, expansion: _Expansion | None = None
    ) -> tuple[str, ...]:
        """解決できる変数と固定波括弧を展開し、未知の語は元の形で返す。"""
        if expansion is None:
            expansion = self._expansions.get(word, _Expansion(False, False))
        match = _VARIABLE.search(word) if expansion.variables else None
        if match is not None:
            name = match[1] or match[2]
            if name in visiting or name in self._invalid or name not in self._supplies:
                return (word,)
            values = [value for supplied in self._supplies[name] for value in self.expand(supplied, visiting | {name})]
            if not values or any("$" in value or SUBSTITUTION_MARK in value or "`" in value for value in values):
                return (word,)
            return tuple(
                dict.fromkeys(
                    result
                    for value in values
                    for result in self.expand(
                        word[: match.start()] + value + word[match.end() :], visiting, expansion=expansion
                    )
                )
            )
        if "$" in word or SUBSTITUTION_MARK in word or "`" in word:
            return (word,)
        # 語分割後の文字種では引用を判定できない。元入力で展開可能だった語だけを列挙する。
        if not expansion.braces:
            return (word,)
        match = _BRACE.search(word)
        if match is None:
            return (word,)
        body = match[1]
        if "," in body:
            choices = body.split(",")
        elif (bounds := _RANGE.fullmatch(body)) is not None:
            start, end = int(bounds[1]), int(bounds[2])
            step = abs(int(bounds[3] or 1)) or 1
            step = step if start <= end else -step
            sequence = range(start, end + (1 if step > 0 else -1), step)
            width = max(len(bounds[1].lstrip("-")), len(bounds[2].lstrip("-")))
            padded = bounds[1].lstrip("-").startswith("0") or bounds[2].lstrip("-").startswith("0")
            choices = [str(value).zfill(width + int(value < 0)) if padded else str(value) for value in sequence]
        else:
            return (word,)
        return tuple(
            result
            for choice in choices
            for result in self.expand(word[: match.start()] + choice + word[match.end() :], expansion=expansion)
        )

    def variants(self, words: Sequence[str]) -> list[tuple[str, ...]]:
        """語の候補ごとに引数境界を維持したコマンドを返す。"""
        return list(itertools.product(*(self.expand(word) for word in words)))
