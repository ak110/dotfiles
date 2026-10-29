"""公開案内の実行例を抽出し、CLI自身が組み立てるparserへ渡す検証用補助。"""

from __future__ import annotations

import argparse
import contextlib
import io
import pathlib
import re
import shlex
from collections.abc import Iterator

import pytest

from agent_toolkit import atk
from agent_toolkit._atk import run_script


class _ParserCaptured(Exception):
    """実スクリプトの解析開始を副作用の実行前で止める。"""

    def __init__(self, parser: argparse.ArgumentParser) -> None:
        super().__init__(parser.prog)
        self.parser = parser


def registered_parser(script_name: str) -> argparse.ArgumentParser:
    """run-script登録先の起動経路から実parserを得る。"""

    def capture(self: argparse.ArgumentParser, *args: object, **kwargs: object) -> argparse.Namespace:
        del args, kwargs
        raise _ParserCaptured(self)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(argparse.ArgumentParser, "parse_args", capture)
        try:
            run_script.dispatch(argparse.Namespace(script_name=script_name, script_args=[]))
        except _ParserCaptured as captured:
            return captured.parser
    raise AssertionError(f"登録スクリプトがparserを構築しなかった: {script_name}")


def examples(text: str) -> Iterator[tuple[int, str]]:
    """行頭の完全な実行例を抽出する。インラインの説明用断片は対象に含めない。"""
    pending = ""
    start = 0
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if pending:
            pending += " " + line.removesuffix("\\").strip()
        elif line.startswith("atk "):
            start = number
            pending = line.removesuffix("\\").strip()
        else:
            continue
        if line.endswith("\\"):
            continue
        yield start, pending
        pending = ""
    assert not pending, f"未完の継続行: {start}"


def example_argv(command: str) -> list[str]:
    """例のプレースホルダーとシェルの値展開だけを固定値へ置き換える。引数名と区切りは保つ。"""
    command = re.sub(r"\$\([^()]*\)", "/absolute/example", command)
    command = re.sub(r"<(?:round|ラウンド番号|正の有限秒数|秒数|N)>", "1", command)
    command = command.replace("<40桁のSHA>", "a" * 40)
    command = re.sub(r"<[^<>]+>", "example", command)
    # シェルのリダイレクトとパイプの先はatkへの入力ではない。
    tokens = shlex.split(command, comments=True)
    for index, token in enumerate(tokens):
        if token in {"|", ">", ">>", "2>", "&&", ";"}:
            tokens = tokens[:index]
            break
    return tokens


def validate_example(command: str, *, source: str, parsers: dict[str, argparse.ArgumentParser] | None = None) -> None:
    """案内に書かれたargvを実parserで検査し、所在付きで構文差を報告する。"""
    argv = example_argv(command)
    assert argv and argv[0] == "atk", (source, command)
    diagnostic = io.StringIO()
    with contextlib.redirect_stderr(diagnostic), contextlib.redirect_stdout(io.StringIO()):
        try:
            root = parsers.get("atk") if parsers is not None else None
            if root is None:
                root = atk._build_parser()  # pylint: disable=protected-access
                if parsers is not None:
                    parsers["atk"] = root
            args = root.parse_args(argv[1:])
            if args.command == "run-script":
                script = args.script_name
                parser = parsers.get(script) if parsers is not None else None
                if parser is None:
                    parser = registered_parser(script)
                    if parsers is not None:
                        parsers[script] = parser
                script_args = args.script_args
                if script_args[:1] == ["--"]:
                    script_args = script_args[1:]
                parser.parse_args(script_args)
        except SystemExit as error:
            assert error.code == 0, f"{source}: {command}\n{diagnostic.getvalue()}"


def validate_document(path: pathlib.Path, *, parsers: dict[str, argparse.ArgumentParser] | None = None) -> int:
    """文書の実物から得た実行例を全件検査する。"""
    count = 0
    for line, command in examples(path.read_text(encoding="utf-8")):
        validate_example(command, source=f"{path}:{line}", parsers=parsers)
        count += 1
    return count
