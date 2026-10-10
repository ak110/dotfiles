"""Bashコマンドの`bash -c`・`sh -c`を含む呼び出しの抽出と、各呼び出しの出力の接続先の判定。"""

from __future__ import annotations

import dataclasses
import enum
import re
import shlex
from collections.abc import Sequence

from agent_toolkit._common.heredocs import heredoc_bodies, mask_heredoc_bodies
from agent_toolkit._common.shell_segments import (
    ENV_ASSIGN_PATTERN,
    PIPE_SEPARATORS,
    ExecutionSegment,
    resolve_execution_segment,
    shell_c_argument,
)


class _BashOutput(enum.Enum):
    PIPE = "pipe"
    UNKNOWN = "unknown"


type _OutputTarget = str | int | _BashOutput


@dataclasses.dataclass(frozen=True)
class BashInvocation:
    """静的に得た外側の引数と、その実行へ作用する出力接続を保持する。

    展開を含む引数は`arguments_known`を偽にし、値を実行して求めない。
    出力の整数は囲むシェルから継承するファイル記述子を表す。
    助言と実行記録が共有する`ExecutionSegment`の既存解析は変更しない。
    """

    segment: ExecutionSegment
    arguments_known: bool
    outputs: tuple[_OutputTarget, _OutputTarget] = (1, 2)
    background: bool = False
    captured: bool = False
    static_tokens: tuple[str, ...] = ()
    static_outputs: tuple[_OutputTarget, _OutputTarget] = (1, 2)

    @property
    def output_pipe(self) -> bool:
        """標準出力または標準エラーが後段のパイプへ渡るかを返す。"""
        return _BashOutput.PIPE in self.outputs

    @property
    def output_redirected(self) -> bool:
        """標準出力か標準エラーの最終的な接続先が、呼び出し元から引き継いだ1と2からリダイレクトで変わったかを返す。

        接続先がファイル（`/dev/null`を含む）、別のファイル記述子、未確定のパスのいずれでも真とする。
        """
        return self.outputs != (1, 2)


@dataclasses.dataclass(frozen=True)
class _BashToken:
    value: str
    operator: bool = False
    known: bool = True
    nested: tuple[str, ...] = ()
    assignment: bool = False
    static_value: str = ""
    reserved: bool = False


_BASH_REDIRECTION = re.compile(r"[0-9]*(?:&>>|&>|<<-|<<<|>>|<<|<>|<&|>&|>\||>|<)")


_BASH_OPERATORS = ("&&", "||", "|&", ";;&", ";;", ";&", ";", "&", "|", "(", ")", "{", "}", "\n")


_UNKNOWN_BASH_WORD = "\x00"
STATIC_UNKNOWN = "\ue000"


def extract_bash_invocations(command: str) -> list[BashInvocation]:
    """引用・置換・出力接続を保持し、判定できるBashの実行位置を返す。

    heredoc本文とリテラル引数を実行位置にせず、置換の内側は別の呼び出しとして解析する。
    区切り語を引用しないheredocの本文にある置換は本関数では解析せず、`heredoc_command_substitutions`が返す。
    PreToolUseはその置換を含むコマンドを実行前に遮断するため、本文の置換の内側を個別の判定へ渡さない。
    変数や別ファイルに隠れた起動、未対応の複合構文、閉じない構文から実行位置を推定しない。
    """
    try:
        tokens = _bash_tokens(mask_heredoc_bodies(command))
        invocations, end = _bash_command_list(tokens, 0)
    except ValueError:
        return []
    return invocations if end == len(tokens) else []


def _bash_tokens(command: str) -> list[_BashToken]:
    """演算子とシェル語を区別し、置換内の語を外側のトークンへ混ぜない。"""
    tokens: list[_BashToken] = []
    index = 0
    while index < len(command):
        if command[index] in " \t\r":
            index += 1
            continue
        if command.startswith("\\\n", index):
            index += 2
            continue
        if command[index] == "#":
            end = command.find("\n", index)
            index = len(command) if end < 0 else end
            continue
        if command.startswith("((", index):
            _, index = _bash_substitution(command, index)
            tokens.append(_BashToken(_UNKNOWN_BASH_WORD, known=False))
            continue
        # プロセス置換はリダイレクト演算子とは別のシェル語である。
        redirect = None if command[index : index + 2] in {"<(", ">("} else _BASH_REDIRECTION.match(command, index)
        operator = next((value for value in _BASH_OPERATORS if command.startswith(value, index)), None)
        if redirect is not None or operator is not None:
            value = redirect.group() if redirect is not None else operator
            assert value is not None
            tokens.append(_BashToken(value, operator=True))
            index += len(value)
            continue
        word, index = _bash_word(command, index)
        tokens.append(word)
    return tokens


def _bash_word(command: str, start: int) -> tuple[_BashToken, int]:
    """1つの語の引用と展開を区別し、静的な値と置換の本文を返す。"""
    pieces: list[str] = []
    static_pieces: list[str] = []
    nested: list[str] = []
    quote: str | None = None
    known = True
    index = start
    while index < len(command):
        char = command[index]
        if char == "\\" and quote != "'":
            if index + 1 >= len(command):
                raise ValueError("閉じないエスケープ")
            if command[index + 1] != "\n":
                pieces.append(command[index : index + 2])
                static_pieces.append(command[index : index + 2])
            index += 2
            continue
        if char == quote:
            quote = None
            pieces.append(char)
            static_pieces.append(char)
            index += 1
            continue
        if quote != "'" and (
            command.startswith(("$(", "${"), index)
            or char == "`"
            or (quote is None and command[index : index + 2] in {"<(", ">("})
        ):
            body, index = _bash_substitution(command, index)
            if body is not None:
                nested.append(body)
            pieces.append(_UNKNOWN_BASH_WORD)
            static_pieces.append(STATIC_UNKNOWN)
            known = False
            continue
        if (
            quote != "'"
            and char == "$"
            and index + 1 < len(command)
            and (command[index + 1].isalnum() or command[index + 1] in "_@*#?!-$")
        ):
            end = index + 2
            if command[index + 1].isalpha() or command[index + 1] == "_":
                while end < len(command) and (command[end].isalnum() or command[end] == "_"):
                    end += 1
            pieces.append(command[index:end])
            static_pieces.append(STATIC_UNKNOWN)
            known = False
            index = end
            continue
        if quote is None and char in {"'", '"'}:
            quote = char
        elif quote is None and (char.isspace() or char in ";&|()<>"):
            break
        elif (
            quote != "'"
            and char == "$"
            and index + 1 < len(command)
            and (command[index + 1].isalnum() or command[index + 1] in "{_@*#?!-$")
            or quote is None
            and (char in "*?[" or (char == "~" and index == start))
        ):
            known = False
        pieces.append(char)
        static_pieces.append(STATIC_UNKNOWN if quote is None and (char in "*?[" or (char == "~" and index == start)) else char)
        index += 1
    if quote is not None or not pieces:
        raise ValueError("閉じない引用または未対応の語")
    values = shlex.split("".join(pieces), posix=True)
    if len(values) != 1:
        raise ValueError("シェル語を確定できない")
    value = values[0]
    if not known:
        assignment = ENV_ASSIGN_PATTERN.match(value)
        value = (assignment.group() if assignment else "") + _UNKNOWN_BASH_WORD
    return _BashToken(
        value,
        known=known,
        nested=tuple(nested),
        assignment=ENV_ASSIGN_PATTERN.match("".join(pieces)) is not None,
        static_value=shlex.split("".join(static_pieces), posix=True)[0],
        reserved="".join(pieces) == value,
    ), index


def _bash_substitution(command: str, start: int) -> tuple[str | None, int]:
    """置換の対応する終端を、内側の引用と入れ子を区別して求める。"""
    backtick = command[start] == "`"
    parameter = command.startswith("${", start)
    arithmetic = command.startswith("((", start)
    opening, closing = ("{", "}") if parameter else ("(", ")")
    cursor = start + (1 if backtick else 2)
    body_start = cursor
    depth = 2 if arithmetic else 1
    quote: str | None = None
    while cursor < len(command):
        char = command[cursor]
        if char == "\\" and quote != "'":
            cursor += 2
            continue
        if backtick and char == "`":
            return command[body_start:cursor], cursor + 1
        if char == quote:
            quote = None
        elif quote is None and char in {"'", '"'}:
            quote = char
        elif quote != "'" and (command.startswith(("$(", "${"), cursor) or (char == "`" and not backtick)):
            _, cursor = _bash_substitution(command, cursor)
            continue
        elif quote is None and char == opening:
            depth += 1
        elif quote is None and char == closing:
            depth -= 1
            if depth == 0:
                body = command[body_start:cursor]
                return (None if parameter or arithmetic or body.startswith("(") else body), cursor + 1
        cursor += 1
    raise ValueError("閉じない置換")


def _bash_command_list(
    tokens: Sequence[_BashToken], start: int, closing: str | frozenset[str] | None = None
) -> tuple[list[BashInvocation], int]:
    """単純なコマンドと括弧・波括弧のグループへ接続を対応付ける。"""
    result: list[BashInvocation] = []
    pipeline_start = 0
    index = start
    while index < len(tokens):
        if _bash_closes(tokens[index], closing):
            return result, index + 1
        if tokens[index].operator and tokens[index].value in {";", "\n", "&&", "||"}:
            pipeline_start = len(result)
            index += 1
            continue
        index, coprocess = _bash_pipeline_prefixes(tokens, index)
        if index >= len(tokens) or _bash_closes(tokens[index], closing):
            raise ValueError("前置の後ろにコマンドがない")
        compound = tokens[index].reserved and tokens[index].value in {"for", "select", "while", "until", "if", "case"}
        function_body = _bash_function_body(tokens, index)
        function = function_body is not None
        grouped = compound or function or tokens[index].operator and tokens[index].value in {"(", "{"}
        if compound:
            current, index = _bash_compound(tokens, index)
            words: list[_BashToken] = []
        elif function:
            assert function_body is not None
            _, index = _bash_command_list(tokens, function_body + 1, ")" if tokens[function_body].value == "(" else "}")
            current, words = [], []
        elif grouped:
            inner_closing = ")" if tokens[index].value == "(" else "}"
            current, index = _bash_command_list(tokens, index + 1, inner_closing)
            words = []
        else:
            current = []
            words = []
        redirects: list[tuple[str, _BashToken]] = []
        while index < len(tokens):
            token = tokens[index]
            if token.operator:
                if _BASH_REDIRECTION.fullmatch(token.value):
                    if index + 1 >= len(tokens) or tokens[index + 1].operator:
                        raise ValueError("リダイレクト先がない")
                    redirects.append((token.value, tokens[index + 1]))
                    index += 2
                    continue
                break
            if grouped:
                raise ValueError("グループの後ろの未対応の語")
            words.append(token)
            index += 1
        separator = tokens[index].value if index < len(tokens) else ""
        if separator in {"(", "{", ")", "}"} and (index >= len(tokens) or not _bash_closes(tokens[index], closing)):
            raise ValueError("未対応のグループ境界")
        if not grouped:
            current = _bash_word_invocations(words)
        outputs = _bash_output_targets(redirects, separator, coprocess=coprocess)
        static_outputs = _bash_output_targets(
            [(operator, dataclasses.replace(word, value=word.static_value, known=True)) for operator, word in redirects],
            separator,
            coprocess=coprocess,
        )
        current = [_inherit_bash_outputs(item, outputs, static_outputs) for item in current]
        if coprocess:
            current = [dataclasses.replace(item, background=True) for item in current]
        result.extend(current)
        if separator == "&":
            result[pipeline_start:] = [dataclasses.replace(item, background=True) for item in result[pipeline_start:]]
        if separator in {";", "\n", "&&", "||", "&"}:
            pipeline_start = len(result)
        if index < len(tokens) and _bash_closes(tokens[index], closing):
            return result, index + 1
        if not separator:
            break
        index += 1
    if closing is not None:
        raise ValueError("閉じないグループ")
    return result, index


def _bash_pipeline_prefixes(tokens: Sequence[_BashToken], start: int) -> tuple[int, bool]:
    """文種の分岐より前に予約語を消費し、coprocの名前を実行位置から除く。"""
    index = start
    coprocess = False
    while index < len(tokens) and tokens[index].reserved and tokens[index].value in {"time", "!", "coproc"}:
        kind = tokens[index].value
        index += 1
        if kind == "time" and index < len(tokens) and tokens[index].reserved and tokens[index].value == "-p":
            index += 1
        if kind == "time" and index < len(tokens) and tokens[index].reserved and tokens[index].value == "--":
            index += 1
        if kind == "coproc":
            coprocess = True
            # 単純コマンドの先頭語は名前ではない。名前を置ける複合文だけで1語を消費する。
            if index + 1 < len(tokens) and not tokens[index].operator and _bash_prefixed_compound(tokens, index + 1):
                index += 1
    return index, coprocess


def _bash_prefixed_compound(tokens: Sequence[_BashToken], start: int) -> bool:
    """名前付きcoprocの後ろが、予約語前置を含む複合コマンドかを判定する。"""
    index = start
    while index < len(tokens) and tokens[index].reserved and tokens[index].value in {"time", "!"}:
        kind = tokens[index].value
        index += 1
        if kind == "time" and index < len(tokens) and tokens[index].reserved and tokens[index].value == "-p":
            index += 1
        if kind == "time" and index < len(tokens) and tokens[index].reserved and tokens[index].value == "--":
            index += 1
    return index < len(tokens) and (
        tokens[index].operator
        and tokens[index].value in {"(", "{"}
        or tokens[index].reserved
        and tokens[index].value in {"for", "select", "while", "until", "if", "case"}
        or _bash_function_body(tokens, index) is not None
    )


def _bash_closes(token: _BashToken, closing: str | frozenset[str] | None) -> bool:
    """引用された引数を節の終端へ変えず、演算子と予約語の境界だけを判定する。"""
    values = frozenset({closing}) if isinstance(closing, str) else closing or frozenset()
    return (token.operator or token.reserved) and token.value in values


def _bash_function_body(tokens: Sequence[_BashToken], start: int) -> int | None:
    """未実行の関数定義を、本体の波括弧またはサブシェルまで識別する。"""
    keyword = tokens[start].reserved and tokens[start].value == "function"
    index = start + int(keyword)
    if index >= len(tokens) or tokens[index].operator:
        return None
    index += 1
    parentheses = index + 1 < len(tokens) and [token.value for token in tokens[index : index + 2]] == ["(", ")"]
    if not keyword and not parentheses:
        return None
    if parentheses:
        index += 2
    while index < len(tokens) and tokens[index].value == "\n":
        index += 1
    return index if index < len(tokens) and tokens[index].operator and tokens[index].value in {"{", "("} else None


def _bash_compound(tokens: Sequence[_BashToken], start: int) -> tuple[list[BashInvocation], int]:
    """複合文の条件と本体の実行位置を再帰解析し、文全体の接続は呼出側へ戻す。"""
    kind = tokens[start].value
    index = start + 1
    result: list[BashInvocation] = []
    if kind in {"for", "select", "case"}:
        # 名前・反復値・caseの比較する値はコマンドではないが、能動的な置換は実行される。
        boundary = "in" if kind == "case" else "do"
        while index < len(tokens) and not _bash_closes(tokens[index], boundary):
            for body in tokens[index].nested:
                result.extend(dataclasses.replace(item, captured=True) for item in extract_bash_invocations(body))
            index += 1
        if index == len(tokens):
            raise ValueError("閉じない複合文の前置部")
        index += 1
    if kind == "case":
        while index < len(tokens):
            while index < len(tokens) and tokens[index].value in {";", "\n", ";;", ";&", ";;&"}:
                index += 1
            if index < len(tokens) and _bash_closes(tokens[index], "esac"):
                return result, index + 1
            while index < len(tokens) and not (tokens[index].operator and tokens[index].value == ")"):
                index += 1
            if index == len(tokens):
                break
            branch, index = _bash_command_list(tokens, index + 1, frozenset({";;", ";&", ";;&", "esac"}))
            result.extend(branch)
            if tokens[index - 1].value == "esac":
                return result, index
        raise ValueError("閉じないcase文")
    if kind == "if":
        while True:
            condition, index = _bash_command_list(tokens, index, "then")
            result.extend(condition)
            branch, index = _bash_command_list(tokens, index, frozenset({"elif", "else", "fi"}))
            result.extend(branch)
            ending = tokens[index - 1].value
            if ending == "elif":
                continue
            if ending == "else":
                branch, index = _bash_command_list(tokens, index, "fi")
                result.extend(branch)
            return result, index
    if kind in {"while", "until"}:
        condition, index = _bash_command_list(tokens, index, "do")
        result.extend(condition)
    body, index = _bash_command_list(tokens, index, "done")
    return [*result, *body], index


def _bash_word_invocations(words: Sequence[_BashToken]) -> list[BashInvocation]:
    """実行前置語を解決し、引用されたシェル本文と能動的な置換を解析する。"""
    result: list[BashInvocation] = []
    values = [word.value for word in words]
    prefix = 0
    while prefix < len(words) and words[prefix].assignment:
        prefix += 1
    # 引用された名前や`=`はシェルの前置代入にならず、その語自身が実行位置になる。
    quoted_assignment = prefix < len(words) and ENV_ASSIGN_PATTERN.match(values[prefix]) is not None
    segment = ExecutionSegment(tuple(values[prefix:]), True) if quoted_assignment else resolve_execution_segment(values)
    known = not any(_UNKNOWN_BASH_WORD in token for token in segment.tokens)
    if segment.resolved and segment.tokens and _UNKNOWN_BASH_WORD not in segment.tokens[0]:
        shell = shell_c_argument(segment.tokens)
        if shell is not None and known:
            result.extend(extract_bash_invocations(shell))
        else:
            result.append(
                BashInvocation(segment, known, static_tokens=tuple(word.static_value for word in words[-len(segment.tokens) :]))
            )
    for word in words:
        for body in word.nested:
            result.extend(dataclasses.replace(item, captured=True) for item in extract_bash_invocations(body))
    return result


def _bash_output_targets(
    redirects: Sequence[tuple[str, _BashToken]], separator: str, *, coprocess: bool = False
) -> tuple[_OutputTarget, _OutputTarget]:
    """リダイレクトを左から適用し、後段へ実際に渡る出力を求める。"""
    outputs: dict[int, _OutputTarget] = {1: _BashOutput.PIPE if coprocess or separator in PIPE_SEPARATORS else 1, 2: 2}
    for raw, operand in redirects:
        operator = raw.lstrip("0123456789")
        fd = int(raw[: len(raw) - len(operator)] or ("0" if operator.startswith("<") else "1"))
        if operator.startswith("<") or fd not in {1, 2}:
            continue
        if not operand.known:
            target: _OutputTarget = _BashOutput.UNKNOWN
        elif operator == ">&":
            target = outputs.get(int(operand.value), _BashOutput.UNKNOWN) if operand.value.isdecimal() else _BashOutput.UNKNOWN
        else:
            target = operand.value
        outputs[fd] = target
        if operator.startswith("&>"):
            outputs[2] = target
    if separator == "|&":
        outputs[2] = outputs[1]
    return outputs[1], outputs[2]


def _inherit_bash_outputs(
    invocation: BashInvocation,
    outputs: tuple[_OutputTarget, _OutputTarget],
    static_outputs: tuple[_OutputTarget, _OutputTarget],
) -> BashInvocation:
    """グループの出力先を、内側で上書きされていない接続へ渡す。"""
    inherited = tuple(
        outputs[value - 1] if isinstance(value, int) and not (invocation.captured and value == 1) else value
        for value in invocation.outputs
    )
    static_inherited = tuple(
        static_outputs[value - 1] if isinstance(value, int) and not (invocation.captured and value == 1) else value
        for value in invocation.static_outputs
    )
    return dataclasses.replace(
        invocation, outputs=(inherited[0], inherited[1]), static_outputs=(static_inherited[0], static_inherited[1])
    )


def _active_substitutions(body: str) -> list[str]:
    r"""展開されるheredoc本文から、エスケープされていないバッククォートと`$(`の置換を返す。

    展開される本文では引用符は特別な意味を持たず、`\\`だけが次の1文字をリテラルにする。
    `$((`は算術展開として扱い、コマンド置換に数えない。閉じない置換はその行の末尾までを返す。
    """
    found: list[str] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            index += 2
            continue
        if char == "`" or (body.startswith("$(", index) and not body.startswith("$((", index)):
            try:
                _, end = _bash_substitution(body, index)
            except ValueError:
                line_end = body.find("\n", index)
                end = len(body) if line_end < 0 else line_end
            found.append(body[index:end])
            index = end
            continue
        index += 1
    return found


def heredoc_command_substitutions(command: str) -> list[str]:
    """bashが本文を展開するheredocの本文にある、能動的なコマンド置換を出現順に返す。

    外側のコマンドに加え、`sh -c`・`bash -c`の引数とコマンド置換の本文の中にあるheredocも対象にする。
    引用付きの区切り語の本文と、`$VAR`・`${VAR}`・`$((...))`・エスケープ済みの表記は対象外とする。
    """
    found: list[str] = []
    for body in heredoc_bodies(command):
        if body.expands:
            found.extend(_active_substitutions(command[body.start : body.end]))
    try:
        tokens = _bash_tokens(mask_heredoc_bodies(command))
    except ValueError:
        tokens = []
    for nested in _nested_shell_texts(tokens):
        found.extend(heredoc_command_substitutions(nested))
    return list(dict.fromkeys(found))


def _nested_shell_texts(tokens: Sequence[_BashToken]) -> list[str]:
    """トークン列から、置換の本文と静的に確定した`sh -c`・`bash -c`の引数を返す。"""
    texts: list[str] = []
    words: list[_BashToken] = []
    for token in [*tokens, _BashToken(";", operator=True)]:
        if not token.operator:
            words.append(token)
            texts.extend(token.nested)
            continue
        segment = resolve_execution_segment([word.value for word in words])
        words = []
        if not segment.resolved or not segment.tokens:
            continue
        shell = shell_c_argument(segment.tokens)
        if shell is not None and _UNKNOWN_BASH_WORD not in shell:
            texts.append(shell)
    return texts
