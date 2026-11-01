"""証拠抽出で共有する、同一シェル入力の固定値の静的解決。シェルとファイルシステムは実行しない。"""

import itertools
import re
from collections.abc import Sequence

_VARIABLE = re.compile(r"\$(?:\{([A-Za-z_]\w*)\}|([A-Za-z_]\w*))")
_ASSIGNMENT = re.compile(r"^([A-Za-z_]\w*)(\+?=)(.*)$", re.DOTALL)
_BRACE = re.compile(r"(?<!\$)\{([^{}]*)\}")
_RANGE = re.compile(r"(-?\d+)\.\.(-?\d+)(?:\.\.(-?\d+))?$")
_OTHER_SUPPLIERS = frozenset({"read", "mapfile", "readarray", "declare", "local", "export", "readonly"})
SUBSTITUTION_MARK = "\ue000"


class StaticValues:
    """代入とfor見出しだけから供給された値の和集合を保持する。"""

    def __init__(self, commands: Sequence[tuple[str, ...]], source: str) -> None:
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

    def expand(self, word: str, visiting: frozenset[str] = frozenset()) -> tuple[str, ...]:
        """解決できる変数と固定波括弧を展開し、未知の語は元の形で返す。"""
        match = _VARIABLE.search(word)
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
                    for result in self.expand(word[: match.start()] + value + word[match.end() :], visiting)
                )
            )
        if "$" in word or SUBSTITUTION_MARK in word or "`" in word:
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
            result for choice in choices for result in self.expand(word[: match.start()] + choice + word[match.end() :])
        )

    def variants(self, words: Sequence[str]) -> list[tuple[str, ...]]:
        """語の候補ごとに引数境界を維持したコマンドを返す。"""
        return list(itertools.product(*(self.expand(word) for word in words)))
