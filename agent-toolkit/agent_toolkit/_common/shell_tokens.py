"""シェルの語の共通表現と、語の列から引数列を得る処理、それを使うコマンド起動の判定。

`split_words`は1つの単純コマンドの文字列を、引用除去後の値に引用・展開・リダイレクトの構文情報を添えた
`ShellWord`の列へ分ける。区間分割（`shell_segments`）と各消費側はこの表現を共有し、
引用を外した後の文字列から演算子を推測し直さない。
位置引数を数える判定と引数列全体を比べる判定は、`_atk`と`_hooks`の双方から本モジュールを使う。
"""

from __future__ import annotations

import pathlib
import re
import shlex
from collections.abc import Sequence

_REDIRECTION_OPERATOR_PATTERN = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|>|<<<|<<-|<<|<>|<&|<)")
"""リダイレクト演算子で始まるトークンの先頭部分。

先頭のファイル記述子番号（`2>`）と、標準出力・標準エラーの同時指定（`&>`）を含む。
"""

_DOUBLE_QUOTE_ESCAPABLE = frozenset('$`"\\\n')


class ShellWord(str):
    """引用を除いた語の値に、元の入力上の構文情報を添えた文字列。

    値は引用除去後の語であり、文字列として比べる既存の消費側はそのまま使える。
    構文情報は次のとおりである。

    - `quoted`: 引用符かエスケープを含む。
    - `expansion`: 単一引用とエスケープの外側に`$`かバッククォートを含み、実行しないと値が決まらない。
    - `redirection`: 語の先頭が引用の外側のリダイレクト演算子である場合に、演算子だけの語なら`"separate"`、
      演算子へ対象が密着した語なら`"attached"`、それ以外は`None`。
    - `unquoted`: 引用とエスケープの外側にあった文字だけを連結した文字列。
    - `raw`: 引用とエスケープを残した元の入力上の語。
    - `start`・`end`: 元の入力上の語の範囲。

    構文情報を持たない文字列（記録から得たargvなど）を受け取る関数は、従来どおり値だけで判定する。
    """

    quoted: bool
    expansion: bool
    redirection: str | None
    unquoted: str
    raw: str
    start: int
    end: int

    def __new__(
        cls,
        value: str,
        *,
        quoted: bool = False,
        expansion: bool = False,
        redirection: str | None = None,
        unquoted: str | None = None,
        raw: str | None = None,
        start: int = -1,
        end: int = -1,
    ) -> ShellWord:  # noqa: PYI034
        word = super().__new__(cls, value)
        word.quoted = quoted
        word.expansion = expansion
        word.redirection = redirection
        word.unquoted = value if unquoted is None else unquoted
        word.raw = value if raw is None else raw
        word.start = start
        word.end = end
        return word


def split_words(text: str) -> list[ShellWord]:
    """単純コマンドの文字列を、空白区切りの`ShellWord`の列へ分ける。

    語の境界と値はBashの引用除去に合わせる（二重引用の内側でドル記号とバッククォートのエスケープも外す点だけ`shlex.split`と異なる）。
    閉じない引用と末尾のエスケープは`ValueError`とする。
    `;`・`|`などの区切り演算子は区間分割が担うため、本関数は語の一部として扱う。
    """
    words: list[ShellWord] = []
    index = 0
    length = len(text)
    while index < length:
        if text[index] in " \t\r\n":
            index += 1
            continue
        start = index
        value: list[str] = []
        unquoted: list[str] = []
        quoted = False
        expansion = False
        while index < length and text[index] not in " \t\r\n":
            char = text[index]
            if char == "\\":
                if index + 1 >= length:
                    raise ValueError("No escaped character")
                quoted = True
                value.append(text[index + 1])
                index += 2
                continue
            if char == "'":
                end = text.find("'", index + 1)
                if end < 0:
                    raise ValueError("No closing quotation")
                quoted = True
                value.append(text[index + 1 : end])
                index = end + 1
                continue
            if char == '"':
                quoted = True
                index += 1
                while True:
                    if index >= length:
                        raise ValueError("No closing quotation")
                    inner = text[index]
                    if inner == '"':
                        index += 1
                        break
                    if inner == "\\" and index + 1 < length and text[index + 1] in _DOUBLE_QUOTE_ESCAPABLE:
                        value.append(text[index + 1])
                        index += 2
                        continue
                    if inner in "$`":
                        expansion = True
                    value.append(inner)
                    index += 1
                continue
            if char in "$`":
                expansion = True
            value.append(char)
            unquoted.append(char)
            index += 1
        literal = "".join(unquoted)
        raw = text[start:index]
        redirection = None
        match = _REDIRECTION_OPERATOR_PATTERN.match(raw)
        if match is not None and raw[: match.end()] == literal[: match.end()]:
            redirection = "separate" if match.end() == len(raw) else "attached"
        words.append(
            ShellWord(
                "".join(value),
                quoted=quoted,
                expansion=expansion,
                redirection=redirection,
                unquoted=literal,
                raw=raw,
                start=start,
                end=index,
            )
        )
    return words


ATK_LAUNCHER_NAMES = ("atk", "atk.cmd", "atk.py")
ATK_SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


def atk_launcher_name(*, windows: bool) -> str:
    """ホスト向けに案内するランチャー名を返す。"""
    return ATK_LAUNCHER_NAMES[1 if windows else 0]


def is_atk_command(tokens: Sequence[str]) -> bool:
    """実行位置のトークンが配布するatkランチャーかを返す。"""
    return bool(tokens) and pathlib.PurePosixPath(tokens[0].replace("\\", "/")).name in ATK_LAUNCHER_NAMES


def join_words(tokens: Sequence[str]) -> str:
    """語の列を、同じ構文として再び解析できる1つのコマンド文字列へ戻す。

    `ShellWord`は元の入力上の語をそのまま使い、リダイレクトや引用の区別を保つ。
    構文情報を持たない文字列は`shlex.quote`で引用する。
    """
    return " ".join(token.raw if isinstance(token, ShellWord) else shlex.quote(token) for token in tokens)


def redirection_kind(token: str) -> str | None:
    """語がリダイレクトであれば`"separate"`か`"attached"`、それ以外は`None`を返す。

    `ShellWord`は分割時に引用の外側の演算子だけを記録した値を使い、引用した`'>'`をデータとして扱う。
    構文情報を持たない文字列は、値の先頭の演算子から判定する。
    """
    if isinstance(token, ShellWord):
        return token.redirection
    match = _REDIRECTION_OPERATOR_PATTERN.match(token)
    if match is None:
        return None
    return "separate" if match.end() == len(token) else "attached"


def strip_redirections(tokens: Sequence[str]) -> tuple[str, ...]:
    """トークン列からリダイレクトの演算子と対象を除いた引数列を返す。

    演算子に対象を密着させたトークン（`2>/dev/null`・`2>&1`）はそれだけを除き、
    演算子だけのトークン（`>`・`2>`・`<<<`）は直後の対象の1トークンと合わせて除く。
    判定は`redirection_kind`に従い、`split_words`が返した引用済みの`'>'`は引数として残す。
    """
    arguments: list[str] = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        kind = redirection_kind(token)
        if kind is None:
            arguments.append(token)
            continue
        skip_target = kind == "separate"
    return tuple(arguments)


def is_agents_wait_command(tokens: Sequence[str]) -> bool:
    """トークン列が`atk agents wait`の起動であるかを返す。"""
    return is_atk_command(tokens) and tuple(tokens[1:3]) == ("agents", "wait")


def is_agents_exit_session_command(tokens: Sequence[str]) -> bool:
    """トークン列が引数を伴わない`atk agents-exit-session`の起動であるかを返す。

    リダイレクトは`atk`へ渡らないため除いてから比べる。位置引数やオプションを伴う起動は認めない。
    """
    arguments = strip_redirections(tokens)
    return len(arguments) == 2 and is_atk_command(arguments) and arguments[1] == "agents-exit-session"
