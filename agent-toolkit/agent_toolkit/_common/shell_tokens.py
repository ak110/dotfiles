"""シェルのトークン列から引数列を得る処理と、それを使うコマンド起動の判定。

`shlex.split`の結果やフックの区間トークン列はリダイレクトの演算子と対象を含む。
位置引数を数える判定と引数列全体を比べる判定は、`_atk`と`_hooks`の双方から本モジュールを使う。
"""

import pathlib
import re
from collections.abc import Sequence

_REDIRECTION_OPERATOR_PATTERN = re.compile(r"^(?:\d+|&)?(?:>>|>\||>&|>|<<<|<<-|<<|<>|<&|<)")
"""リダイレクト演算子で始まるトークンの先頭部分。

先頭のファイル記述子番号（`2>`）と、標準出力・標準エラーの同時指定（`&>`）を含む。
"""


def strip_redirections(tokens: Sequence[str]) -> tuple[str, ...]:
    """トークン列からリダイレクトの演算子と対象を除いた引数列を返す。

    演算子に対象を密着させたトークン（`2>/dev/null`・`2>&1`）はそれだけを除き、
    演算子だけのトークン（`>`・`2>`・`<<<`）は直後の対象の1トークンと合わせて除く。
    `shlex.split`は引用の有無を保持しないため、引用した`'>'`も演算子として除く。
    """
    arguments: list[str] = []
    skip_target = False
    for token in tokens:
        if skip_target:
            skip_target = False
            continue
        match = _REDIRECTION_OPERATOR_PATTERN.match(token)
        if match is None:
            arguments.append(token)
            continue
        skip_target = match.end() == len(token)
    return tuple(arguments)


def is_agents_wait_command(tokens: Sequence[str]) -> bool:
    """トークン列が`atk agents wait`の起動であるかを返す。"""
    executable = pathlib.PurePath(tokens[0].replace("\\", "/")).name if tokens else ""
    return executable in {"atk", "atk.py"} and tuple(tokens[1:3]) == ("agents", "wait")


def is_agents_exit_session_command(tokens: Sequence[str]) -> bool:
    """トークン列が引数を伴わない`atk agents-exit-session`の起動であるかを返す。

    リダイレクトは`atk`へ渡らないため除いてから比べる。位置引数やオプションを伴う起動は認めない。
    """
    arguments = strip_redirections(tokens)
    return (
        len(arguments) == 2
        and pathlib.PurePath(arguments[0]).name in {"atk", "atk.py"}
        and arguments[1] == "agents-exit-session"
    )
